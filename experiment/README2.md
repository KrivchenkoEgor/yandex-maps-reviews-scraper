# Experiment Part 2: Поиск альтернативных эндпоинтов и комбинация

**Цель:** найти другой эндпоинт или комбинацию, чтобы получить 835, не ломая `main`.

## 1. Снифф всех эндпоинтов (sniff_all_endpoints.py)

Перехватили **все** `response` где `business|review|rating|comment|org|maps/api`:

- Единственный review-эндпоинт: `GET https://yandex.ru/maps/api/business/fetchReviews?ajax=1&businessId=1033677441&csrfToken=...&locale=ru_RU&page=1..17&pageSize=50&ranking=by_relevance_org`
- Параметры ответа: `data.params = {offset, limit, count:835, loadedReviewsCount, page, totalPages:17, reviewsRemained}`
- Другие `api/business/*`: `fetchReviewPollData` (опросы, не отзывы), `checkAvailability` (жалобы) — не дают отзывы.
- Вывод: **альтернативного эндпоинта для отзывов нет** в этом флоу. Всё через `fetchReviews`.

## 2. JS bundle (maps.yastatic.net/.../chunks/reviews/d4cf683cb90dc7b089e8.yandex.ru.js)

```bash
curl -s .../d4cf683cb90dc7b089e8.yandex.ru.js | grep -o "by_[a-z_]*" | sort -u
# by_aspect_tone_asc
# by_aspect_tone_desc
# by_rating_asc
# by_rating_desc
# by_relevance_org
# by_time
```

Вызов: `fetchReviews({businessId:e.id, page:n, pageSize:t, reqId:e.requestId, aspectId:l.selectedAspect, ranking:s()})`

- `ranking` зависит от `selectedAspect ? b : _`:
  - без аспекта: `by_relevance_org`, `by_time`, `by_rating_*` (показаны в UI "По релевантности / По времени / По рейтингу")
  - с аспектом: `by_aspect_tone_asc/desc`
- На практике: для `oid=1033677441` валиден **только** `by_relevance_org` без аспекта и `by_aspect_tone_desc` с `aspectId`. Остальные `ranking` → `400 Bad Request` (проверено `sniff_rankings.py` страниц 1 и 13, pageSize 10/20/100 — тоже 400).
- HTML-аспекты: 4 штуки, без `id` в `data.aspects` (только `text`/`count`), но при клике UI подставляет `aspectId`:
  - Выбор товаров → 3502044705 (267)
  - Еда → 3502043738 (261)
  - Качество товаров → 3502044673 (196)
  - Скидки и акции → 3502044260 (153)
  - Суммарно 877, пересечение большое.

## 3. Комбинация (test_aspect_pagination.py / test_aspect_scroll.py)

**Методика:** скролл общего списка (12 страниц → 600), затем по очереди клик по каждому аспекту + скролл 6-7 страниц, дедуп по `reviewId`.

```
general (by_relevance_org 12×50): 600
+ Выбор товаров (267, 6 pages): +24 → 624
+ Еда (261, 6 pages): +6 → 630
+ Качество товаров (196, 4 pages): +3 → 633
+ Скидки и акции (153, 4 pages): +26 → 659
```

Итого **659 уникальных** (`835-659=176` не покрыто). DOM после всех скроллов: `div.business-review-view` максимум 300 одновременно (виртуализация), так что DOM не источник.

Проверка остальных фильтров:
- `by_time`, `by_rating_asc/desc` без аспекта → `400`
- `offset=600`, `page=13` без аспекта → `400` / `got=0`
- Клики по рейтингу/времени в UI (find_rating_filter.py) — попап "По релевантности" найден, но другие ranking не принимаются сервером для этого `businessId`.

## 4. Вывод по 835

- Хард-лимит `fetchReviews` для `by_relevance_org`: **12×50=600**. `totalPages=17` и `count=835` — декоративные, сервер не отдаёт страницы 13-17 ни с каким валидным `ranking`/`pageSize`/`offset`.
- Комбинация через `aspectId` + `by_aspect_tone_desc` даёт **+59** (600→659), но не 835. 176 отзывов не имеют аспекта из топ-4 и не попадают ни в один фильтр из доступных в UI.
- Других эндпоинтов (`maps/api/*`) с отзывами нет. `pageSize` ≠50 и `ranking` ≠`by_relevance_org`/`by_aspect_tone_desc` → `400`.

## 5. Рекомендация без ломки main

- Оставить `main` с `total_reviews = max(api_total_count)=835` и `uniq=600` (или 659 если включать аспекты). Сейчас `yandex_scraper.py` уже делает `total_reviews = api_total or shop_info.total` → честно показывает `835 | 600`.
- Если нужен максимум без регресса (222/600 не ломаем), можно в эксперименте включить аспект-догрузку опционально (флаг `ENABLE_ASPECT_COMBINE=1`), даст 659 для Быстроном с ценой +~20 сек и +4×6 запросов. Полные 835 недостижимы через публичный `fetchReviews`.
- Альтернатива за рамками: авторизованный API Яндекс.Бизнеса или парсинг `https://yandex.ru/maps-reviews` с пагинацией по `offset` курсору (не найден в текущем фронте) — требует отдельного реверс-инжиниринга с логином.

## Артефакты

- `experiment/sniff_all_endpoints.py` → `/tmp/sniff_all.json` (13× fetchReviews + polls)
- `experiment/sniff_rankings.py` → ranking/pageSize брут
- `experiment/test_aspects.py` → aspectId маппинг
- `experiment/test_aspect_scroll.py` → 659 результат
- Этот файл — итог. Ветка `experiment/bystronom-835-full` не мерджится в `main` без решения по 176.
