"""
mailer — доставка кода подтверждения email.

Два бэкенда (выбор через .env MAIL_BACKEND):
- console (по умолчанию, для локальной работы и тестов): код пишется в лог
  сервера и в файл logs/outbox/<email>.txt — «почтовый ящик» на диске;
- smtp: настоящая отправка письмом через smtplib (стандартная библиотека).
  Включается конфигом SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS/SMTP_FROM.

Никаких новых зависимостей — только stdlib (см. AGENTS.md §2 про секреты:
пароль SMTP берётся из переменных окружения, не хардкодится).
"""

import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

from loguru import logger

from app import config


def send_confirmation_code(email: str, code: str) -> None:
    """Отправить код подтверждения выбранным бэкендом."""
    if config.MAIL_BACKEND == "smtp" and config.SMTP_HOST:
        _send_via_smtp(email, code)
    else:
        _write_outbox(email, code)


def _write_outbox(email: str, code: str) -> None:
    """Console-бэкенд: код в лог и в файл logs/outbox/<email>.txt."""
    outbox_dir = Path(config.OUTBOX_DIR)
    outbox_dir.mkdir(parents=True, exist_ok=True)
    path = outbox_dir / f"{email}.txt"
    body = (
        f"Кому: {email}\n"
        f"Тема: Код подтверждения Yandex Reviews Scraper\n"
        f"Отправлено: {datetime.now().isoformat()}\n\n"
        f"Код: {code}\n"
        f"Действует {config.CONFIRM_CODE_TTL_HOURS} ч.\n"
    )
    path.write_text(body, encoding="utf-8")
    logger.info(f"📧 Письмо (console-бэкенд) для {email}: код {code} → {path}")


def _send_via_smtp(email: str, code: str) -> None:
    """SMTP-бэкенд: настоящее письмо. Ошибка доставки не роняет регистрацию —
    код можно выслать повторно."""
    msg = MIMEText(
        f"Ваш код подтверждения: {code}\n"
        f"Код действует {config.CONFIRM_CODE_TTL_HOURS} ч.",
        "plain", "utf-8",
    )
    msg["Subject"] = "Код подтверждения Yandex Reviews Scraper"
    msg["From"] = config.SMTP_FROM
    msg["To"] = email
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as srv:
            if config.SMTP_STARTTLS:
                srv.starttls()
            if config.SMTP_USER:
                srv.login(config.SMTP_USER, config.SMTP_PASS)
            srv.send_message(msg)
        logger.info(f"📧 Письмо (smtp) отправлено: {email}")
    except Exception as e:
        logger.error(f"SMTP не смог отправить письмо на {email}: {e} — код доступен в outbox")
        _write_outbox(email, code)
