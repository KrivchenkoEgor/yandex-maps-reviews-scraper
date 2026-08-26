# Experiment: Быстроном 835 → 600

**Цель:** получить 835 отзывов вместо 600, не ломая `main` (ветка `experiment/bystronom-835-full`).

**Окружение:** Playwright Chromium headless, `businessId=1033677441`, `ranking=by_relevance_org`, `pageSize=50`

## Факты из сниффа (`sniff_fetch.py` → `/tmp/sniff_result.json`)

- API URL: `https://yandex.ru/maps/api/business/fetchReviews?ajax=1&businessId=1033677441&csrfToken=...&locale=ru_RU&page=1..13&pageSize=50&ranking=by_relevance_org&reqId=...&s=...&sessionId=...`
- `page=1..12` → `status 200`, `total=835`, `got=50` каждый → `600` суммарно
- `page=13` → `status 200` с пустым `reviews: []` (при скролле) или `400 Bad Request` (при прямом `fetch` с тем же `csrfToken`) → `got=0`, `total=None`
- DOM после 5 скроллов: `document.querySelectorAll('div.business-review-view').length = 300` (виртуализация, не 600/835)

## Перебор ranking / pageSize (`sniff_rankings.py`)

- В HTML найдено: `by_relevance_org`, `by_credit_card`, `by_family` — только `by_relevance_org` валиден.
- Все кандидаты `by_time`, `by_rating`, `by_time_desc/asc`, `by_rating_desc/asc`, `by_relevance`, `recent`, `time`, `rating`, `date`, `newest`, `oldest` → `400 Bad Request` на `page=1` и `page=13`.
- `pageSize` ≠ 50 → `400 Bad Request` (10,20,100 не работают). Только `50` валиден.
- Без `ranking` → `400`.

## Проверка offset / альтернативных параметров (`test_offset.py`)

- `offset=600`, `page=13`, `pageSize=100`, удаление `ranking` — всё `400`.
- Прямой `fetch` с `page=13` → `400`, но скролл-триггер с тем же URL → `200` с `got=0` (разные заголовки/куки, но результат одинаков — данных нет).

## Вывод

- **Лимит 600 — серверный, жёсткий.** Яндекс отдаёт `total=835` в `params.count`/`pagination.total`, но `fetchReviews` с `ranking=by_relevance_org` возвращает максимум 12 страниц ×50. Это не `MAX_REVIEWS_PER_SHOP` (10000) и не `YANDEX_MAX_SCROLL_ATTEMPTS` (5→50) — это `api/business/fetchReviews` пагинация Яндекса.
- Попытка обойти сменой `ranking`/`pageSize`/`offset` невозможна: API валидирует строго `ranking=by_relevance_org` + `pageSize=50` + `page=1..12`.
- DOM тоже не даёт больше: после скролла в DOM лишь 300 карточек (виртуализация), API надёжнее на 600.
- Остальные 235 отзывов (835-600) не экспонируются публично — либо скрыты модерацией, либо требуют другой эндпоинт (не найден в `api/business/*`).

## Способы, которые *пробовали* (все дали `Bad Request` или `got=0`)

1. Смена `ranking` на `by_time` / `by_rating` / etc. → `400`
2. `pageSize` 10/20/100 → `400`
3. `offset=600` → `400`
4. `page=13` с любым валидным `ranking` → `400` (прямой) / `got=0` (скролл)
5. Удаление `ranking` → `400`

## Что *не* трогали (чтобы не ломать main)

- `app/yandex_scraper.py`, `app/config.py` — не меняли в этой ветке (только снифф-скрипты в `experiment/`).
- `main` остаётся с `total_reviews = max(api_total_count)` (=835) и `uniq=600` → UI: `Всего отзывов: 835 | Скачано отзывов: 600` — честное отображение лимита.

## Рекомендация для prod (если нужно показывать 835)

1. **Оставить как есть:** считать `600` ожидаемым максимумом для `fetchReviews`. В `ui.py` показывать `Скачано 600 из 835 (лимит API Яндекса 12×50)`.
2. **Если очень нужно >600:** нужен другой источник — либо `yandex.ru/maps-reviews` не `fetchReviews`, либо парсить `DOM` + `Показать ещё` пагинацию + `offset` cursor (не найден), либо официальный API Я.Бизнеса. Требует отдельного реверс-инжиниринга с авторизацией — за рамками текущего `fetchReviews`.

## Файлы эксперимента

- `experiment/sniff_fetch.py` — логирует все `fetchReviews` URL, `page`, `ranking`, `total`, `got`
- `experiment/sniff_rankings.py` — брут `ranking` × `page` + `pageSize`
- `experiment/test_offset.py` — `offset`/`no-ranking` тесты + DOM count

Запуск: `.venv/bin/python experiment/sniff_fetch.py` → `/tmp/sniff_result.json`
