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
from app.database import get_db
from app.exporter import export_csv, export_excel, export_json, excel_date_sort_key, to_excel_date
from app.url_resolver import resolve_yandex_url
from app.yandex_scraper import YandexScraper

# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------

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

async def _scrape_one(url: str, progress_cb=None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Скачать один магазин с кэшем 24ч (логика из AGENTS.md)."""
    url = url.strip()
    if not url:
        raise ValueError("Вставьте ссылку на магазин с Яндекс.Карт")

    resolved = await asyncio.to_thread(resolve_yandex_url, url)
    oid = resolved["oid"]
    if not oid:
        raise ValueError("Не удалось извлечь OID. Убедитесь что ссылка ведёт на карточку магазина (с poi).")

    db = get_db()
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


def handle_single(url: str, progress=gr.Progress(track_tqdm=True)):
    """Gradio-обработчик одиночного режима (синхронная обёртка)."""
    progress(0, desc="Разрешаю ссылку...")
    try:
        shop, reviews = asyncio.run(_scrape_one(url, progress_cb=lambda m: progress(0.5, desc=m)))
    except Exception as e:
        logger.error(f"Ошибка одиночного: {e}")
        return (
            f"❌ Ошибка: {e}",
            gr.update(value=None, visible=False),
            gr.update(value=None, visible=False),
            gr.update(value=None, visible=False),
            gr.update(value=None, visible=False),
        )

    preview = _fmt_preview(shop, reviews)
    df_preview = pd.DataFrame([
        {"Автор": r.get("author"), "Оценка": r.get("rating"), "Дата": r.get("date"), "Текст": (r.get("text") or "")[:120], "👍": r.get("likes",0), "👎": r.get("dislikes",0)}
        for r in reviews[:5]
    ])

    try:
        p_xlsx = export_excel(shop, reviews)
        p_csv = export_csv(shop, reviews)
        p_json = export_json(shop, reviews)
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


def handle_batch(file, progress=gr.Progress(track_tqdm=True)):
    """Пакетный: Excel со списком ссылок (колонка 'Ссылка' или первая колонка)."""
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
            shop, reviews = asyncio.run(_scrape_one(u))
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
    out_dir = Path(config.OUTPUT_DIR)
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


def handle_monitor_add(url: str, interval_hours: int):
    """Добавить магазин в мониторинг."""
    if not url.strip():
        return "Вставьте ссылку"
    try:
        resolved = resolve_yandex_url(url.strip())
        oid = resolved["oid"]
        if not oid:
            return "❌ Не удалось извлечь OID из ссылки"
        db = get_db()
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


def handle_monitor_list():
    """Список подписок."""
    db = get_db()
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

/* --- Табы: большая белая карточка + сегмент-контрол --- */
.tabs {
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
.tabs .form { background: transparent !important; border: none !important; box-shadow: none !important; }
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

/* --- Таблицы (превью, подписки) --- */
.tabs table { border-collapse: collapse !important; font-size: 15px !important; }
.tabs th {
  font-weight: 600 !important; color: #6e6e73 !important; font-size: 12px !important;
  text-transform: uppercase !important; letter-spacing: 0.04em !important;
  border-bottom: 1px solid #e8e8ed !important; background: transparent !important;
}
.tabs td { border-bottom: 1px solid #f0f0f2 !important; color: #1d1d1f !important; }

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
        gr.Markdown(
            "# Yandex Reviews Scraper\n"
            "Отзывы с Яндекс.Карт в один клик: вставьте ссылку — получите Excel, CSV или JSON.",
            elem_classes=["app-hero"],
        )

        with gr.Tab("Скачивание"):
            inp = gr.Textbox(label="Ссылка на магазин", placeholder="https://yandex.ru/maps/-/CTwsUYyk  или полная с poi[uri]=ymapsbm1://org?oid=...", lines=2)
            btn = gr.Button("Скачать отзывы", variant="primary")
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
            gr.Markdown("Кэш 24 часа: повторный запрос в течение суток вернёт данные из базы без обращения к Яндексу. При капче сервис попросит подождать.")

            # minimal — один компактный индикатор прогресса вместо оверлея
            # на каждом выходном компоненте (иначе прогресс-бар дублируется)
            btn.click(
                handle_single,
                inputs=[inp],
                outputs=[preview_md, preview_df, dl_xlsx, dl_csv, dl_json],
                show_progress="minimal",
            )

        with gr.Tab("Пакетная обработка"):
            gr.Markdown("Загрузите Excel или CSV с колонкой **Ссылка** (или первой колонкой). Магазины обрабатываются по очереди, с паузой 2–5 секунд.")
            batch_file = gr.File(label="Excel/CSV со ссылками", file_types=[".xlsx",".xls",".csv"])
            batch_btn = gr.Button("Запустить обработку", variant="primary")
            batch_msg = gr.Markdown()
            batch_out = gr.File(label="Общий отчёт (Excel)", visible=False)

            batch_btn.click(handle_batch, inputs=[batch_file], outputs=[batch_msg, batch_out], show_progress="minimal")

        with gr.Tab("Мониторинг"):
            gr.Markdown("Подписка на магазин: проверка новых отзывов по интервалу — день или неделя. Новое попадает в базу, дубликаты по review_id игнорируются.")
            with gr.Row():
                mon_url = gr.Textbox(label="Ссылка на магазин", placeholder="https://yandex.ru/maps/-/CTwsUYyk")
                mon_interval = gr.Radio(choices=[("День (24ч)", 24), ("Неделя (168ч)", 168)], value=24, label="Интервал")
                mon_add = gr.Button("Добавить в мониторинг", variant="primary")
            mon_status = gr.Markdown()
            mon_table = gr.Dataframe(
                label="Подписки", interactive=False,
                headers=["ID", "Магазин", "OID", "Интервал ч", "Последняя проверка", "Активна"],
            )
            mon_refresh = gr.Button("Обновить список", variant="secondary")

            mon_add.click(handle_monitor_add, inputs=[mon_url, mon_interval], outputs=[mon_status])
            mon_refresh.click(handle_monitor_list, outputs=[mon_table])
            demo.load(handle_monitor_list, outputs=[mon_table])

        gr.Markdown(
            f"<div>"
            f"v{__version__} • <a href='{__git_url__}' target='_blank'>GitHub</a> • "
            f"Yandex Reviews Scraper"
            f"</div>",
            elem_classes=["app-footer"],
        )

    return demo


if __name__ == "__main__":
    ui = build_ui()
    ui.launch(server_name="127.0.0.1", server_port=7860)
