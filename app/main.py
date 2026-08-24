"""
main — точка входа: FastAPI + Gradio (из AGENTS.md).

Запуск:  python -m app.main
          или  uvicorn app.main:app --host 127.0.0.1 --port 8000

Gradio монтируется на "/" , API FastAPI — на "/api/*" и "/health".
"""

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import gradio as gr
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from loguru import logger

from app import config
from app.database import get_db
from app.exporter import export_csv, export_excel, export_json
from app.ui import _scrape_one  # переиспользуем логику одиночного
from app.url_resolver import resolve_yandex_url

# ---------------------------------------------------------------------------
# Логирование
# ---------------------------------------------------------------------------
logger.remove()
logger.add(sys.stderr, level=config.LOG_LEVEL, format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}")
try:
    Path(config.LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        config.LOG_FILE,
        level=config.LOG_LEVEL,
        rotation=config.LOG_ROTATION,
        retention=config.LOG_RETENTION,
        encoding="utf-8",
    )
except Exception as e:
    logger.warning(f"Файловый лог не настроен: {e}")


# ---------------------------------------------------------------------------
# Lifespan: инициализация БД + фоновый мониторинг
# ---------------------------------------------------------------------------

_monitor_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    db = get_db()
    await db.init()
    logger.info(f"БД готова: {db.path}")

    # Фоновый мониторинг (как в monitor.py)
    global _monitor_task, _stop_event
    _stop_event = asyncio.Event()
    try:
        from app.monitor import monitor_loop
        _monitor_task = asyncio.create_task(monitor_loop(_stop_event))
        logger.info("Фоновый мониторинг запущен")
    except Exception as e:
        logger.warning(f"Мониторинг не запущен: {e}")

    yield

    # Shutdown
    if _stop_event:
        _stop_event.set()
    if _monitor_task:
        _monitor_task.cancel()
        try:
            await _monitor_task
        except asyncio.CancelledError:
            pass
    logger.info("Приложение остановлено")


app = FastAPI(title="Yandex Reviews Scraper", version="0.1.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/api/resolve")
async def api_resolve(url: str):
    """Разрешение ссылки и извлечение oid без скрейпа (для отладки)."""
    try:
        r = resolve_yandex_url(url)
        return r
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/scrape")
async def api_scrape(payload: dict):
    """
    POST {"url": "https://yandex.ru/maps/-/CTwsUYyk"}
    Возвращает shop + reviews, пишет в кэш и на диск (Excel/CSV/JSON).
    """
    url = (payload.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="Поле 'url' обязательно")
    try:
        shop, reviews = await _scrape_one(url)
        # Экспорты (опционально, но делаем для API тоже)
        p_xlsx = export_excel(shop, reviews)
        p_csv = export_csv(shop, reviews)
        p_json = export_json(shop, reviews)
        return {
            "shop": shop,
            "reviews": reviews,
            "files": {"xlsx": str(p_xlsx), "csv": str(p_csv), "json": str(p_json)},
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Ошибка /api/scrape")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Монтирование Gradio (UI из app.ui)
# ---------------------------------------------------------------------------

try:
    from app.ui import build_ui
    gradio_app = build_ui()
    # Gradio 4.x: монтируем через gr.mount_gradio_app
    app = gr.mount_gradio_app(app, gradio_app, path="/")
    logger.info("Gradio UI смонтирован на /")
except Exception as e:
    logger.warning(f"Gradio не смонтирован: {e}. API всё равно доступен на /api/* и /health")

# Для прямого запуска: python -m app.main
if __name__ == "__main__":
    import uvicorn
    logger.info(f"Запуск http://{config.APP_HOST}:{config.APP_PORT} (debug={config.APP_DEBUG})")
    uvicorn.run(
        "app.main:app",
        host=config.APP_HOST,
        port=config.APP_PORT,
        reload=config.APP_DEBUG,
        log_level=config.LOG_LEVEL.lower(),
    )
