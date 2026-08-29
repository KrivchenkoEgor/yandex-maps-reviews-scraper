"""
auth — регистрация и вход пользователей Yandex Reviews Scraper.

Реестр аккаунтов хранится в db/users/users.db (SQLite). Данные скрейпа каждого
пользователя — в отдельной БД db/users/uid_<id>/ya_ot.db (та же схема, что
и раньше), поэтому таблицы отзывов не меняются и код database.py
переиспользуется как есть.

Пароли: PBKDF2-HMAC-SHA256 (стандартная библиотека, без новых зависимостей).
Коды подтверждения: 6 цифр через secrets, в БД только sha256-хэш с солью
проекта (EMAIL_INTEGRATION.md §4): TTL 10 минут, максимум 5 попыток ввода,
повторная отправка не чаще раза в 60 секунд. Доставка — app/emailer.py
(локальный Postfix; MAIL_ENABLED=false → dev-режим, код в logs/outbox).
"""

import asyncio
import hashlib
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from app import config
from app.emailer import EmailSendError, send_auth_code

USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    is_verified INTEGER NOT NULL DEFAULT 0,
    api_token TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS email_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT 'register',
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_email_codes_email ON email_codes(email, purpose);
"""

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CODE_TTL_MIN = config.CONFIRM_CODE_TTL_MIN      # срок жизни кода, минут
MAX_CODE_ATTEMPTS = 5                            # попыток ввода на один код
RESEND_COOLDOWN_SEC = 60                         # анти-фlood повторной отправки
CODE_SALT = "ya_ot_email_code_v1"                # соль проекта для хэша кода
MAX_LOGIN_FAILS = 5                              # неудачных входов до паузы
LOGIN_LOCKOUT_SEC = 60                           # длительность паузы, секунд

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


def _hash_code(email: str, code: str) -> str:
    """Хэш кода с солью проекта — в БД храним только его (EMAIL_INTEGRATION.md §4)."""
    return hashlib.sha256(f"{CODE_SALT}:{_norm_email(email)}:{code.strip()}".encode()).hexdigest()


def user_db_path(user_id: int) -> Path:
    """Персональная БД пользователя (shops/reviews/monitoring — схема прежняя)."""
    return Path(config.USER_DATA_DIR) / f"uid_{user_id}" / "ya_ot.db"


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(registry_path())
    con.row_factory = sqlite3.Row
    return con


def get_user(user_id: int) -> Optional[dict[str, Any]]:
    init_registry()
    con = _con()
    try:
        row = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def _get_by_email(email: str) -> Optional[dict[str, Any]]:
    con = _con()
    try:
        row = con.execute("SELECT * FROM users WHERE email=?", (_norm_email(email),)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def get_unverified_user(email: str) -> Optional[dict[str, Any]]:
    """Незавершённая регистрация: аккаунт есть, email не подтверждён (для UI)."""
    user = _get_by_email(email)
    if user and not user["is_verified"]:
        return {"email": user["email"], "user_id": user["id"]}
    return None


def get_user_by_token(token: str) -> Optional[dict[str, Any]]:
    """Найти подтверждённого пользователя по API-токену (для /api/scrape).
    Возвращает ту же форму, что и login_user: {user_id, email, api_token}."""
    if not token:
        return None
    init_registry()
    con = _con()
    try:
        row = con.execute(
            "SELECT * FROM users WHERE api_token=? AND is_verified=1", (str(token),)
        ).fetchone()
        if not row:
            return None
        return {"user_id": row["id"], "email": row["email"], "api_token": row["api_token"]}
    finally:
        con.close()


def _issue_code(email: str, purpose: str = "register") -> str:
    """Выдать новый код: старые активные погасить, вставить свежий (хэш в БД)."""
    code = f"{secrets.randbelow(10**6):06d}"
    now = datetime.now()
    con = _con()
    try:
        con.execute(
            "UPDATE email_codes SET used_at=? WHERE email=? AND purpose=? AND used_at IS NULL",
            (now.isoformat(), email, purpose),
        )
        con.execute(
            "INSERT INTO email_codes(email,code_hash,purpose,created_at,expires_at)"
            " VALUES(?,?,?,?,?)",
            (email, _hash_code(email, code), purpose,
             now.isoformat(), (now + timedelta(minutes=CODE_TTL_MIN)).isoformat()),
        )
        con.commit()
    finally:
        con.close()
    return code


def _send_code(email: str, code: str) -> None:
    """Доставить код письмом; ошибки доставки — AuthError с понятным текстом.

    Работает и из синхронного контекста (Gradio-хендлеры), и изнутри цикла
    asyncio (тесты): во втором случае корутина исполняется в отдельном потоке
    со своим циклом — asyncio.run() из работающего цикла вызывать нельзя."""
    import concurrent.futures

    async def _send():
        await send_auth_code(email, code, ttl_minutes=CODE_TTL_MIN)

    try:
        asyncio.get_running_loop()
        in_loop = True
    except RuntimeError:
        in_loop = False
    try:
        if in_loop:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                ex.submit(lambda: asyncio.run(_send())).result()
        else:
            asyncio.run(_send())
    except EmailSendError as e:
        raise AuthError(
            "Аккаунт создан, но письмо с кодом не отправлено. Проверьте настройки почты "
            f"(MAIL_ENABLED/Postfix) и попросите выслать код ещё раз. Детали: {e}"
        )


def register_user(email: str, password: str) -> dict[str, Any]:
    """Зарегистрировать пользователя и отправить код подтверждения.

    Возвращает {email, user_id}. Бросает AuthError с понятным текстом."""
    init_registry()
    email = _norm_email(email)
    if not EMAIL_RE.match(email):
        raise AuthError("Введите корректный email (например, ivan@example.com)")
    if len(password or "") < 6:
        raise AuthError("Пароль должен быть не короче 6 символов")
    if _get_by_email(email):
        raise AuthError("Пользователь с таким email уже зарегистрирован")

    salt = secrets.token_hex(16)
    con = _con()
    try:
        cur = con.execute(
            "INSERT INTO users(email,password_hash,salt,is_verified,api_token,created_at)"
            " VALUES(?,?,?,0,?,?)",
            (email, _hash_password(password, salt), salt,
             secrets.token_hex(16), datetime.now().isoformat()),
        )
        con.commit()
        user_id = cur.lastrowid
    finally:
        con.close()

    code = _issue_code(email)
    _send_code(email, code)
    logger.info(f"Регистрация: {email} (id={user_id}), код подтверждения отправлен")
    return {"email": email, "user_id": user_id}


def resend_code(email: str) -> None:
    """Выслать новый код подтверждения (не чаще раза в 60 секунд)."""
    user = _get_by_email(email)
    if not user:
        raise AuthError("Пользователь с таким email не найден")
    if user["is_verified"]:
        raise AuthError("Email уже подтверждён — просто войдите")

    email = _norm_email(email)
    con = _con()
    try:
        row = con.execute(
            "SELECT created_at FROM email_codes WHERE email=? AND purpose='register'"
            " ORDER BY id DESC LIMIT 1", (email,)).fetchone()
    finally:
        con.close()
    if row:
        try:
            elapsed = (datetime.now() - datetime.fromisoformat(row["created_at"])).total_seconds()
            if elapsed < RESEND_COOLDOWN_SEC:
                wait = int(RESEND_COOLDOWN_SEC - elapsed) + 1
                raise AuthError(f"Повторная отправка не чаще раза в минуту — подождите {wait} с")
        except (TypeError, ValueError):
            pass

    code = _issue_code(email)
    _send_code(email, code)
    logger.info(f"Повторный код подтверждения: {email}")


def confirm_email(email: str, code: str) -> None:
    """Подтвердить email кодом из письма (хэш-сверка, лимит попыток)."""
    user = _get_by_email(email)
    if not user:
        raise AuthError("Пользователь с таким email не найден")
    if user["is_verified"]:
        return  # уже подтверждён — идемпотентно

    email = _norm_email(email)
    con = _con()
    try:
        row = con.execute(
            "SELECT * FROM email_codes WHERE email=? AND purpose='register' AND used_at IS NULL"
            " ORDER BY id DESC LIMIT 1", (email,)).fetchone()
        if not row:
            raise AuthError("Код не найден — запросите новый («Не пришёл код»)")
        if datetime.fromisoformat(row["expires_at"]) < datetime.now():
            raise AuthError("Код истёк — запросите новый («Не пришёл код»)")
        if row["attempts"] >= MAX_CODE_ATTEMPTS:
            raise AuthError("Слишком много неверных попыток — запросите новый код")

        if secrets.compare_digest(row["code_hash"], _hash_code(email, str(code))):
            con.execute("UPDATE email_codes SET used_at=? WHERE id=?",
                        (datetime.now().isoformat(), row["id"]))
            con.execute("UPDATE users SET is_verified=1 WHERE id=?", (user["id"],))
            con.commit()
        else:
            attempts = row["attempts"] + 1
            con.execute("UPDATE email_codes SET attempts=? WHERE id=?", (attempts, row["id"]))
            con.commit()
            left = MAX_CODE_ATTEMPTS - attempts
            raise AuthError(
                f"Неверный код (осталось попыток: {left})" if left > 0
                else "Слишком много неверных попыток — запросите новый код")
    except AuthError:
        raise
    finally:
        con.close()
    logger.info(f"Email подтверждён: {email}")


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
        # Временный реестр и личная БД — ничего в проде не трогаем.
        # Отправку писем подменяем фейком (сеть и Postfix не нужны).
        config.USER_DATA_DIR = "tmp/test_users"
        Path("tmp/test_users").mkdir(parents=True, exist_ok=True)
        registry_path().unlink(missing_ok=True)

        sent: list[tuple[str, str]] = []

        async def fake_send(to, code, ttl_minutes=10):
            sent.append((to, code))

        global send_auth_code
        send_auth_code = fake_send

        # 1. Регистрация: код выдан, в БД только хэш
        u = register_user("fake.user@example.com", "secret123")
        assert sent and sent[-1][0] == u["email"], "код не «отправлен»"
        code = sent[-1][1]
        con = _con()
        row = con.execute("SELECT code_hash FROM email_codes WHERE email=?", (u["email"],)).fetchone()
        con.close()
        assert code not in (row["code_hash"] or ""), "код лежит в БД ОТКРЫТЫМ текстом!"
        assert row["code_hash"] == _hash_code(u["email"], code), "хэш не совпадает с ожидаемым"

        # 2. Вход до подтверждения запрещён
        try:
            login_user("fake.user@example.com", "secret123")
            raise SystemExit("ошибка: впустили неподтверждённого")
        except AuthError:
            pass

        # 3. Неверный код: attempts растёт, после 5 — блокировка
        for i in range(MAX_CODE_ATTEMPTS):
            try:
                confirm_email("fake.user@example.com", "000000")
            except AuthError as e:
                assert "Неверный код" in str(e) or "много неверных" in str(e)
            else:
                raise SystemExit("ошибка: неверный код принят")
        try:
            confirm_email("fake.user@example.com", "000000")
            raise SystemExit("ошибка: попытки не блокируются")
        except AuthError as e:
            assert "запросите новый" in str(e)

        # 4. Новый код после блокировки — подтверждает (отматываем кулдаун 60с)
        con = _con()
        con.execute("UPDATE email_codes SET created_at=datetime('now','-61 seconds')")
        con.commit()
        con.close()
        resend_code("fake.user@example.com")
        code2 = sent[-1][1]

        # 5. Кулдаун повторной отправки (60с) — пока email ещё не подтверждён
        try:
            resend_code("fake.user@example.com")
            raise SystemExit("ошибка: resend без кулдауна")
        except AuthError as e:
            assert "не чаще раза в минуту" in str(e)

        confirm_email("fake.user@example.com", code2)
        me = login_user("fake.user@example.com", "secret123")
        assert me["email"] == "fake.user@example.com" 

        # 6. Неверный пароль и дубль email
        try:
            login_user("fake.user@example.com", "wrong")
            raise SystemExit("ошибка: пустим с неверным паролем")
        except AuthError:
            pass
        try:
            register_user("FAKE.USER@example.com", "secret123")
            raise SystemExit("ошибка: дубль email принят")
        except AuthError:
            pass

        print("✅ Все проверки auth OK (хэши кодов, попытки, кулдаун, вход, изоляция e-mail)")

        import shutil
        shutil.rmtree("tmp/test_users", ignore_errors=True)
        Path("logs/outbox/fake.user@example.com.txt").unlink(missing_ok=True)

    asyncio.run(_demo())
