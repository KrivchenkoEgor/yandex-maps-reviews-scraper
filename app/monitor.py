"""
monitor — фоновый мониторинг подписок на магазины.

Логика из AGENTS.md:
- таблица monitoring (shop_oid, interval_hours, last_check, active)
- интервал день/неделя (в часах)
- при проверке: скачиваем отзывы, сравниваем review_id с кэшем, добавляем только новые
- логика TTL не применяется — мониторинг всегда свежо скачивает
"""

import asyncio
from datetime import datetime
from typing import Any

from loguru import logger

from app import config
from app.database import Database, get_db
from app.yandex_scraper import YandexScraper


async def check_shop(shop_oid: str, shop_url: str, db: Database | None = None) -> dict[str, Any]:
    """
    Проверить один магазин на новые отзывы.

    Возвращает {new_reviews: int, total: int, shop: dict}
    """
    db = db or get_db()
    # Скачиваем свежие отзывы напрямую (без кэша)
    scraper = YandexScraper(headless=True)
    result = await scraper.scrape(shop_url)
    fresh: list[dict[str, Any]] = result["reviews"]
    shop = result["shop"]

    # Существующие review_id в БД
    existing = await db.get_reviews(shop_oid)
    existing_ids = {r["review_id"] for r in existing if r.get("review_id")}
    new = [r for r in fresh if r.get("review_id") not in existing_ids]

    if new:
        # Обновляем магазин и добавляем только новые
        await db.upsert_shop(shop)
        added = await db.upsert_reviews(shop_oid, new)
        logger.info(f"Мониторинг {shop_oid}: {added} новых из {len(fresh)}")
    else:
        logger.info(f"Мониторинг {shop_oid}: новых нет ({len(fresh)} всего)")

    # Обновляем last_check в monitoring
    # Находим запись мониторинга для этого shop_oid
    monitors = await db.list_monitors(active_only=False)
    for m in monitors:
        if m["shop_oid"] == shop_oid:
            # Обновляем last_check
            import aiosqlite
            async with aiosqlite.connect(db.path) as c:
                await c.execute("UPDATE monitoring SET last_check=? WHERE id=?", (datetime.now().isoformat(), m["id"]))
                await c.commit()
            break

    return {"new_reviews": len(new), "total": len(fresh), "shop": shop, "new_items": new}


async def check_all_monitors() -> list[dict[str, Any]]:
    """Проверить все активные подписки, у которых истёк интервал."""
    db = get_db()
    await db.init()
    monitors = await db.list_monitors(active_only=True)
    results: list[dict[str, Any]] = []
    for m in monitors:
        last = m.get("last_check")
        interval = m.get("interval_hours") or config.MONITOR_DEFAULT_INTERVAL_HOURS
        # Проверяем истёк ли интервал
        should = True
        if last:
            try:
                dt = datetime.fromisoformat(last)
                elapsed_h = (datetime.now() - dt).total_seconds() / 3600
                should = elapsed_h >= interval
            except Exception:
                should = True
        if not should:
            continue

        # Нужен URL магазина — берём из shops
        shop = await db.get_shop(m["shop_oid"])
        if not shop or not shop.get("url"):
            logger.warning(f"Мониторинг {m['id']}: нет URL для {m['shop_oid']}, пропускаем")
            continue

        # Уважение к серверу Яндекса — пауза между магазинами 2-5с из anti_bot
        from app.anti_bot import async_random_delay
        try:
            res = await check_shop(m["shop_oid"], shop["url"], db)
            res["monitor_id"] = m["id"]
            results.append(res)
        except Exception as e:
            logger.error(f"Мониторинг ошибка {m['shop_oid']}: {e}")
            results.append({"monitor_id": m["id"], "shop_oid": m["shop_oid"], "error": str(e)})
        await async_random_delay()

    return results


async def monitor_loop(stop_event: asyncio.Event | None = None) -> None:
    """
    Фоновый цикл: каждые MONITOR_CHECK_INTERVAL_MINUTES минут проверять подписки.
    Запускается из main.py lifespan.
    """
    interval_min = config.MONITOR_CHECK_INTERVAL_MINUTES
    logger.info(f"Мониторинг запущен, проверка каждые {interval_min} мин")
    while True:
        if stop_event and stop_event.is_set():
            break
        try:
            await check_all_monitors()
        except Exception as e:
            logger.error(f"Ошибка цикла мониторинга: {e}")
        # Ждём с возможностью досрочной остановки
        try:
            await asyncio.wait_for(
                (stop_event.wait() if stop_event else asyncio.sleep(interval_min * 60)),
                timeout=interval_min * 60,
            )
            if stop_event and stop_event.is_set():
                break
        except asyncio.TimeoutError:
            continue


if __name__ == "__main__":
    # Демо: показать список подписок
    async def _demo():
        db = get_db()
        await db.init()
        ms = await db.list_monitors(active_only=False)
        print(f"Подписок: {len(ms)}")
        for m in ms:
            print(m)

    asyncio.run(_demo())
