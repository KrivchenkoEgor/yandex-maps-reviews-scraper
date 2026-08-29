"""Отправка писем через локальный Postfix (send-only). См. EMAIL_INTEGRATION.md.

Жёсткие правила из живых тестов Gmail (не нарушать):
- конвертный отправитель (sendmail from_addr) == заголовку From;
- тему/имена кодирует email.message.EmailMessage (руками заголовки не собирать);
- никаких своих DKIM/SPF заголовков — их ставит milter сервера;
- Message-ID не генерируем — Postfix добавит сам;
- From только на домене xn--80aaacg3aje4aocssle9l.xn--p1ai.
"""

import asyncio
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from loguru import logger

from app import config


class EmailSendError(Exception):
    """Письмо не отправлено после retry'ев (или отправка выключена)."""


def _mask(code: str) -> str:
    """Код для логов: только первые 2 цифры (полный код в лог нельзя)."""
    return f"{code[:2]}****" if code else "****"


def _build_message(to: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject                    # кириллица — закодирует сам
    msg["From"] = formataddr((config.MAIL_FROM_NAME, config.MAIL_FROM))
    msg["To"] = to
    msg.set_content(body)                       # plain text, charset=utf-8 автоматически
    return msg


def _send_sync(to: str, subject: str, body: str) -> None:
    msg = _build_message(to, subject, body)
    # Урок №1: from_addr (конверт, MAIL FROM) обязан совпадать с заголовком From
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=config.SMTP_TIMEOUT_SEC) as s:
        s.sendmail(msg["From"], [to], msg.as_string())


def _dev_outbox(to: str, subject: str, body: str) -> None:
    """Dev-режим (MAIL_ENABLED=false): письмо не шлём, кладём в файл outbox —
    удобно отлаживать UI без почтовой инфраструктуры."""
    from pathlib import Path
    outbox = Path(config.OUTBOX_DIR)
    outbox.mkdir(parents=True, exist_ok=True)
    path = outbox / f"{to}.txt"
    path.write_text(f"Кому: {to}\nТема: {subject}\n\n{body}\n", encoding="utf-8")
    logger.warning(f"MAIL_ENABLED=false — письмо не отправлено, сохранено в {path}")


async def send_email(to: str, subject: str, body: str) -> None:
    """Отправить текстовое письмо. Бросает EmailSendError после retry'ев.

    Retry: до 3 попыток с backoff 2/4/8 c на SMTPException / ConnectionRefusedError
    / таймаут. При MAIL_ENABLED=false — dev-outbox + EmailSendError (приложение
    не роняем, вызывающий код решает, что показать пользователю)."""
    if not config.MAIL_ENABLED:
        _dev_outbox(to, subject, body)
        raise EmailSendError(
            "Отправка писем выключена (MAIL_ENABLED=false) — код сохранён в "
            f"{config.OUTBOX_DIR}/{to}.txt (dev-режим)")

    delays = (2, 4, 8)
    last_err: Exception | None = None
    for attempt, delay in enumerate(delays, 1):
        try:
            await asyncio.to_thread(_send_sync, to, subject, body)
            logger.info(f"Письмо отправлено: {to} ({subject})")
            return
        except (smtplib.SMTPException, ConnectionRefusedError, TimeoutError, OSError) as e:
            last_err = e
            logger.warning(f"Попытка {attempt}/3 отправки на {to} не удалась: {e}")
            if attempt < len(delays):
                await asyncio.sleep(delay)
    logger.error(f"Письмо на {to} не отправлено после 3 попыток: {last_err}")
    raise EmailSendError(f"Не удалось отправить письмо на {to}: {last_err}")


async def send_auth_code(to: str, code: str, ttl_minutes: int = 10) -> None:
    """Письмо с кодом авторизации. Шаблон — здесь, НЕ в вызывающем коде."""
    body = (
        "Ваш код подтверждения:\n\n"
        f"    {code}\n\n"
        f"Код действует {ttl_minutes} минут.\n"
        "Если вы не запрашивали код — просто проигнорируйте это письмо.\n\n"
        "РаботайОтзывами\n"
        "https://xn--80aaacg3aje4aocssle9l.xn--p1ai"
    )
    logger.info(f"Отправляю код подтверждения на {to} (код {_mask(code)})")
    await send_email(to, "Код подтверждения РаботайОтзывами", body)


if __name__ == "__main__":
    """Живой тест доставки (запускать вручную НА СЕРВЕРЕ, не в CI):
        python -m app.emailer --test drdiz14@gmail.com
    Критерий: в /var/log/mail.log — status=sent (250 ...);
    в Gmail «Показать оригинал»: dkim=pass, spf=pass, dmarc=pass."""
    import sys

    if len(sys.argv) != 3 or sys.argv[1] != "--test":
        print("Использование: python -m app.emailer --test <ваш@email>")
        raise SystemExit(1)
    to = sys.argv[2]
    test_code = "123456"
    try:
        asyncio.run(send_auth_code(to, test_code, ttl_minutes=config.CONFIRM_CODE_TTL_MIN))
        print(f"✅ Письмо с тестовым кодом отправлено на {to}. "
              f"Проверьте /var/log/mail.log (status=sent) и папку «Спам» в Gmail.")
    except EmailSendError as e:
        print(f"❌ {e}")
        raise SystemExit(1)
