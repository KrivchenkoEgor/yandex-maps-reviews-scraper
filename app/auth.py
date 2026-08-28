"""
auth — регистрация и вход пользователей Yandex Reviews Scraper.

Реестр аккаунтов хранится в db/users.db (SQLite). Данные скрейпа каждого
пользователя — в отдельной БД db/users/uid_<id>/ya_ot.db (та же схема, что
и раньше), поэтому таблицы отзывов не меняются и код database.py
переиспользуется как есть.

Пароли: PBKDF2-HMAC-SHA256 (стандартная библиотека, без новых зависимостей).
Подтверждение email: 6-значный код, живёт CONFIRM_CODE_TTL_HOURS.
Код хранится открыто — это локный личный инструмент; для публичного деплоя
хранить хэш и доставлять настоящей почтой (mailer.py, SMTP-бэкенд).
"""

import asyncio
import hashlib
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from app import config
from app.mailer import send_confirmation_code

USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    is_verified INTEGER NOT NULL DEFAULT 0,
    confirm_code TEXT,
    code_expires TEXT,
    api_token TEXT,
    created_at TEXT NOT NULL
);
"""

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CONFIRM_CODE_TTL_HOURS = 24
MAX_LOGIN_FAILS = 5
LOGIN_LOCKOUT_SEC = 60

# Простая защита от перебора паролей (в пределах процесса)
_login_fails: dict[str, list[float]] = {}


class AuthError(Exception):
    """Ожидаемая ошибка регистрации/входа — текст показываем пользователю."""


def registry_path() -> Path:
    return Path(config.USER_DATA_DIR) / "users.db"


def init_registry() -> Path:
    """Создать реестр пользователей (если нет). Возвращает путь."""
    p = registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    try:
        con.execute("PRAGMA journal_mode=WAL;")
        con.executescript(USERS_SCHEMA)
        con.commit()
    finally:
        con.close()
    return p


def _norm_email(email: str) -> str:
    return (email or "").strip().lower()


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()


def user_db_path(user_id: int) -> Path:
    """Персональная БД пользователя (shops/reviews/monitoring — схема прежняя)."""
    return Path(config.USER_DATA_DIR) / f"uid_{user_id}" / "ya_ot.db"


def get_user(user_id: int) -> Optional[dict[str, Any]]:
    init_registry()
    con = sqlite3.connect(registry_path())
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def _get_by_email(email: str) -> Optional[dict[str, Any]]:
    con = sqlite3.connect(registry_path())
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT * FROM users WHERE email=?", (_norm_email(email),)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def register_user(email: str, password: str) -> dict[str, Any]:
    """
    Зарегистрировать пользователя и отправить код подтверждения.

    Возвращает {email, user_id}. Бросает AuthError с понятным текстом.
    """
    init_registry()
    email = _norm_email(email)
    if not EMAIL_RE.match(email):
        raise AuthError("Введите корректный email (например, ivan@example.com)")
    if len(password or "") < 6:
        raise AuthError("Пароль должен быть не короче 6 символов")
    if _get_by_email(email):
        raise AuthError("Пользователь с таким email уже зарегистрирован")

    salt = secrets.token_hex(16)
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires = (datetime.now() + timedelta(hours=CONFIRM_CODE_TTL_HOURS)).isoformat()
    con = sqlite3.connect(registry_path())
    try:
        cur = con.execute(
            "INSERT INTO users(email,password_hash,salt,is_verified,confirm_code,code_expires,api_token,created_at)"
            " VALUES(?,?,?,0,?,?,?,?)",
            (email, _hash_password(password, salt), salt, code, expires,
             secrets.token_hex(16), datetime.now().isoformat()),
        )
        con.commit()
        user_id = cur.lastrowid
    finally:
        con.close()
    send_confirmation_code(email, code)
    logger.info(f"Регистрация: {email} (id={user_id}), код подтверждения отправлен")
    return {"email": email, "user_id": user_id}


def resend_code(email: str) -> None:
    """Выслать новый код подтверждения (старый перестаёт действовать)."""
    user = _get_by_email(email)
    if not user:
        raise AuthError("Пользователь с таким email не найден")
    if user["is_verified"]:
        raise AuthError("Email уже подтверждён — просто войдите")
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires = (datetime.now() + timedelta(hours=CONFIRM_CODE_TTL_HOURS)).isoformat()
    con = sqlite3.connect(registry_path())
    try:
        con.execute("UPDATE users SET confirm_code=?, code_expires=? WHERE id=?",
                    (code, expires, user["id"]))
        con.commit()
    finally:
        con.close()
    send_confirmation_code(_norm_email(email), code)
    logger.info(f"Повторный код подтверждения: {email}")


def confirm_email(email: str, code: str) -> None:
    """Подтвердить email кодом из письма."""
    user = _get_by_email(email)
    if not user:
        raise AuthError("Пользователь с таким email не найден")
    if user["is_verified"]:
        return  # уже подтверждён — идемпотентно
    if not user["confirm_code"] or str(code).strip() != user["confirm_code"]:
        raise AuthError("Неверный код — проверьте письмо")
    try:
        if datetime.fromisoformat(user["code_expires"]) < datetime.now():
            raise AuthError("Код истёк — запросите новый (кнопка «Выслать код ещё раз»)")
    except (TypeError, ValueError):
        raise AuthError("Код истёк — запросите новый")
    con = sqlite3.connect(registry_path())
    try:
        con.execute("UPDATE users SET is_verified=1, confirm_code=NULL, code_expires=NULL WHERE id=?",
                    (user["id"],))
        con.commit()
    finally:
        con.close()
    logger.info(f"Email подтверждён: {user['email']}")


def get_user_by_token(token: str) -> Optional[dict[str, Any]]:
    """Найти подтверждённого пользователя по API-токену (для /api/scrape)."""
    if not token:
        return None
    init_registry()
    con = sqlite3.connect(registry_path())
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT * FROM users WHERE api_token=? AND is_verified=1", (str(token),)
        ).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def login_user(email: str, password: str) -> dict[str, Any]:
    """
    Войти по email и паролю. Возвращает данные пользователя (без хэшей).

    После 5 неудачных попыток — пауза 60 секунд.
    """
    init_registry()
    email = _norm_email(email)
    now = time.time()
    fails = [t for t in _login_fails.get(email, []) if now - t < LOGIN_LOCKOUT_SEC]
    if len(fails) >= MAX_LOGIN_FAILS:
        raise AuthError("Слишком много неудачных попыток — подождите минуту")
    user = _get_by_email(email)
    ok = False
    if user:
        ok = secrets.compare_digest(_hash_password(password or "", user["salt"]), user["password_hash"])
    if not user or not ok:
        _login_fails.setdefault(email, []).append(now)
        raise AuthError("Неверный email или пароль")
    if not user["is_verified"]:
        raise AuthError("Email не подтверждён — введите код из письма на вкладке «Аккаунт»")
    _login_fails.pop(email, None)
    return {"user_id": user["id"], "email": user["email"], "api_token": user["api_token"]}


# ---------------------------------------------------------------------------
# Самотест
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    async def _demo():
        # Временный реестр и личная БД — ничего в проде не трогаем
        config.USER_DATA_DIR = "tmp/test_users"
        Path("tmp/test_users").mkdir(parents=True, exist_ok=True)
        registry_path().unlink(missing_ok=True)

        # 1. Регистрация с фейковым email
        u = register_user("fake.user@example.com", "secret123")
        assert u["user_id"] > 0
        # 2. Код можно прочитать из outbox (console-почта)
        outbox = Path("logs/outbox/fake.user@example.com.txt")
        assert outbox.exists(), "код не попал в outbox"
        code = outbox.read_text().split("Код:")[1].split()[0].strip()
        # 3. Вход до подтверждения запрещён
        try:
            login_user("fake.user@example.com", "secret123")
            raise SystemExit("ошибка: впустили неподтверждённого")
        except AuthError:
            pass
        # 4. Неверный код не подтверждает
        try:
            confirm_email("fake.user@example.com", "000000")
            raise SystemExit("ошибка: неверный код принят")
        except AuthError:
            pass
        # 5. Верный код подтверждает, вход работает
        confirm_email("fake.user@example.com", code)
        me = login_user("fake.user@example.com", "secret123")
        assert me["email"] == "fake.user@example.com"
        # 6. Неверный пароль не пускает
        try:
            login_user("fake.user@example.com", "wrong")
            raise SystemExit("ошибка: пустим с неверным паролем")
        except AuthError:
            pass
        # 7. Повторная регистрация того же email запрещена
        try:
            register_user("FAKE.USER@example.com", "secret123")
            raise SystemExit("ошибка: дубль email принят")
        except AuthError:
            pass
        # 8. Личная БД изолирована и работает по обычной схеме
        from app.database import Database
        db1 = Database(user_db_path(me["user_id"]))
        await db1.init()
        await db1.upsert_shop({"oid": "1", "name": "Магазин А", "address": "", "rating": 4.0,
                               "total_reviews": 1, "url": "https://example.com"})
        assert await db1.count_reviews("1") == 0  # отзывов нет, но магазин виден
        db2 = Database(user_db_path(user_id=999999))
        await db2.init()
        shops2 = await db2.get_shop("1")
        assert shops2 is None, "ошибка: чужие данные видны!"
        print("✅ Все проверки auth OK (регистрация, код, вход, изоляция)")

        # уборка
        import shutil
        shutil.rmtree("tmp/test_users", ignore_errors=True)
        for f in Path("logs/outbox").glob("fake.user@example.com.txt"):
            f.unlink()

    asyncio.run(_demo())
