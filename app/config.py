"""
config — чтение .env для Yandex Reviews Scraper.

Все секреты только через os.getenv / python-dotenv (см. AGENTS.md §2).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Загружаем .env из корня проекта (рядом с app/)
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


def _bool(key: str, default: bool) -> bool:
    v = os.getenv(key, str(default)).lower()
    return v in ("1", "true", "yes", "on")


# --- Антибот ---
YANDEX_MIN_DELAY_SEC = _float("YANDEX_MIN_DELAY_SEC", 2.0)
YANDEX_MAX_DELAY_SEC = _float("YANDEX_MAX_DELAY_SEC", 5.0)
YANDEX_MAX_RETRIES = _int("YANDEX_MAX_RETRIES", 3)
YANDEX_RETRY_BASE_DELAY_SEC = _float("YANDEX_RETRY_BASE_DELAY_SEC", 2.0)
YANDEX_PAGE_TIMEOUT_SEC = _int("YANDEX_PAGE_TIMEOUT_SEC", 30)
YANDEX_SCROLL_PAUSE_SEC = _float("YANDEX_SCROLL_PAUSE_SEC", 3.0)
YANDEX_MAX_SCROLL_ATTEMPTS = _int("YANDEX_MAX_SCROLL_ATTEMPTS", 5)

# --- БД и кэш ---
DATABASE_PATH = os.getenv("DATABASE_PATH", "db/ya_ot.db")
SHOP_CACHE_TTL_HOURS = _int("SHOP_CACHE_TTL_HOURS", 24)

# --- Многопользовательский режим ---
# db/users/users.db — реестр аккаунтов; db/users/uid_<id>/ya_ot.db — БД пользователя
USER_DATA_DIR = os.getenv("USER_DATA_DIR", "db/users")
MAIL_BACKEND = os.getenv("MAIL_BACKEND", "console")  # console | smtp
OUTBOX_DIR = os.getenv("OUTBOX_DIR", "logs/outbox")
CONFIRM_CODE_TTL_HOURS = _int("CONFIRM_CODE_TTL_HOURS", 24)
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = _int("SMTP_PORT", 587)
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM = os.getenv("SMTP_FROM", "yandex-reviews-scraper@localhost")
SMTP_STARTTLS = _bool("SMTP_STARTTLS", True)

# --- Приложение ---
APP_HOST = os.getenv("APP_HOST", "127.0.0.1")
APP_PORT = _int("APP_PORT", 8000)
APP_DEBUG = _bool("APP_DEBUG", False)

# --- Логи ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = os.getenv("LOG_FILE", "logs/ya_ot.log")
LOG_ROTATION = os.getenv("LOG_ROTATION", "10 MB")
LOG_RETENTION = os.getenv("LOG_RETENTION", "30 days")

# --- Экспорт ---
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "output")

# --- Мониторинг ---
MONITOR_DEFAULT_INTERVAL_HOURS = _int("MONITOR_DEFAULT_INTERVAL_HOURS", 24)
MONITOR_CHECK_INTERVAL_MINUTES = _int("MONITOR_CHECK_INTERVAL_MINUTES", 60)

# --- Ограничения ---
MAX_REVIEWS_PER_SHOP = _int("MAX_REVIEWS_PER_SHOP", 10000)
