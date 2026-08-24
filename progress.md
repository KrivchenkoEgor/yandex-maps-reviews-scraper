# Progress — Дневник проекта

## Окружение — исследовано 2026-08-24

### OS и железо
- **OS**: macOS 27.0 (Tahoe) Build 26A5416b, Darwin 27.0.0, arch ARM64
- **Shell**: /bin/zsh
- **Рабочая директория**: `/Users/egorkrivchenko/ya_ot`
- **Node.js**: v26.0.0, npm 11.12.1, npx 11.12.1

### Python
- **Актуальный**: `/opt/homebrew/bin/python3.11` → **Python 3.11.15** (используется в .venv)
- **.venv**: создан 2026-08-24 16:03, Python 3.11.15, pip 26.2.1
- **Установлено**: все 15 пакетов + huggingface-hub==0.26.2 (фикс для gradio 4.44.0), playwright 1.45.0, gradio 4.44.0, pandas 2.2.3 — все импорты OK
- **Playwright Chromium**: кэш `~/Library/Caches/ms-playwright/` (chromium-1124..1228), smoke `example.com` title OK
- **SQLite**: 3.53.1
- **.env**: скопирован из .env.example, 16 переменных
- **Структура**: app/ 10 модулей, data/, db/, logs/, output/ — всё на месте

## Выполненные задачи

### [2026-08-24 16:04] Установка окружения
- .venv Python 3.11.15, все зависимости, фикс huggingface-hub 0.26.2, playwright chromium OK

### [2026-08-24 16:10] app/url_resolver.py
- Разрешение коротких ссылок yandex.ru/maps/-/XXX → финальный URL + извлечение oid из poi[uri]=ymapsbm1://org?oid=... + tab=reviews
- Тест на CTwsUYyk: oid=1659941740 OK, is_short корректно, ensure_tab_reviews OK

### [2026-08-24 16:44] app/anti_bot.py
- 10 UA, get_random_user_agent (fake_useragent + fallback), get_random_viewport (7 разрешений ±50px), random_delay 2-5с + async, exponential_backoff, retry_with_backoff, is_captcha_page (keywords + селекторы), handle_captcha (30с пауза → CaptchaDetectedError), human_mouse_move, AntiBotConfig из .env, 3 исключения. Demo OK.

### [2026-08-24 16:46] app/review_parser.py
- SELECTORS с фолбэками, парсинг 7 полей + shop info, parse_yandex_date ("3 дня назад","вчера","15 марта 2024", "15.03.2024"), тесты на фейковом HTML (rating 5, фото, owner_response, likes 12, verified) OK. Найдено 2 карточки в тесте (фильтр пустых — норма).

### [2026-08-24 16:46] app/database.py
- Схема shops/reviews/monitoring/scrape_log + индексы, класс Database (aiosqlite async), upsert_shop, get_shop, is_cache_fresh (24ч), upsert_reviews (дедуп по review_id), get_reviews (распаковка JSON), monitoring CRUD, scrape_log. Demo: кэш fresh, дедуп 1 из 2 OK.

### [2026-08-24 16:46] app/config.py + app/exporter.py
- config: чтение всех YANDEX_*, DATABASE_PATH, APP_*, OUTPUT_DIR и т.д. из .env через os.getenv
- exporter: Excel 3 листа (Отзывы с фильтрами/ширинами/заморозкой, Статистика распределение+тренд, Магазин) + CSV utf-8-sig + JSON {shop, reviews, stats}. Demo 3 отзыва → 3 файла + сверка 3/3 OK.

### [2026-08-24 16:47] app/yandex_scraper.py
- Playwright async, launch с UA/viewport рандом, resolve_yandex_url → tab=reviews, ожидание reviews/shop селекторов, бесконечный скролл (window.scrollTo / контейнер + пауза из AntiBotConfig, проверка капчи каждую итерацию, сбор через parse_reviews_html, дедуп по review_id, лимит MAX_REVIEWS_PER_SHOP). Обёртка scrape_sync. py_compile OK.

### [2026-08-24 16:47] app/monitor.py
- check_shop (сравнение review_id), check_all_monitors (интервал из БД vs MONITOR_DEFAULT_INTERVAL_HOURS), monitor_loop (каждые MONITOR_CHECK_INTERVAL_MINUTES с паузой anti_bot). Demo list_monitors OK.

### [2026-08-24 16:48] app/ui.py + app/main.py
- ui: Gradio Blocks 3 вкладки (Одиночный: прогресс+превью 5+3 файла, Пакетный: Excel/CSV колонка Ссылка → batch_*.xlsx, Мониторинг: add/list). _scrape_one с кэшем 24ч из AGENTS.md.
- main: FastAPI lifespan (init DB + запуск monitor_loop), /health, /api/resolve, /api/scrape, mount Gradio на "/", логи в logs/ya_ot.log. Запуск `python -m app.main` или `uvicorn app.main:app --host 127.0.0.1 --port 8000`. FULL_COMPILE_OK + ALL_IMPORTS_OK.

### [2026-08-24 16:49] Сквозная проверка
- ALL_SYNC_CHECKS_OK + INTEGRATION_FIX_OK

### [2026-08-24 17:15-18:20] Реальные прогоны + API
- Магнит CTwsUYyk 222: 5 страниц API `50×4+22`, `count 222 totalPages 5`, Олег 2×get-altay (19996834), полный 327 `бумерангом... 😁` без Ещё, Сергей 2019-12-01 + ответ 2025-06-01 — `output/Магнит_20260824_1816.xlsx` 50К
- Добрянка CTwDJ0if 600/1750: `count 1750 totalPages 35`, `by_relevance_org` лимит 600 (12×50) → 35 скроллов `scroll__container` + `mouse.wheel 300-700 ×3-5` + `hover`, `limited` 158 байт → 60с кулдаун, `desktop UA` (iPhone блочит), `h1 Добрянка` (было Магнит/Магазин)
- Фото: `get-altay/.../S` (review) vs `get-yapic/islands-68` (аватарка) — теперь строго `get-altay`
- Запуск: `APP_PORT 8001` (8000 занят), `start.sh`/`stop.sh` (`lsof -ti :8001`, `health`, `open`), сервис 8001 `health ok`

## Текущий статус
✅ 222 Магнит полный, 600 Добрянка (12 страниц), 1750 сеть — 35 страниц в работе, доки README/LESSONS обновлены, сервис 8001 `health ok` (PID 12696/24384)
⏭ Следующий: добить Добрянка 1750 (35 страниц, `ranking` перебор) → `output/Добрянка_1750_*.xlsx`

## Структура проекта
```
ya_ot/
├── app/__init__.py, config.py, url_resolver.py, anti_bot.py, review_parser.py,
│   database.py, exporter.py, yandex_scraper.py, monitor.py, ui.py, main.py
├── .venv/ (Python 3.11.15)
├── .env (из .env.example)
├── db/ (ya_ot.db создаётся при первом запуске)
├── output/ (экспорты)
├── logs/ya_ot.log
└── requirements.txt (+ huggingface-hub==0.26.2)
```

## Замечания
- Использовать только .venv Python 3.11.15 (`source .venv/bin/activate`), дефолтный 3.8 не подходит.
- Уважение к Яндексу: не более 1 магазина одновременно, паузы 2-5с соблюдаются везде (anti_bot + yandex_scraper + monitor + ui batch).
- При капче — пауза 30с → если не ушла → CaptchaDetectedError с просьбой к пользователю (по AGENTS.md).
