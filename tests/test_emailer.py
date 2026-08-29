"""Тесты app/emailer.py по чек-листу EMAIL_INTEGRATION.md §5.

Запуск: .venv/bin/python -m pytest tests/test_emailer.py -v
Сеть не используется: smtplib.SMTP подменяется фейком."""
import asyncio
import smtplib

import pytest

from app import config
from app.emailer import EmailSendError, send_email, send_auth_code


class FakeSMTP:
    """Фейк smtplib.SMTP: записывает вызовы; failures — сколько первых вызовов падают."""
    last_instance = None

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.sent = None
        FakeSMTP.last_instance = self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def sendmail(self, from_addr, to_addrs, msg):
        if FakeSMTP.failures > 0:
            FakeSMTP.failures -= 1
            raise smtplib.SMTPException("временный сбой")
        self.sent = {"from_addr": from_addr, "to_addrs": to_addrs, "msg": msg}


FakeSMTP.failures = 0


@pytest.fixture(autouse=True)
def mail_enabled(monkeypatch):
    """Тесты не зависят от локального .env: отправка в тестах всегда включена
    (кроме test_disabled_falls_back_to_outbox, который выключает сам)."""
    monkeypatch.setattr(config, "MAIL_ENABLED", True)


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.failures = 0
    monkeypatch.setattr("app.emailer.smtplib.SMTP", FakeSMTP)
    return FakeSMTP


def _parse(raw: str):
    import email
    from email import policy
    return email.message_from_string(raw, policy=policy.default)


def test_envelope_from_equals_header_from(fake_smtp):
    """Урок №1: MAIL FROM (конверт) обязан совпадать с заголовком From."""
    asyncio.run(send_email("user@example.com", "Тема", "Тело"))
    sent = FakeSMTP.last_instance.sent
    assert sent, "письмо не отправлено"
    m = _parse(sent["msg"])
    from email.utils import parseaddr
    header_addr = parseaddr(str(m["From"]))[1]
    envelope_addr = parseaddr(sent["from_addr"])[1]
    assert envelope_addr == header_addr == config.MAIL_FROM


def test_subject_is_encoded_word(fake_smtp):
    """Урок №2: кириллица в теме — encoded-word от библиотеки email, не сырϊй текст."""
    asyncio.run(send_email("user@example.com", "Код подтверждения", "Тело"))
    raw = FakeSMTP.last_instance.sent["msg"]
    subject_line = [l for l in raw.split("\n") if l.startswith("Subject:")][0]
    assert "=?utf-8?" in subject_line.lower(), f"тема не закодирована: {subject_line}"
    assert "Код подтверждения" not in subject_line  # сыробы кириллицы в заголовке быть не должно


def test_body_utf8(fake_smtp):
    asyncio.run(send_email("user@example.com", "Тема", "Привет, мир — проверка"))
    m = _parse(FakeSMTP.last_instance.sent["msg"])
    assert m.get_content_charset() == "utf-8"
    assert "Привет, мир — проверка" in m.get_content()


def test_from_is_project_domain(fake_smtp):
    """Урок №5: From строго на домене проекта (punycode), не gmail/yandex."""
    asyncio.run(send_email("user@example.com", "Тема", "Тело"))
    addr = FakeSMTP.last_instance.sent["from_addr"]
    assert "xn--80aaacg3aje4aocssle9l.xn--p1ai" in addr
    assert "gmail.com" not in addr and "yandex.ru" not in addr


def test_retry_then_success(fake_smtp):
    """Две неудачи, третья попытка успешна — письмо ушло."""
    FakeSMTP.failures = 2
    asyncio.run(send_email("user@example.com", "Тема", "Тело"))
    assert FakeSMTP.last_instance.sent is not None


def test_retry_exhausted_raises(fake_smtp):
    """Все 3 попытки упали — EmailSendError, письмо не ушло."""
    FakeSMTP.failures = 3
    with pytest.raises(EmailSendError):
        asyncio.run(send_email("user@example.com", "Тема", "Тело"))
    assert FakeSMTP.last_instance.sent is None


def test_disabled_falls_back_to_outbox(monkeypatch, tmp_path):
    """MAIL_ENABLED=false: письмо не шлётся (SMTP не вызывается), код в outbox, EmailSendError."""
    monkeypatch.setattr(config, "MAIL_ENABLED", False)
    monkeypatch.setattr(config, "OUTBOX_DIR", str(tmp_path))
    called = {"smtp": False}

    def boom(*a, **kw):
        called["smtp"] = True

    monkeypatch.setattr("app.emailer.smtplib.SMTP", boom)
    with pytest.raises(EmailSendError):
        asyncio.run(send_auth_code("user@example.com", "123456", ttl_minutes=10))
    assert not called["smtp"]
    outbox_file = tmp_path / "user@example.com.txt"
    assert outbox_file.exists() and "123456" in outbox_file.read_text()


def test_auth_code_template(fake_smtp):
    """Шаблон кода: код отдельной строкой в начале, срок действия, «если не вы»."""
    asyncio.run(send_auth_code("user@example.com", "012345", ttl_minutes=10))
    m = _parse(FakeSMTP.last_instance.sent["msg"])
    body = m.get_content()
    assert "012345" in body
    assert "10 минут" in body
    assert "игнорируйте" in body
