"""
ui — Gradio-интерфейс для Yandex Reviews Scraper.

Три вкладки из AGENTS.md:
- Одиночный: поле ссылки → «Скачать отзывы» → превью + кнопки экспорта
- Пакетный: загрузка Excel со списком ссылок → прогресс → общий отчёт
- Мониторинг: список подписок, новые отзывы, добавление в мониторинг

Делаем максимально простой, без JS, в стиле "для не-программиста" — всё по-русски.
"""

import asyncio
from pathlib import Path
from typing import Any

import gradio as gr
import pandas as pd
from loguru import logger

from app import __git_url__, __version__, config
from app.database import get_db
from app.exporter import export_csv, export_excel, export_json
from app.url_resolver import resolve_yandex_url
from app.yandex_scraper import YandexScraper

# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------

def _fmt_preview(shop: dict[str, Any], reviews: list[dict[str, Any]]) -> str:
    if not shop:
        return "Нет данных"
    lines = [
        f"**{shop.get('name','—')}**",
        f"Адрес: {shop.get('address','—')}",
        f"Рейтинг: {shop.get('rating','—')} | Всего отзывов: {shop.get('total_reviews','—')} | OID: {shop.get('oid','—')}",
        f"Скачано отзывов: {len(reviews)}",
        "",
        "Первые 5 отзывов:",
    ]
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

        # Сохраняем в БД
        await db.upsert_shop(shop)
        await db.upsert_reviews(oid, reviews)
        await db.log_finish(log_id, reviews_found=len(reviews), status="ok")
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
            None,
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
        df_preview,
        gr.update(value=str(p_xlsx) if p_xlsx else None, visible=True),
        gr.update(value=str(p_csv) if p_csv else None, visible=True),
        gr.update(value=str(p_json) if p_json else None, visible=True),
    )


def handle_batch(file, progress=gr.Progress(track_tqdm=True)):
    """Пакетный: Excel со списком ссылок (колонка 'Ссылка' или первая колонка)."""
    if file is None:
        return "Загрузите Excel/CSV со списком ссылок", None

    path = Path(file.name) if hasattr(file, "name") else Path(file)
    try:
        if path.suffix.lower() in (".xlsx", ".xls"):
            df = pd.read_excel(path, dtype=str)
        else:
            df = pd.read_csv(path, dtype=str)
    except Exception as e:
        return f"❌ Не удалось прочитать файл: {e}", None

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
        return "❌ В файле не найдено ссылок (ищу колонку 'Ссылка')", None

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
        # Пауза 2-5с между магазинами (уважение к Яндексу)
        import time, random
        time.sleep(random.uniform(2, 3))

    # Общий отчёт Excel
    df_out = pd.DataFrame(all_reviews)
    df_shops = pd.DataFrame(shop_rows)
    out_dir = Path(config.OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"batch_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.xlsx"
    with pd.ExcelWriter(out_path, engine="openpyxl") as w:
        df_out.to_excel(w, sheet_name="Отзывы", index=False)
        df_shops.to_excel(w, sheet_name="Отчёты_по_магазинам", index=False)

    msg = f"Готово: {len(urls)} ссылок, {len(all_reviews)} отзывов. Ошибок: {len(errors)}"
    if errors:
        msg += "\n" + "\n".join(errors[:5])
    return msg, str(out_path)


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
            # Убедимся что магазин есть в БД (скачаем шапку если нет)
            shop = await db.get_shop(oid)
            if not shop:
                scraper = YandexScraper(headless=True)
                result = await scraper.scrape(url.strip())
                await db.upsert_shop(result["shop"])
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
# Сборка Gradio Blocks
# ---------------------------------------------------------------------------

def build_ui() -> gr.Blocks:
    with gr.Blocks(title="Yandex Reviews Scraper", theme=gr.themes.Soft()) as demo:
        gr.Markdown("# 🗺 Yandex Reviews Scraper\nСкачивание отзывов с Яндекс.Карт — вставьте ссылку, получите Excel/CSV/JSON. Паузы 2-5с, антибот, кэш 24ч.")

        with gr.Tab("Одиночный"):
            inp = gr.Textbox(label="Ссылка на магазин", placeholder="https://yandex.ru/maps/-/CTwsUYyk  или полная с poi[uri]=ymapsbm1://org?oid=...", lines=2)
            btn = gr.Button("Скачать отзывы", variant="primary")
            preview_md = gr.Markdown()
            preview_df = gr.Dataframe(label="Превью (5 отзывов)", interactive=False)
            with gr.Row():
                dl_xlsx = gr.File(label="Excel (3 листа)", visible=False)
                dl_csv = gr.File(label="CSV", visible=False)
                dl_json = gr.File(label="JSON", visible=False)
            gr.Markdown("💡 Кэш 24ч: повторный запрос в течение суток вернёт данные из БД без обращения к Яндексу. При капче — сервис попросит подождать.")

            btn.click(
                handle_single,
                inputs=[inp],
                outputs=[preview_md, preview_df, dl_xlsx, dl_csv, dl_json],
            )

        with gr.Tab("Пакетный"):
            gr.Markdown("Загрузите Excel/CSV с колонкой **Ссылка** (или первая колонка). Сервис обработает по очереди с паузой 2-5с.")
            batch_file = gr.File(label="Excel/CSV со ссылками", file_types=[".xlsx",".xls",".csv"])
            batch_btn = gr.Button("Запустить пакетную обработку", variant="primary")
            batch_msg = gr.Markdown()
            batch_out = gr.File(label="Общий отчёт (Excel)")

            batch_btn.click(handle_batch, inputs=[batch_file], outputs=[batch_msg, batch_out])

        with gr.Tab("Мониторинг"):
            gr.Markdown("Подписка на магазин: проверка новых отзывов по интервалу (день/неделя). Новые отзывы добавляются в БД, дубликаты по review_id игнорируются.")
            with gr.Row():
                mon_url = gr.Textbox(label="Ссылка на магазин", placeholder="https://yandex.ru/maps/-/CTwsUYyk")
                mon_interval = gr.Radio(choices=[("День (24ч)", 24), ("Неделя (168ч)", 168)], value=24, label="Интервал")
                mon_add = gr.Button("Добавить в мониторинг", variant="secondary")
            mon_status = gr.Markdown()
            mon_table = gr.Dataframe(label="Подписки", interactive=False)
            mon_refresh = gr.Button("Обновить список")

            mon_add.click(handle_monitor_add, inputs=[mon_url, mon_interval], outputs=[mon_status])
            mon_refresh.click(handle_monitor_list, outputs=[mon_table])
            demo.load(handle_monitor_list, outputs=[mon_table])

        gr.Markdown(
            f"<div style='text-align:center; margin-top:20px; opacity:0.7; font-size:13px'>"
            f"v{__version__} • <a href='{__git_url__}' target='_blank'>GitHub</a> • "
            f"Yandex Reviews Scraper</div>"
        )

    return demo


if __name__ == "__main__":
    ui = build_ui()
    ui.launch(server_name="127.0.0.1", server_port=7860)
