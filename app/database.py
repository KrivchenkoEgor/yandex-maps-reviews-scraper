"""
database — SQLite кэш для ya_ot (shops, reviews, monitoring, scrape_log).

Схема из AGENTS.md + логика TTL 24ч:
- перед скачиванием проверить last_scraped
- мониторинг: добавлять только новые review_id
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import aiosqlite
from loguru import logger

# Путь по умолчанию (переопределяется через .env DATABASE_PATH)
DEFAULT_DB = "db/ya_ot.db"

SCHEMA = """
-- Магазины
CREATE TABLE IF NOT EXISTS shops (
    oid TEXT PRIMARY KEY,
    name TEXT,
    address TEXT,
    rating REAL,
    total_reviews INTEGER,
    last_scraped TEXT,
    url TEXT
);

-- Отзывы
CREATE TABLE IF NOT EXISTS reviews (
    review_id TEXT PRIMARY KEY,
    shop_oid TEXT NOT NULL,
    author TEXT,
    rating INTEGER,
    date TEXT,
    text TEXT,
    photos TEXT,              -- JSON-массив
    owner_response TEXT,       -- JSON {"text","date"}
    likes INTEGER DEFAULT 0,
    is_verified INTEGER DEFAULT 0,
    scraped_at TEXT,
    FOREIGN KEY(shop_oid) REFERENCES shops(oid) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_reviews_shop ON reviews(shop_oid);
CREATE INDEX IF NOT EXISTS idx_reviews_date ON reviews(date);

-- Мониторинг подписок
CREATE TABLE IF NOT EXISTS monitoring (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_oid TEXT NOT NULL,
    interval_hours INTEGER NOT NULL,
    last_check TEXT,
    active INTEGER DEFAULT 1,
    FOREIGN KEY(shop_oid) REFERENCES shops(oid) ON DELETE CASCADE
);

-- Лог скрейпов
CREATE TABLE IF NOT EXISTS scrape_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_oid TEXT,
    started_at TEXT,
    finished_at TEXT,
    reviews_found INTEGER,
    status TEXT,
    error TEXT
);
"""

# ---------------------------------------------------------------------------
# Синхронные хелперы (для скриптов и тестов)
# ---------------------------------------------------------------------------

def _ensure_db(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA busy_timeout=5000;")
        con.executescript(SCHEMA)
        con.commit()
    finally:
        con.close()


def init_db(path: Optional[str] = None) -> str:
    """Инициализировать БД (создать таблицы если нет). Возвращает путь."""
    p = path or os.getenv("DATABASE_PATH", DEFAULT_DB)
    _ensure_db(p)
    logger.info(f"БД инициализирована: {p}")
    return p


# ---------------------------------------------------------------------------
# Асинхронный доступ (основной для FastAPI / Gradio)
# ---------------------------------------------------------------------------

class Database:
    """Тонкая обёртка над aiosqlite с методами кэша."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or os.getenv("DATABASE_PATH", DEFAULT_DB)

    async def init(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            await db.executescript(SCHEMA)
            await db.commit()

    # -- shops --

    async def upsert_shop(self, shop: dict[str, Any]) -> None:
        """Вставить/обновить магазин. Ожидает ключи: oid, name, address, rating, total_reviews, url."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO shops(oid,name,address,rating,total_reviews,last_scraped,url)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(oid) DO UPDATE SET
                     name=excluded.name, address=excluded.address,
                     rating=excluded.rating, total_reviews=excluded.total_reviews,
                     last_scraped=excluded.last_scraped, url=excluded.url""",
                (
                    shop["oid"],
                    shop.get("name"),
                    shop.get("address"),
                    shop.get("rating"),
                    shop.get("total_reviews"),
                    datetime.now().isoformat(),
                    shop.get("url"),
                ),
            )
            await db.commit()

    async def get_shop(self, oid: str) -> Optional[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM shops WHERE oid=?", (oid,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def is_cache_fresh(self, oid: str, ttl_hours: int = 24) -> bool:
        """Свежий ли кэш (< ttl_hours)?"""
        shop = await self.get_shop(oid)
        if not shop or not shop.get("last_scraped"):
            return False
        try:
            last = datetime.fromisoformat(shop["last_scraped"])
            return datetime.now() - last < timedelta(hours=ttl_hours)
        except Exception:
            return False

    # -- reviews --

    async def upsert_reviews(self, shop_oid: str, reviews: list[dict]) -> int:
        """Вставить отзывы (игнорирует дубликаты по review_id). Возвращает кол-во вставленных."""
        if not reviews:
            return 0
        now = datetime.now().isoformat()
        rows = []
        for r in reviews:
            rows.append((
                r.get("review_id"),
                shop_oid,
                r.get("author"),
                r.get("rating"),
                r.get("date"),
                r.get("text"),
                json.dumps(r.get("photos", []), ensure_ascii=False),
                json.dumps(r.get("owner_response"), ensure_ascii=False) if r.get("owner_response") else None,
                r.get("likes", 0),
                1 if r.get("is_verified") else 0,
                now,
            ))
        async with aiosqlite.connect(self.path) as db:
            inserted = 0
            for row in rows:
                try:
                    cursor = await db.execute(
                        """INSERT OR IGNORE INTO reviews
                           (review_id,shop_oid,author,rating,date,text,photos,owner_response,likes,is_verified,scraped_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?)""", row)
                    if cursor.rowcount > 0:
                        inserted += 1
                except Exception as e:
                    logger.warning(f"Не удалось вставить отзыв {row[0]}: {e}")
            await db.commit()
            return inserted

    async def get_reviews(self, shop_oid: str) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM reviews WHERE shop_oid=? ORDER BY date DESC", (shop_oid,)) as cur:
                rows = await cur.fetchall()
                out = []
                for r in rows:
                    d = dict(r)
                    # Распаковать JSON
                    try:
                        d["photos"] = json.loads(d["photos"]) if d["photos"] else []
                    except Exception:
                        d["photos"] = []
                    try:
                        d["owner_response"] = json.loads(d["owner_response"]) if d["owner_response"] else None
                    except Exception:
                        d["owner_response"] = None
                    d["is_verified"] = bool(d["is_verified"])
                    out.append(d)
                return out

    async def count_reviews(self, shop_oid: str) -> int:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute("SELECT COUNT(*) FROM reviews WHERE shop_oid=?", (shop_oid,)) as cur:
                row = await cur.fetchone()
                return row[0] if row else 0

    # -- monitoring --

    async def add_monitor(self, shop_oid: str, interval_hours: int = 24) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "INSERT INTO monitoring(shop_oid,interval_hours,last_check,active) VALUES(?,?,?,1)",
                (shop_oid, interval_hours, datetime.now().isoformat()),
            )
            await db.commit()
            return cur.lastrowid  # type: ignore

    async def list_monitors(self, active_only: bool = True) -> list[dict]:
        q = "SELECT * FROM monitoring WHERE active=1" if active_only else "SELECT * FROM monitoring"
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(q) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def deactivate_monitor(self, monitor_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE monitoring SET active=0 WHERE id=?", (monitor_id,))
            await db.commit()

    # -- scrape_log --

    async def log_start(self, shop_oid: str) -> int:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA busy_timeout=5000;")
            cur = await db.execute(
                "INSERT INTO scrape_log(shop_oid,started_at,status) VALUES(?,?,?)",
                (shop_oid, datetime.now().isoformat(), "running"),
            )
            await db.commit()
            return cur.lastrowid  # type: ignore

    async def log_finish(self, log_id: int, reviews_found: int, status: str = "ok", error: Optional[str] = None) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA busy_timeout=5000;")
            await db.execute(
                "UPDATE scrape_log SET finished_at=?, reviews_found=?, status=?, error=? WHERE id=?",
                (datetime.now().isoformat(), reviews_found, status, error, log_id),
            )
            await db.commit()

    async def touch_monitor(self, monitor_id: int) -> None:
        """Обновить last_check мониторинга (вынесено из monitor.py)."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA busy_timeout=5000;")
            await db.execute("UPDATE monitoring SET last_check=? WHERE id=?", (datetime.now().isoformat(), monitor_id))
            await db.commit()


# Удобный синглтон для импорта
_db: Optional[Database] = None

def get_db(path: Optional[str] = None) -> Database:
    global _db
    if _db is None or (path and path != _db.path):
        _db = Database(path)
    return _db


if __name__ == "__main__":
    import asyncio

    async def _demo():
        db = Database("db/test_demo.db")
        await db.init()
        await db.upsert_shop({"oid": "999", "name": "Тест", "address": "ул. Тестовая 1", "rating": 4.5, "total_reviews": 10, "url": "https://yandex.ru/maps/1"})
        assert await db.is_cache_fresh("999", ttl_hours=24) is True
        await db.upsert_reviews("999", [
            {"review_id": "r1", "author": "Иван", "rating": 5, "date": "2024-03-15", "text": "Отлично", "photos": [], "owner_response": None, "likes": 2, "is_verified": True},
            {"review_id": "r1", "author": "Иван", "rating": 5, "date": "2024-03-15", "text": "Дубль", "photos": [], "owner_response": None, "likes": 0, "is_verified": False},
        ])
        assert await db.count_reviews("999") == 1
        print("✅ database demo OK")
        Path("db/test_demo.db").unlink(missing_ok=True)

    asyncio.run(_demo())
