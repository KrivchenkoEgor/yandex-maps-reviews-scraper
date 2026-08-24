# Checkpoint — GPS текущего состояния

## ⚠️ Важно помнить
- Картинки в чате запрещены: DeepSeek их не видит, сессия упадёт необратимо
- Секреты только в `.env`, никогда не коммитить
- Паузы между запросами к Яндексу 2-5 секунд обязательны
- При появлении капчи — остановиться и спросить пользователя
- Файлы в `data/` не модифицировать (всё — в `output/`)
- При старте сессии читать этот файл первым, затем `progress.md`

## Где я сейчас
Этап 11/11 — доки обновлены, сервис 8001 живой, 222/600 проверено, 1750 сеть в работе (2026-08-24 19:15).

## Что сделано
- [2026-08-24] Исследование + .venv 3.11.15 + requirements fix + chromium + .env
- [2026-08-24 16:10] url_resolver 359 — CTwsUYyk → 1659941740 + CTwDJ0if → 1275165507 (Добрянка, не Магнит)
- [2026-08-24 16:44] anti_bot 670 — 10 UA, viewport 7, visible captcha (не captchapgrd), limited 60с, desktop UA, human mouse
- [2026-08-24 16:46] review_parser 466 — 7 полей, get-altay (не yapic), hash full_text|author|date|rating|likes|photos_len
- [2026-08-24 16:46] database 290 — 4 таблицы, TTL 24ч, oid PK (1659941740:222, 1275165507:600)
- [2026-08-24 16:46] config 74 + exporter 310 — 3 листа, 222→50034, 600→158К
- [2026-08-24 16:47] yandex_scraper 511 — API fetchReviews page1..5/35 (count 222/1750), scroll__container + mouse.wheel 300-700×3-5 + hover, Ещё 12 кликов
- [2026-08-24 16:47] monitor 145 + ui 320 + main 135 — 3 вкладки, lifespan, /health, /api/scrape, mount /
- [2026-08-24 17:15-19:00] Реально: Магнит 222 (Олег 2×altay 19996834, 324 `бумерангом... 😁`, Сергей 2019-12-01 + ответ 2025-06-01), Добрянка 600/1750 (by_relevance_org лимит 600, 35 страниц сеть), start/stop.sh (lsof -ti :8001, open)
- [2026-08-24 19:15] Доки: README (222/600/1750, 8001, API), LESSONS (222 vs 1750, Ещё, altay vs yapic, limited), progress, checkpoint

## Что делаю сейчас
Доки обновлены (README/LESSONS/progress/checkpoint). Сервис 8001 `health ok` (PID 12696+24384), кэш 222/600. Жду команды добить Добрянка 1750 (35 страниц, 90с) или тест другого магазина.

## Что осталось
- [NEXT] Добрянка 1750 (35 страниц, ranking перебор) → `output/Добрянка_1750_*.xlsx`
- [OPT] Пакетный Excel 2-3 ссылки, мониторинг 24ч
- [OPT] shop_info: рейтинг/адрес из API (сейчас h1 Добрянка, rating None)

## Важные детали
- Тестовая ссылка: `https://yandex.ru/maps/-/CTwsUYyk` → oid 1659941740 (Новосибирск, Красный пр.)
- Полная: `https://yandex.ru/maps/65/novosibirsk/?ll=82.986172%2C55.044555&mode=poi&poi%5Bpoint%5D=82.986891%2C55.044638&poi%5Buri%5D=ymapsbm1%3A%2F%2Forg%3Foid%3D1659941740&tab=reviews&z=17.95`
- Запуск: `source .venv/bin/activate && python -m app.main` → http://127.0.0.1:8000  (API: /health, /api/resolve?url=, /api/scrape)
- Логи: `logs/ya_ot.log` (ротация 10 MB, 30 дней), консоль INFO
- Вёрстка Яндекс меняется — селекторы в review_parser.py вынесены в SELECTORS с фолбэками
