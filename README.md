<div align="center">

# 🗺 Yandex Reviews Scraper

**Скачивает все отзывы с Яндекс.Карт — по короткой `yandex.ru/maps/-/XXX` или полной ссылке. API + человечный браузер, 222–600 отзывов проверено**

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Playwright](https://img.shields.io/badge/playwright-1.45-green.svg)](https://playwright.dev)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com)

[Быстрый старт](#-быстрый-старт) • [Как пользоваться](#-как-пользоваться) • [Конфигурация](#️-конфигурация) • [Архитектура](#-архитектура)

</div>

---

## 📖 Обзор

**Yandex Reviews Scraper** решает простую задачу: по ссылке на магазин в Яндекс.Картах скачать **все** отзывы с полной метаданной — автор, рейтинг, дата, текст, фото, ответ владельца, лайки, verified.

**Проблема:** Яндекс.Карты — SPA (Single Page Application). Обычный `requests` возвращает пустой HTML, отзывы подгружаются через API `fetchReviews?page=1..35&pageSize=50` только после скролла. Плюс антибот: капча, `limited`, блокировка по User-Agent.

**Решение в этом проекте:** Playwright (headless Chromium) + перехват API + человечный скролл + антибот-защита. Проверено на реальных магазинах Новосибирска.

**Для кого:** категорийные менеджеры, исследователи, аналитики — кому нужен полный срез отзывов конкурента или своей сети без ручного копирования.

> Проверено 2026-08-24: `Магнит 222` (`CTwsUYyk` oid 1659941740, 5 стр. API) и `Добрянка 600/1750` (`CTwDJ0if` oid 1275165507, 12 стр. API, лимит `by_relevance_org` 600, сеть 35 стр.)

## ✨ Что умеет (проверено)

- **222 для Магнита** — 5 страниц API `50×4+22`, `Олег 2×get-altay` (19996834/.../S), `Валерия 1×`, `Сергей 2019-12-01` + ответ `2025-06-01`, без `Ещё`
- **600 для Добрянки** — 12 страниц API, `h1` + `Добрянка`, `1750` — сеть (35 страниц, `by_relevance_org` лимит 600)
- **Фото — get-altay** (`S` 68px, `get-yapic/islands-68` — аватарки отфильтрованы), `background-image` + `data-srcset`
- **Полный текст** — клик `Ещё`/`…Ещё` (до 12 кликов) + мерж по `reviewId` (длинный текст побеждает)
- **API, не скролл** — `fetchReviews?businessId=...&page=1..35&pageSize=50` через `page.on('response')` + человечный `scroll__container` (`mouse.wheel 300-700 ×3-5` + `human_mouse_move` 20-50 шагов + `hover`)
- **Антибот** — `disable-blink-features=AutomationControlled`, `webdriver=undefined`, `has_touch` рандом, `Referer: yandex.ru/maps`, паузы 2.5-4.5с + `networkidle`, капча `visible` + `innerText`, `limited` → `RateLimit 60с`
- **Кэш 24ч по oid** — `db/ya_ot.db` (`shops`/`reviews`/`monitoring`/`scrape_log`), `is_cache_fresh` — повтор без сети
- **Экспорт** — Excel 3 листа (Отзывы с фильтрами, Статистика 222/600, Магазин) + CSV utf-8-sig + JSON + `output/`

## 🎬 Demo

> Интерфейс Gradio на `http://127.0.0.1:8001` — 3 вкладки: Одиночный / Пакетный / Мониторинг.
> Превью: 5 отзывов + статистика + кнопки «Скачать Excel / CSV / JSON».

<!-- TODO: добавить скриншот
![Demo](docs/demo.png)
-->

## 🚀 Быстрый старт

```bash
# 1. Клонировать
git clone https://github.com/KrivchenkoEgor/yandex-maps-reviews-scraper.git
cd yandex-maps-reviews-scraper

# 2. Окружение (нужен Python 3.11, не 3.8)
python3.11 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3. Зависимости
pip install -r requirements.txt
playwright install chromium

# 4. Настройки
cp .env.example .env  # APP_PORT=8001 (8000 часто занят)

# 5. Запуск
./start.sh    # → http://127.0.0.1:8001 + health, лог logs/app.log
# или
python -m app.main
./stop.sh     # стоп по lsof -ti :8001
```

## 📦 Установка подробно

### macOS / Linux

```bash
/opt/homebrew/bin/python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt  # + huggingface-hub==0.26.2 для gradio 4.44.0
playwright install chromium
cp .env.example .env
```

### Windows

```powershell
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
copy .env.example .env
```

## 💻 Как пользоваться

### Одиночный

1. Скопируй **короткую** `https://yandex.ru/maps/-/CTwDJ0if` (Добрянка 600) или `https://yandex.ru/maps/-/CTwsUYyk` (Магнит 222) или полную с `poi[uri]=ymapsbm1://org?oid=...&tab=reviews`
2. Вставь → «Скачать отзывы» → жди 20-90с (5-35 страниц API, прогресс `API: 50→100→...222/600`)
3. Превью 5 + Excel 222/600 + CSV + JSON в `output/`

### Пакетный

Excel с колонкой `Ссылка` → по очереди с паузой 2-5с → `output/batch_*.xlsx` (Отзывы + Отчёты_по_магазинам)

### Мониторинг

Ссылка + интервал 24ч/168ч → `monitoring` (проверка каждые 60 мин, `reviewsRemained` из `params`, дедуп по `reviewId`)

### API

```bash
# health
curl http://127.0.0.1:8001/health

# резолв короткой ссылки
curl "http://127.0.0.1:8001/api/resolve?url=https://yandex.ru/maps/-/CTwsUYyk"

# скрейп
curl -X POST http://127.0.0.1:8001/api/scrape \
  -H "Content-Type: application/json" \
  -d '{"url":"https://yandex.ru/maps/-/CTwsUYyk"}'
```

```python
import requests
r = requests.post("http://127.0.0.1:8001/api/scrape", json={"url": "https://yandex.ru/maps/-/CTwDJ0if"})
print(r.json()["shop"]["name"], len(r.json()["reviews"]))  # Добрянка 600
```

## ⚙️ Конфигурация

Создайте `.env` из `.env.example`:

| Параметр | По умолчанию | Что значит |
|---|---|---|
| `YANDEX_MIN_DELAY_SEC` | 2 | мин пауза скролла |
| `YANDEX_MAX_DELAY_SEC` | 5 | макс пауза |
| `YANDEX_PAGE_TIMEOUT_SEC` | 30 | таймаут goto |
| `YANDEX_MAX_SCROLL_ATTEMPTS` | 5 → 50 для 1750 | скроллов `scroll__container` |
| `APP_PORT` | **8001** | 8000 часто занят `irdmi` |
| `DATABASE_PATH` | `db/ya_ot.db` | кэш 24ч `SHOP_CACHE_TTL_HOURS` |
| `OUTPUT_DIR` | `output` | Excel/CSV/JSON |
| `MAX_REVIEWS_PER_SHOP` | 10000 | лимит API (35×50) |
| `LOG_LEVEL` | INFO | DEBUG/INFO/WARNING |
| `SHOP_CACHE_TTL_HOURS` | 24 | TTL кэша магазина |

## 🏗 Архитектура

```
ya_ot/
├── app/
│   ├── main.py              # FastAPI lifespan + Gradio mount /
│   ├── yandex_scraper.py    # API fetchReviews 222/600 + scroll__container + Ещё + фото get-altay
│   ├── review_parser.py     # 7 полей + hash full_text|author|date|rating|likes|photos_len
│   ├── database.py          # aiosqlite: shops/reviews/monitoring/scrape_log, TTL 24ч, oid PK
│   ├── exporter.py          # Excel 3 листа + CSV + JSON
│   ├── anti_bot.py          # 10 UA, 7 viewport, random_delay, backoff 429/503, visible captcha
│   ├── url_resolver.py      # HEAD→GET, oid из poi[uri], tab=reviews
│   ├── config.py            # .env → os.getenv
│   ├── monitor.py           # check_all каждые 60 мин
│   └── ui.py                # Gradio 3 вкладки
├── tests/                   # pytest (review_parser, database, exporter, anti_bot) — 10 тестов
├── start.sh / stop.sh       # lsof -ti :8001, health, open
├── output/                  # Магнит_*.xlsx (222) / Добрянка_*.xlsx (600)
├── db/ya_ot.db              # 1659941740:222, 1275165507:600
├── logs/ya_ot.log + app.log
├── requirements.txt
└── .env.example
```

**Поток:** `resolve_yandex_url` → `oid` → Playwright `goto tab=reviews` → `page.on('response')` ловит `fetchReviews` → человечный скролл `scroll__container` → сбор `parse_reviews_html` → дедуп `reviewId` → кэш SQLite → экспорт.

## 🛠 Стек

FastAPI 0.115 + Gradio 4.44 + Playwright 1.45 (Chromium, `disable-blink-features`) + BeautifulSoup4 + pandas 2.2.3 + openpyxl + aiosqlite + loguru + fake-useragent

## 🤝 Contributing

Приветствуются любые предложения!

1. Форкните репозиторий
2. Создайте ветку: `git checkout -b feature/amazing-feature`
3. Сделайте коммит: `git commit -m 'Add amazing feature'`
4. Запушьте: `git push origin feature/amazing-feature`
5. Откройте Pull Request

## 📜 License

Этот проект лицензирован под [MIT License](LICENSE).

## 🔗 Контакты

- GitHub: [@KrivchenkoEgor](https://github.com/KrivchenkoEgor)
- Issues: [открыть issue](https://github.com/KrivchenkoEgor/yandex-maps-reviews-scraper/issues)

---

<div align="center">

⭐ **Если проект был полезен — поставьте звезду!**

</div>
