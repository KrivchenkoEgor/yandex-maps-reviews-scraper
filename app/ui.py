"""
ui — Gradio-интерфейс для Yandex Reviews Scraper.

Три вкладки из AGENTS.md:
- Одиночный: поле ссылки → «Скачать отзывы» → превью + кнопки экспорта
- Пакетный: загрузка Excel со списком ссылок → прогресс → общий отчёт
- Мониторинг: список подписок, новые отзывы, добавление в мониторинг

Делаем максимально простой, без JS, в стиле "для не-программиста" — всё по-русски.
"""

import asyncio
import random
import time
from pathlib import Path
from typing import Any

import gradio as gr
import pandas as pd
from loguru import logger

from app import __git_url__, __version__, config
from app import auth
from app.auth import AuthError
from app.database import Database, get_db
from app.exporter import export_csv, export_excel, export_json, excel_date_sort_key, to_excel_date
from app.url_resolver import resolve_yandex_url
from app.yandex_scraper import YandexScraper

# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------

def _user_db(user: dict[str, Any] | None) -> Database | None:
    """Личная БД вошедшего пользователя (None → общая, для API-совместимости)."""
    if not user:
        return None
    return Database(auth.user_db_path(user["user_id"]))


def _user_out_dir(user: dict[str, Any] | None) -> str | None:
    """Личная папка экспортов пользователя."""
    if not user:
        return None
    return str(Path(config.OUTPUT_DIR) / "users" / str(user["user_id"]))


_LOGIN_HINT = "⚠️ Сначала войдите на вкладке «Аккаунт» — регистрация занимает минуту."
_LOGIN_TABLE = pd.DataFrame([{"Сообщение": "Войдите на вкладке «Аккаунт», чтобы видеть подписки"}])

def _fmt_preview(shop: dict[str, Any], reviews: list[dict[str, Any]]) -> str:
    if not shop:
        return "Нет данных"
    warning = shop.get("_partial_warning")
    lines = [
        f"**{shop.get('name','—')}**",
        f"Адрес: {shop.get('address','—')}",
        f"Рейтинг: {shop.get('rating','—')} | Всего отзывов: {shop.get('total_reviews','—')} | OID: {shop.get('oid','—')}",
        f"Скачано отзывов: {len(reviews)}",
    ]
    if warning:
        lines.append(warning)
    lines += ["", "Первые 5 отзывов:"]
    for i, r in enumerate(reviews[:5], 1):
        text = (r.get("text") or "")[:200].replace("\n", " ")
        lines.append(f"{i}. **{r.get('author','Аноним')}** ({r.get('rating','—')}★, {r.get('date','')}) — {text} [👍{r.get('likes',0)} 👎{r.get('dislikes',0)}]")
        if r.get("owner_response"):
            lines.append(f"   ↳ Ответ: {(r['owner_response'].get('text') or '')[:120]}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Обработчики
# ---------------------------------------------------------------------------

async def _scrape_one(url: str, progress_cb=None, db=None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Скачать один магазин с кэшем 24ч (логика из AGENTS.md).
    db — куда сохранять; по умолчанию общая БД, у вошедшего пользователя — его личная."""
    url = url.strip()
    if not url:
        raise ValueError("Вставьте ссылку на магазин с Яндекс.Карт")

    resolved = await asyncio.to_thread(resolve_yandex_url, url)
    oid = resolved["oid"]
    if not oid:
        raise ValueError("Не удалось извлечь OID. Убедитесь что ссылка ведёт на карточку магазина (с poi).")

    db = db or get_db()
    await db.init()

    # Кэш 24ч — если свежо, берём из БД, но проверяем полноту выгрузки
    ttl = config.SHOP_CACHE_TTL_HOURS
    if await db.is_cache_fresh(oid, ttl_hours=ttl):
        shop = await db.get_shop(oid)
        reviews = await db.get_reviews(oid)
        if shop and reviews:
            total = shop.get("total_reviews") or 0
            if total and len(reviews) < total * 0.9:
                logger.info(f"Кэш неполный {oid}: {len(reviews)}/{total}, перезапрос")
            else:
                logger.info(f"Кэш HIT {oid}: {len(reviews)} отзывов")
                return shop, reviews

    # Скрапим — лог пишем ДО попытки, чтобы неудачи тоже фиксировались
    log_id = await db.log_start(oid)
    try:
        def _prog(msg: str):
            if progress_cb:
                progress_cb(msg)

        scraper = YandexScraper(headless=True, on_progress=_prog)
        result = await scraper.scrape(url)
        shop = result["shop"]
        reviews = result["reviews"]

        # Сохраняем в БД даже если выгрузка частичная — лучше частичные данные, чем ничего
        await db.upsert_shop(shop)
        await db.upsert_reviews(oid, reviews)
        total = shop.get("total_reviews") or 0
        is_partial = bool(total and total > 20 and len(reviews) < total * 0.9 and len(reviews) < total)
        status = "partial" if is_partial else "ok"
        await db.log_finish(log_id, reviews_found=len(reviews), status=status)
        if is_partial:
            logger.warning(f"Частичная выгрузка {oid}: {len(reviews)}/{total}")
            shop["_partial_warning"] = f"⚠️ Скачано {len(reviews)} из {total} — Яндекс отдал не всё"
    except Exception as e:
        try:
            await db.log_finish(log_id, reviews_found=0, status="error", error=str(e))
        except Exception:
            pass
        raise

    return shop, reviews


def handle_single(url: str, user: dict[str, Any] | None, progress=gr.Progress(track_tqdm=True)):
    """Gradio-обработчик одиночного режима (синхронная обёртка)."""
    if not user:
        return (_LOGIN_HINT, gr.update(visible=False), gr.update(visible=False),
                gr.update(visible=False), gr.update(visible=False))
    progress(0, desc="Разрешаю ссылку...")
    try:
        shop, reviews = asyncio.run(_scrape_one(
            url, progress_cb=lambda m: progress(0.5, desc=m), db=_user_db(user)))
    except Exception as e:
        logger.error(f"Ошибка одиночного: {e}")
        return (
            f"❌ Ошибка: {e}",
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
        )

    preview = _fmt_preview(shop, reviews)
    df_preview = pd.DataFrame([
        {"Автор": r.get("author"), "Оценка": r.get("rating"), "Дата": r.get("date"), "Текст": (r.get("text") or "")[:120], "👍": r.get("likes",0), "👎": r.get("dislikes",0)}
        for r in reviews[:5]
    ])

    out_dir = _user_out_dir(user)
    try:
        p_xlsx = export_excel(shop, reviews, out_dir=out_dir)
        p_csv = export_csv(shop, reviews, out_dir=out_dir)
        p_json = export_json(shop, reviews, out_dir=out_dir)
    except Exception as e:
        logger.error(f"Ошибка экспорта: {e}")
        p_xlsx = p_csv = p_json = None

    return (
        preview,
        gr.update(value=df_preview, visible=True),
        gr.update(value=str(p_xlsx) if p_xlsx else None, visible=True),
        gr.update(value=str(p_csv) if p_csv else None, visible=True),
        gr.update(value=str(p_json) if p_json else None, visible=True),
    )


def handle_batch(file, user: dict[str, Any] | None, progress=gr.Progress(track_tqdm=True)):
    """Пакетный: Excel со списком ссылок (колонка 'Ссылка' или первая колонка)."""
    if not user:
        return _LOGIN_HINT, gr.update(visible=False)
    if file is None:
        return "Загрузите Excel/CSV со списком ссылок", gr.update(visible=False)

    path = Path(file.name) if hasattr(file, "name") else Path(file)
    try:
        if path.suffix.lower() in (".xlsx", ".xls"):
            df = pd.read_excel(path, dtype=str)
        else:
            df = pd.read_csv(path, dtype=str)
    except Exception as e:
        return f"❌ Не удалось прочитать файл: {e}", gr.update(visible=False)

    # Ищем колонку с ссылками
    col = None
    for c in df.columns:
        if "ссыл" in str(c).lower() or "link" in str(c).lower() or "url" in str(c).lower():
            col = c
            break
    if col is None:
        col = df.columns[0]

    urls = [str(x).strip() for x in df[col].dropna().tolist() if str(x).strip().startswith("http")]
    urls = list(dict.fromkeys(urls))  # уникальные с сохранением порядка
    if not urls:
        return "❌ В файле не найдено ссылок (ищу колонку 'Ссылка')", gr.update(visible=False)

    all_reviews: list[dict[str, Any]] = []
    shop_rows: list[dict[str, Any]] = []
    errors: list[str] = []

    for i, u in enumerate(urls, 1):
        progress(i / len(urls), desc=f"Обрабатываю {i}/{len(urls)}: {u[:40]}...")
        try:
            shop, reviews = asyncio.run(_scrape_one(u, db=_user_db(user)))
            for r in reviews:
                row = {"Магазин": shop.get("name"), "OID": shop.get("oid"), "Ссылка": u, **{f"Отзыв_{k}": v for k, v in r.items() if k in ("author","rating","date","text","likes","dislikes","is_verified")}}
                all_reviews.append(row)
            shop_rows.append({"Ссылка": u, "Магазин": shop.get("name"), "OID": shop.get("oid"), "Отзывов": len(reviews), "Статус": "OK"})
        except Exception as e:
            errors.append(f"{u}: {e}")
            shop_rows.append({"Ссылка": u, "Магазин": "", "OID": "", "Отзывов": 0, "Статус": f"Ошибка: {e}"})
        time.sleep(random.uniform(2, 3))

    # Общий отчёт Excel: отзывы от новой к старой, даты — ДД.ММ.ГГГГ
    df_out = pd.DataFrame(all_reviews)
    if not df_out.empty and "Отзыв_date" in df_out.columns:
        df_out = (
            df_out.assign(_k=df_out["Отзыв_date"].map(excel_date_sort_key))
            .sort_values("_k", ascending=False)
            .drop(columns="_k")
            .reset_index(drop=True)
        )
        df_out["Отзыв_date"] = df_out["Отзыв_date"].map(to_excel_date)
    df_shops = pd.DataFrame(shop_rows)
    out_dir = Path(_user_out_dir(user))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"batch_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.xlsx"
    with pd.ExcelWriter(out_path, engine="openpyxl") as w:
        df_out.to_excel(w, sheet_name="Отзывы", index=False)
        df_shops.to_excel(w, sheet_name="Отчёты_по_магазинам", index=False)
        if not df_out.empty and "Отзыв_date" in df_out.columns:
            ws = w.sheets["Отзывы"]
            date_col = list(df_out.columns).index("Отзыв_date") + 1
            for row in range(2, ws.max_row + 1):
                ws.cell(row=row, column=date_col).number_format = "DD.MM.YYYY"

    msg = f"Готово: {len(urls)} ссылок, {len(all_reviews)} отзывов. Ошибок: {len(errors)}"
    if errors:
        msg += "\n" + "\n".join(errors[:5])
    return msg, gr.update(value=str(out_path), visible=True)


def handle_monitor_add(url: str, interval_hours: int, user: dict[str, Any] | None):
    """Добавить магазин в мониторинг (в личную БД пользователя)."""
    if not user:
        return _LOGIN_HINT
    if not url.strip():
        return "Вставьте ссылку"
    try:
        resolved = resolve_yandex_url(url.strip())
        oid = resolved["oid"]
        if not oid:
            return "❌ Не удалось извлечь OID из ссылки"
        db = _user_db(user)
        async def _add():
            await db.init()
            shop = await db.get_shop(oid)
            if not shop:
                scraper = YandexScraper(headless=True)
                result = await scraper.scrape(url.strip())
                await db.upsert_shop(result["shop"])
                await db.upsert_reviews(oid, result["reviews"])
            await db.add_monitor(oid, interval_hours=int(interval_hours))
            return oid
        oid2 = asyncio.run(_add())
        return f"✅ Добавлен в мониторинг: {oid2}, интервал {interval_hours}ч"
    except Exception as e:
        return f"❌ Ошибка: {e}"


def handle_monitor_list(user: dict[str, Any] | None):
    """Список подписок текущего пользователя из его личной БД."""
    if not user:
        return _LOGIN_TABLE
    db = _user_db(user)
    async def _list():
        await db.init()
        ms = await db.list_monitors(active_only=False)
        rows = []
        for m in ms:
            shop = await db.get_shop(m["shop_oid"])
            rows.append({
                "ID": m["id"],
                "Магазин": shop.get("name") if shop else m["shop_oid"],
                "OID": m["shop_oid"],
                "Интервал ч": m["interval_hours"],
                "Последняя проверка": m.get("last_check",""),
                "Активна": "Да" if m["active"] else "Нет",
            })
        return pd.DataFrame(rows) if rows else pd.DataFrame([{"Сообщение": "Подписок нет — добавьте ссылку выше"}])
    return asyncio.run(_list())


# ---------------------------------------------------------------------------
# Аккаунт: регистрация, подтверждение кодом, вход, выход
# (прогрессивное раскрытие: подтверждение показываем только после регистрации)
# ---------------------------------------------------------------------------

def handle_register(email: str, password: str):
    """Создать аккаунт и показать шаг 2 — ввод кода (email подставим сами)."""
    try:
        u = auth.register_user(email, password)
        # Текст зависит от режима: в бою письмо ушло почтой, в dev лежит в outbox
        if config.MAIL_ENABLED:
            note = "Код отправлен на почту — проверьте входящие и папку «Спам»."
        else:
            note = f"Отправка писем выключена (dev-режим): код лежит в logs/outbox/{u['email']}.txt."
        return (
            f"✅ Аккаунт создан для **{u['email']}**. {note} Введите код ниже, чтобы завершить.",
            gr.update(visible=True),   # шаг 2: подтверждение
            gr.update(value=u["email"]),  # email подставлен
        )
    except AuthError as e:
        # аккаунт создан, но письмо не ушло (dev-режим/нет Postfix) — шаг 2 открываем:
        # код можно взять из logs/outbox или выслать заново кнопкой ниже
        pending = auth.get_unverified_user(email)
        if pending:
            return (f"⚠️ {e}", gr.update(visible=True), gr.update(value=pending["email"]))
        return f"❌ {e}", gr.update(), gr.update()
    except Exception as e:
        logger.error(f"Регистрация: {e}")
        return f"❌ Ошибка: {e}", gr.update(), gr.update()


def handle_resend(email: str):
    """Выслать новый код подтверждения на уже введённый email."""
    try:
        auth.resend_code(email)
        return "✅ Новый код отправлен"
    except AuthError as e:
        return f"❌ {e}"
    except Exception as e:
        return f"❌ Ошибка: {e}"


def handle_confirm(email: str, code: str):
    """Подтвердить email кодом."""
    try:
        auth.confirm_email(email, code)
        return "✅ Email подтверждён — откройте вкладку **Вход** и войдите с паролем"
    except AuthError as e:
        return f"❌ {e}"
    except Exception as e:
        return f"❌ Ошибка: {e}"


def handle_login(email: str, password: str):
    """Войти: скрываем формы, показываем карточку аккаунта."""
    try:
        user = auth.login_user(email, password)
    except AuthError as e:
        return (None, f"❌ {e}", "Вы не вошли — скачивание и мониторинг недоступны.",
                gr.update(visible=True), gr.update(visible=False), gr.update(visible=False), _LOGIN_TABLE)
    except Exception as e:
        logger.error(f"Вход: {e}")
        return (None, f"❌ Ошибка: {e}", "Вы не вошли — скачивание и мониторинг недоступны.",
                gr.update(visible=True), gr.update(visible=False), gr.update(visible=False), _LOGIN_TABLE)
    return (
        user,
        "",
        f"Вы вошли как **{user['email']}** — данные и подписки теперь ваши личные.",
        gr.update(visible=False),  # формы входа/регистрации
        gr.update(visible=True),   # панель сессии
        gr.update(visible=True),   # рабочие вкладки
        handle_monitor_list(user),
    )


def handle_logout(user: dict[str, Any] | None):
    """Выйти: вернуть экран входа в исходное состояние."""
    email = user["email"] if user else ""
    return (
        None,
        f"Вы вышли из аккаунта {email}." if email else "",
        "Вы не вошли — скачивание и мониторинг недоступны.",
        gr.update(visible=True),   # формы входа/регистрации
        gr.update(visible=False),  # панель сессии
        gr.update(visible=False),  # рабочие вкладки
        _LOGIN_TABLE,
        gr.update(visible=False),  # спрятать блок подтверждения
    )


# ---------------------------------------------------------------------------
# Дизайн: тема в стиле Apple (проверено на Gradio 4.44)
# Системный шрифт SF Pro, фон #f5f5f7, белые карточки 18px, кнопки-пилюли #0071e3,
# табы — сегмент-контрол как в iOS. Только внешний вид, логика не тронута.
# ---------------------------------------------------------------------------

_APP_CSS = """
/* --- База: фон и типографика Apple --- */
body { background: #f5f5f7 !important; }
.gradio-container {
  max-width: 1060px !important;
  margin: 0 auto !important;
  background: #f5f5f7 !important;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text",
               "Helvetica Neue", "Segoe UI", Roboto, Arial, sans-serif !important;
  color: #1d1d1f !important;
}
.gradio-container * { font-family: inherit !important; }

/* --- Шапка (hero) --- */
.app-hero { text-align: center !important; padding: 44px 0 8px 0 !important; }
.app-hero h1 {
  font-size: 40px !important; font-weight: 700 !important; letter-spacing: -0.022em !important;
  color: #1d1d1f !important; margin: 0 !important; border: none !important; padding: 0 !important;
}
.app-hero p { font-size: 17px !important; color: #6e6e73 !important; margin: 10px auto 0 !important; max-width: 640px !important; line-height: 1.5 !important; }

/* --- Рабочие вкладки: большая белая карточка (только после входа) --- */
#work-tabs {
  background: #ffffff !important;
  border: none !important;
  border-radius: 20px !important;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.05) !important;
  padding: 8px 28px 28px 28px !important;
  margin-top: 24px !important;
}
.tab-nav {
  border-bottom: none !important;
  display: flex !important;
  justify-content: center !important;
  gap: 0 !important;
  background: #ececee !important;
  border-radius: 12px !important;
  padding: 3px !important;
  width: max-content !important;
  margin: 20px auto 24px !important;
}
.tab-nav button {
  border: none !important; background: transparent !important;
  border-radius: 10px !important; padding: 8px 22px !important;
  font-size: 15px !important; font-weight: 500 !important; color: #6e6e73 !important;
  box-shadow: none !important; margin: 0 !important; opacity: 1 !important;
  transition: all .18s ease !important;
}
.tab-nav button:hover { color: #1d1d1f !important; }
.tab-nav button.selected {
  background: #ffffff !important; color: #1d1d1f !important;
  box-shadow: 0 1px 5px rgba(0, 0, 0, 0.12) !important;
}
.tabitem { border: none !important; background: transparent !important; }

/* --- Блоки внутри карточки: без рамок, воздух --- */
.block { border: none !important; background: transparent !important; box-shadow: none !important; }
.block.padded { padding: 6px 2px !important; }
.form { background: transparent !important; border: none !important; box-shadow: none !important; }
[data-testid="block-info"] {
  font-size: 13px !important; font-weight: 600 !important; color: #6e6e73 !important;
  margin-bottom: 7px !important; letter-spacing: 0.01em !important;
}

/* --- Кнопки-пилюли Apple --- */
button.lg {
  min-height: 48px !important; font-size: 16px !important; font-weight: 500 !important;
  border-radius: 980px !important; transition: all .15s ease !important;
}
button.primary {
  background: #0071e3 !important; background-image: none !important;
  color: #ffffff !important; border: none !important; box-shadow: none !important;
}
button.primary:hover { background: #0077ed !important; }
button.primary:active { background: #006edb !important; }
button.secondary {
  background: #e8e8ed !important; background-image: none !important;
  color: #1d1d1f !important; border: none !important; box-shadow: none !important;
}
button.secondary:hover { background: #dcdce1 !important; }

/* --- Поля ввода --- */
input[type="text"], input[type="number"], textarea {
  background: #fbfbfd !important;
  border: 1px solid #d2d2d7 !important;
  border-radius: 12px !important;
  box-shadow: none !important;
  font-size: 16px !important; color: #1d1d1f !important;
  transition: border-color .15s ease, box-shadow .15s ease !important;
}
input:hover, textarea:hover { border-color: #b8b8bf !important; }
input:focus, textarea:focus {
  border-color: #0071e3 !important;
  box-shadow: 0 0 0 4px rgba(0, 113, 227, 0.15) !important;
}
input::placeholder, textarea::placeholder { color: #a1a1a6 !important; }

/* --- Радио «Интервал» — сегмент-контрол --- */
[role="radiogroup"] { gap: 8px !important; }
[role="radiogroup"] label {
  border: 1px solid #d2d2d7 !important; background: #ffffff !important;
  border-radius: 980px !important; padding: 9px 18px !important;
  font-size: 15px !important; color: #1d1d1f !important; box-shadow: none !important;
  transition: all .15s ease !important;
}
[role="radiogroup"] label.selected {
  background: #0071e3 !important; border-color: #0071e3 !important; color: #ffffff !important;
}
[role="radiogroup"] label.selected span { color: #ffffff !important; }

/* --- Шаги «как это работает» под шапкой --- */
.how-wrap { text-align: center !important; margin: 6px 0 4px !important; }
.how-steps { display: inline-flex; gap: 36px; flex-wrap: wrap; justify-content: center; }
.how-step { color: #6e6e73; font-size: 14px; display: inline-flex; align-items: center; gap: 8px; }
.how-num {
  display: inline-flex; align-items: center; justify-content: center;
  width: 22px; height: 22px; border-radius: 50%;
  background: #0071e3; color: #fff; font-size: 12px; font-weight: 600;
}

/* --- Узкая колонка форм аккаунта (одна колонка — лучшие практики форм) --- */
.auth-col { max-width: 460px !important; margin: 0 auto !important; width: 100%; }

/* --- Вложенные табы Регистрация/Вход — компактный сегмент-контрол --- */
#auth-tabs .tab-nav { margin: 4px auto 16px !important; padding: 2px !important; }
#auth-tabs .tab-nav button { padding: 6px 26px !important; font-size: 14px !important; }

/* --- Группы-карточки (экран входа, формы, подписки) --- */
.gr-group, .gr-group .styler { background: #ffffff !important; }
.gr-group {
  border: 1px solid #ececf0 !important;
  border-radius: 20px !important;
  padding: 18px 24px !important;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.05) !important;
}
#auth-card { max-width: 560px !important; margin: 24px auto 0 !important; }

/* --- Строка «поле + кнопка» --- */
.scrape-row { align-items: flex-end !important; gap: 12px !important; }
.mon-row { align-items: flex-end !important; gap: 12px !important; }
.hint-line { color: #86868b !important; font-size: 13px !important; margin: 2px 0 0 !important; }

/* --- Таблицы (превью, подписки) --- */
#work-tabs table { border-collapse: collapse !important; font-size: 15px !important; }
#work-tabs th {
  font-weight: 600 !important; color: #6e6e73 !important; font-size: 12px !important;
  text-transform: uppercase !important; letter-spacing: 0.04em !important;
  border-bottom: 1px solid #e8e8ed !important; background: transparent !important;
}
#work-tabs td { border-bottom: 1px solid #f0f0f2 !important; color: #1d1d1f !important; }

/* --- Файлы и загрузка --- */
[data-testid="file"], .file-preview {
  border: 1px solid #e8e8ed !important; border-radius: 14px !important; background: #fbfbfd !important;
}

/* --- Markdown-тексты и подсказки --- */
.prose { color: #1d1d1f !important; }
.prose p { line-height: 1.5 !important; }
.hint-text, .prose p em { color: #6e6e73 !important; }

/* --- Прогресс --- */
.progress-bar { background: #0071e3 !important; }

/* --- Подвал --- */
footer { display: none !important; }
.app-footer { text-align: center !important; padding: 28px 0 34px !important; }
.app-footer div { font-size: 13px !important; color: #86868b !important; }
.app-footer a { color: #06c !important; text-decoration: none !important; }
.app-footer a:hover { text-decoration: underline !important; }
"""


# ---------------------------------------------------------------------------
# Сборка Gradio Blocks
# ---------------------------------------------------------------------------

def build_ui() -> gr.Blocks:
    theme = gr.themes.Default(
        primary_hue="blue",
        neutral_hue="slate",
        font=["-apple-system", "BlinkMacSystemFont", "SF Pro Text", "Segoe UI",
              "Helvetica Neue", "Arial", "sans-serif"],
        font_mono=["ui-monospace", "SF Mono", "Menlo", "Consolas", "monospace"],
    )
    with gr.Blocks(title="Yandex Reviews Scraper", theme=theme, css=_APP_CSS) as demo:
        # Сессия текущей вкладки браузера: dict пользователя или None
        user_state = gr.State(None)

        gr.Markdown(
            "# Yandex Reviews Scraper\n"
            "Отзывы с Яндекс.Карт в Excel, CSV или JSON — по ссылке на магазин.",
            elem_classes=["app-hero"],
        )
        # Как это работает: три шага — ориентир для нового пользователя
        gr.Markdown(
            "<div class='how-steps'>"
            "<span class='how-step'><span class='how-num'>1</span> Войдите или зарегистрируйтесь</span>"
            "<span class='how-step'><span class='how-num'>2</span> Вставьте ссылку на магазин</span>"
            "<span class='how-step'><span class='how-num'>3</span> Скачайте Excel, CSV или JSON</span>"
            "</div>",
            elem_classes=["how-wrap"],
        )

        # Экран входа/регистрации — единственное, что видно до авторизации
        with gr.Group(visible=True, elem_id="auth-card") as auth_area:
            with gr.Column(elem_classes=["auth-col"]):
                with gr.Tabs(elem_id="auth-tabs"):
                    with gr.Tab("Регистрация"):
                        reg_email = gr.Textbox(label="Email", placeholder="ivan@example.com")
                        reg_pwd = gr.Textbox(label="Пароль (от 6 символов)", type="password")
                        reg_btn = gr.Button("Зарегистрироваться", variant="primary")
                        reg_msg = gr.Markdown()
                        with gr.Group(visible=False) as conf_group:
                            gr.Markdown("**Шаг 2.** Введите код из письма — он уже отправлен.")
                            conf_email = gr.Textbox(label="Email (подставлен автоматически)")
                            conf_code = gr.Textbox(label="Код из письма (6 цифр)", placeholder="123456", max_lines=1)
                            conf_btn = gr.Button("Подтвердить email", variant="primary")
                            conf_msg = gr.Markdown()
                            res_btn = gr.Button("Не пришёл код — выслать ещё раз", variant="secondary", size="sm")
                            res_msg = gr.Markdown()
                    with gr.Tab("Вход"):
                        login_email = gr.Textbox(label="Email", placeholder="ivan@example.com")
                        login_pwd = gr.Textbox(label="Пароль", type="password")
                        login_btn = gr.Button("Войти", variant="primary")
                        login_msg = gr.Markdown()

        # Панель сессии — вместо форм после входа
        with gr.Group(visible=False) as user_area:
            with gr.Row(elem_classes=["session-row"]):
                user_info = gr.Markdown()
                logout_btn = gr.Button("Выйти", variant="secondary", size="sm", scale=0)

        # Рабочие вкладки — только для вошедших
        with gr.Tabs(visible=False, elem_id="work-tabs") as work_tabs:
            with gr.Tab("Скачивание"):
                with gr.Group():
                    with gr.Row(elem_classes=["scrape-row"]):
                        inp = gr.Textbox(
                            label="Ссылка на магазин", scale=4,
                            placeholder="https://yandex.ru/maps/-/CTwsUYyk  или полная с poi[uri]=ymapsbm1://org?oid=...",
                        )
                        btn = gr.Button("Скачать", variant="primary", scale=1)
                    gr.Markdown("Кэш 24 часа: повторный запрос вернёт данные из базы без обращения к Яндексу.", elem_classes=["hint-line"])
                preview_md = gr.Markdown()
                preview_df = gr.Dataframe(
                    label="Превью (5 отзывов)", interactive=False,
                    headers=["Автор", "Оценка", "Дата", "Текст", "👍", "👎"],
                    col_count=6,
                    visible=False,
                )
                with gr.Row():
                    dl_xlsx = gr.File(label="Excel (3 листа)", visible=False)
                    dl_csv = gr.File(label="CSV", visible=False)
                    dl_json = gr.File(label="JSON", visible=False)

            with gr.Tab("Пакетная обработка"):
                gr.Markdown("Excel или CSV с колонкой **Ссылка** (или первой колонкой). Магазины обрабатываются по очереди, с паузой 2–5 секунд.")
                batch_file = gr.File(label="Файл со ссылками", file_types=[".xlsx",".xls",".csv"])
                batch_btn = gr.Button("Запустить обработку", variant="primary")
                batch_msg = gr.Markdown()
                batch_out = gr.File(label="Общий отчёт (Excel)", visible=False)

            with gr.Tab("Мониторинг"):
                with gr.Group():
                    gr.Markdown("**Новая подписка** — проверка новых отзывов раз в день или неделю.")
                    with gr.Row(elem_classes=["mon-row"]):
                        mon_url = gr.Textbox(label="Ссылка на магазин", placeholder="https://yandex.ru/maps/-/CTwsUYyk", scale=3)
                        mon_interval = gr.Radio(choices=[("День", 24), ("Неделя", 168)], value=24, label="Интервал", scale=1)
                    mon_add = gr.Button("Добавить в мониторинг", variant="primary")
                mon_status = gr.Markdown()
                gr.Markdown("**Мои подписки**")
                mon_table = gr.Dataframe(
                    interactive=False,
                    headers=["ID", "Магазин", "OID", "Интервал ч", "Последняя проверка", "Активна"],
                )
                mon_refresh = gr.Button("Обновить список", variant="secondary", size="sm")

        gr.Markdown(
            f"<div>"
            f"v{__version__} • <a href='{__git_url__}' target='_blank'>GitHub</a> • "
            f"Yandex Reviews Scraper"
            f"</div>",
            elem_classes=["app-footer"],
        )

        # ------------------------------------------------------------------
        # Проводка событий (после объявления всех компонентов)
        # ------------------------------------------------------------------
        # Аккаунт
        reg_btn.click(handle_register, inputs=[reg_email, reg_pwd],
                      outputs=[reg_msg, conf_group, conf_email])
        res_btn.click(handle_resend, inputs=[conf_email], outputs=[res_msg])
        conf_btn.click(handle_confirm, inputs=[conf_email, conf_code], outputs=[conf_msg])
        login_btn.click(handle_login, inputs=[login_email, login_pwd],
                        outputs=[user_state, login_msg, user_info, auth_area, user_area,
                                 work_tabs, mon_table])
        logout_btn.click(handle_logout, inputs=[user_state],
                         outputs=[user_state, login_msg, user_info, auth_area, user_area,
                                  work_tabs, mon_table, conf_group])

        # Скачивание — minimal: один индикатор прогресса вместо оверлея на каждом выходе
        btn.click(
            handle_single,
            inputs=[inp, user_state],
            outputs=[preview_md, preview_df, dl_xlsx, dl_csv, dl_json],
            show_progress="minimal",
        )

        # Пакетная обработка
        batch_btn.click(handle_batch, inputs=[batch_file, user_state],
                        outputs=[batch_msg, batch_out], show_progress="minimal")

        # Мониторинг
        mon_add.click(handle_monitor_add, inputs=[mon_url, mon_interval, user_state],
                      outputs=[mon_status])
        mon_refresh.click(handle_monitor_list, inputs=[user_state], outputs=[mon_table])
        demo.load(handle_monitor_list, inputs=[user_state], outputs=[mon_table])

    return demo


if __name__ == "__main__":
    ui = build_ui()
    ui.launch(server_name="127.0.0.1", server_port=7860)
