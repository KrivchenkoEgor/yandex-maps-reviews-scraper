"""
yandex_scraper — Playwright-парсер Яндекс.Карт (SPA) для сбора всех отзывов.

Стратегия из AGENTS.md:
3) Открытие страницы с tab=reviews в Chromium (UA/viewport рандом)
4) Бесконечный скролл: scroll → пауза 2-5с → проверка новых → стоп после 3 пустых итераций
5) Извлечение метаданных через review_parser (BeautifulSoup)
6) Сохранение — отдать наружу, запись в БД делает вызывающий код.
"""

import asyncio
import random
from typing import Any, Callable, Optional

from loguru import logger
from playwright.async_api import async_playwright, Page, Browser, BrowserContext

from app import config
from app.anti_bot import (
    AntiBotConfig,
    CaptchaDetectedError,
    async_random_delay,
    get_random_user_agent,
    get_random_viewport,
    is_captcha_page_playwright,
    handle_captcha,
    human_mouse_move,
)
from app.review_parser import parse_reviews_html, parse_shop_info_html
from app.url_resolver import resolve_yandex_url

# ---------------------------------------------------------------------------
# Константы / селекторы для ожидания загрузки
# ---------------------------------------------------------------------------
# Набор селекторов, любой из которых сигнализирует что блок отзывов загрузился
REVIEWS_READY_SELECTORS = [
    "div.business-review-view",
    "[data-testid='review-card']",
    "div[class*='business-reviews']",
    "div[class*='reviews-view']",
    "div[class*='business-review']",
]

SHOP_READY_SELECTORS = [
    "h1",
    "[data-testid='business-title']",
    "div[class*='business-card']",
]


class YandexScraper:
    """Скрапер отзывов с Яндекс.Карт через Playwright."""

    def __init__(self, headless: bool = True, on_progress: Optional[Callable[[str], None]] = None):
        self.headless = headless
        self.on_progress = on_progress
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

    def _progress(self, msg: str) -> None:
        logger.info(msg)
        if self.on_progress:
            try:
                self.on_progress(msg)
            except Exception:
                pass

    # -- lifecycle --

    async def _launch(self) -> tuple[Browser, BrowserContext]:
        """Запустить Chromium по-человечески: скрываем automation, рандом viewport/UA, часовой пояс."""
        self._playwright = await async_playwright().start()
        ua = get_random_user_agent()
        vw = get_random_viewport()
        logger.info(f"Запуск Chromium: UA={ua[:60]}..., viewport={vw}, headless={self.headless}")
        browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            user_agent=ua,
            viewport={"width": vw[0], "height": vw[1]},
            locale="ru-RU",
            timezone_id="Asia/Novosibirsk",
            has_touch=random.choice([True, False]),
            is_mobile=False,
            color_scheme="light",
        )
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined}); window.chrome = {runtime:{}};")
        await context.set_extra_http_headers({
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Referer": "https://yandex.ru/maps/",
        })
        self._browser = browser
        self._context = context
        return browser, context

    async def _close(self) -> None:
        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass
        try:
            if self._browser:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass

    # -- core --

    async def scrape(self, url: str, max_reviews: Optional[int] = None) -> dict[str, Any]:
        """
        Скачать все отзывы по ссылке на магазин.

        Args:
            url: короткая или полная ссылка Яндекс.Карт
            max_reviews: лимит отзывов (по умолчанию из config.MAX_REVIEWS_PER_SHOP)

        Returns:
            {
              "shop": {name, address, rating, total_reviews, oid, url},
              "reviews": [ {review_id, author, rating, date, text, photos, owner_response, likes, is_verified}, ... ],
              "resolved_url": str,
            }
        """
        limit = max_reviews or config.MAX_REVIEWS_PER_SHOP
        resolved = await asyncio.to_thread(resolve_yandex_url, url)
        oid = resolved["oid"]
        if not oid:
            raise ValueError(f"Не удалось извлечь oid из ссылки: {url}. Проверьте что ссылка ведёт на карточку организации.")

        target_url = resolved["resolved_url"]
        # гарантируем tab=reviews (resolve_yandex_url уже добавляет, но дублируем)
        if "tab=reviews" not in target_url:
            target_url += ("&tab=reviews" if "?" in target_url else "?tab=reviews")

        self._progress(f"OID={oid}, переход на {target_url[:100]}...")
        browser, context = await self._launch()
        page: Page = await context.new_page()
        page.set_default_timeout(config.YANDEX_PAGE_TIMEOUT_SEC * 1000)

        try:
            api_collected: list[dict[str, Any]] = []
            pending_api_tasks: list[asyncio.Task] = []
            api_total_count: list[int] = []

            async def _api_handle(resp):
                if "fetchReviews" in resp.url:
                    try:
                        j = await resp.json()
                        # capture total count for shop header (835 vs 300)
                        try:
                            data = j.get("data", {})
                            cnt = None
                            if isinstance(data.get("total"), int):
                                cnt = data["total"]
                            elif isinstance(data.get("count"), int):
                                cnt = data["count"]
                            elif isinstance(data.get("params"), dict) and isinstance(data["params"].get("count"), int):
                                cnt = data["params"]["count"]
                            elif isinstance(data.get("pagination"), dict) and isinstance(data["pagination"].get("total"), int):
                                cnt = data["pagination"]["total"]
                            if cnt:
                                api_total_count.append(int(cnt))
                        except Exception:
                            pass
                        if "data" in j and "reviews" in j["data"]:
                            for r in j["data"]["reviews"]:
                                photos = [p["urlTemplate"].replace("{size}", "XL") for p in r.get("photos", [])]
                                owner = None
                                if r.get("businessComment"):
                                    owner = {"text": r["businessComment"]["text"], "date": r["businessComment"].get("updatedTime", "")[:10]}
                                # Extract real verified status from API response
                                # Check common fields: isVerified, verified, badges array
                                is_verified = False
                                if r.get("isVerified") is True:
                                    is_verified = True
                                elif r.get("verified") is True:
                                    is_verified = True
                                elif isinstance(r.get("badges"), list):
                                    for badge in r["badges"]:
                                        if isinstance(badge, dict) and badge.get("type") in ("verified", "isVerified", "verified_review"):
                                            is_verified = True
                                            break
                                        if isinstance(badge, str) and badge.lower() in ("verified", "isverified", "verified_review"):
                                            is_verified = True
                                            break
                                api_collected.append({
                                    "review_id": r.get("reviewId"),
                                    "author": r.get("author", {}).get("name", "Аноним"),
                                    "rating": r.get("rating"),
                                    "date": r.get("updatedTime", "")[:10],
                                    "raw_date": r.get("updatedTime", ""),
                                    "text": r.get("text", ""),
                                    "photos": photos,
                                    "owner_response": owner,
                                    "likes": r.get("reactions", {}).get("likes", 0),
                                    "is_verified": is_verified,
                                })
                    except Exception:
                        pass

            page.on("response", lambda r: pending_api_tasks.append(asyncio.create_task(_api_handle(r))))

            await asyncio.sleep(random.uniform(1.0, 2.5))
            try:
                await page.goto(target_url, wait_until="domcontentloaded", timeout=config.YANDEX_PAGE_TIMEOUT_SEC * 1000)
            except Exception as e:
                if "Timeout" in str(e):
                    logger.warning(f"goto timeout {config.YANDEX_PAGE_TIMEOUT_SEC}s для {target_url[:80]} — ретрай с 'commit' и паузой 3с")
                    await asyncio.sleep(3)
                    await page.goto(target_url, wait_until="commit", timeout=(config.YANDEX_PAGE_TIMEOUT_SEC + 15) * 1000)
                else:
                    raise
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception: pass
            await async_random_delay(2.5, 4.5)
            # Проверка на "limited" с ретраями (60с кулдаун, до 2 повторов)
            max_limited_retries = 2
            for limited_attempt in range(max_limited_retries + 1):
                content = await page.content()
                if "limited" not in content.lower():
                    break
                if limited_attempt < max_limited_retries:
                    logger.warning(
                        f"Яндекс вернул заглушку 'limited' — лимит запросов, пауза 60с "
                        f"(попытка {limited_attempt + 1}/{max_limited_retries})"
                    )
                    await asyncio.sleep(60)
                    # Перезагружаем страницу после паузы
                    await page.reload(wait_until="domcontentloaded")
                    await async_random_delay(2.5, 4.5)
                else:
                    raise RuntimeError(
                        "Яндекс вернул заглушку 'limited' — лимит запросов, пауза 60с. "
                        "Повторите попытку позже или уменьшите частоту запросов."
                    )
            if await is_captcha_page_playwright(page):
                await handle_captcha(page, pause_sec=30)

            # Ожидание блока отзывов (любой из селекторов)
            loaded = False
            for sel in REVIEWS_READY_SELECTORS + SHOP_READY_SELECTORS:
                try:
                    await page.wait_for_selector(sel, timeout=5000)
                    loaded = True
                    logger.info(f"Блок загружен по селектору {sel}")
                    break
                except Exception:
                    continue
            if not loaded:
                logger.warning("Блок отзывов не найден по ожидаемым селекторам — пробуем скролл всё равно")
            await async_random_delay(1, 2)

            # Лёгкая имитация мыши
            try:
                await human_mouse_move(page)
            except Exception:
                pass

            dom_collected = False
            try:
                reviews = await self._fetch_via_api(page, limit, api_collected)
                if len(reviews) < 50:
                    raise ValueError(f"API дал {len(reviews)}, фолбэк на DOM")
            except Exception as e:
                logger.warning(f"API путь не сработал ({e}), фолбэк на DOM скролл")
                reviews = await self._scroll_and_collect(page, limit)
                dom_collected = True

            # Дождаться задач перехвата API ДО слияния и дедупликации:
            # во время DOM-скролла fetchReviews продолжает приходить, и
            # api_collected ещё может пополняться
            if pending_api_tasks:
                await asyncio.gather(*pending_api_tasks, return_exceptions=True)

            # Слияние API и DOM по review_id — поле за полем, лучшие значения
            if dom_collected and api_collected:
                reviews = self._merge_reviews(api_collected, reviews, limit)

            html = await page.content()
            shop_info = parse_shop_info_html(html)
            h1_name = ""
            try:
                h1_name = await page.evaluate("() => document.querySelector('h1')?.innerText?.trim() || ''")
                if h1_name and len(h1_name) < 80:
                    shop_info["name"] = h1_name
            except Exception: pass
            api_total = max(api_total_count) if api_total_count else None
            total_reviews = api_total or shop_info.get("total_reviews") or len(reviews)
            shop = {
                "oid": oid,
                "name": shop_info.get("name") or (h1_name if h1_name else "Магазин"),
                "address": shop_info.get("address") or "",
                "rating": shop_info.get("rating"),
                "total_reviews": total_reviews,
                "url": target_url,
            }

            # Дедупликация по review_id
            seen: set[str] = set()
            uniq: list[dict[str, Any]] = []
            for r in reviews:
                rid = r.get("review_id")
                if rid and rid in seen:
                    continue
                if rid:
                    seen.add(rid)
                uniq.append(r)
            # Ограничиваем
            if len(uniq) > limit:
                uniq = uniq[:limit]

            self._progress(f"Готово: {len(uniq)} отзывов (сырых {len(reviews)})")
            return {"shop": shop, "reviews": uniq, "resolved_url": target_url}

        finally:
            await self._close()

    async def _fetch_via_api(self, page: Page, limit: int, collected: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prev_len = len(collected)
        empty_iters = 0
        import math
        needed = math.ceil(limit / 50) + 10
        max_scroll_attempts = max(config.YANDEX_MAX_SCROLL_ATTEMPTS, min(needed, 50))
        max_reviews_limit = config.MAX_REVIEWS_PER_SHOP
        for _ in range(max_scroll_attempts):
            if len(collected) >= limit:
                break
            if len(collected) >= max_reviews_limit:
                break
            # Человечный скролл + мышь
            try:
                # 2-3 колёсика с рандомом
                for _ in range(random.randint(2, 4)):
                    await page.mouse.wheel(0, random.randint(350, 750))
                    await asyncio.sleep(random.uniform(0.4, 0.9))
                    if random.random() < 0.5:
                        try:
                            await human_mouse_move(page)
                        except Exception: pass
                # Скролл контейнера
                await page.evaluate("""() => {
                    const els=document.querySelectorAll('div[class*=\"scroll\"]');
                    let best=null,maxH=0;
                    for(const el of els){ if(el.scrollHeight>el.clientHeight && el.scrollHeight>maxH){maxH=el.scrollHeight; best=el;}}
                    if(best) best.scrollTop=best.scrollHeight; else window.scrollTo(0, document.body.scrollHeight);
                }""")
                # Ховер на случайную карточку
                if random.random() < 0.6:
                    try:
                        cards = await page.query_selector_all("div.business-review-view")
                        if cards:
                            c = random.choice(cards[:10])
                            await c.hover()
                            await asyncio.sleep(random.uniform(0.5, 1.2))
                    except Exception: pass
            except Exception: pass
            await asyncio.sleep(random.uniform(3.0, 4.5))
            if len(collected) == prev_len:
                empty_iters += 1
                if empty_iters >= 4:
                    break
            else:
                empty_iters = 0
                prev_len = len(collected)
            if len(collected) % 200 == 0 and len(collected) > 0 and len(collected) != prev_len:
                self._progress(f"API: {len(collected)} отзывов...")
        # Небольшая пауза на случай, если какие-то ответы ещё в полёте
        await asyncio.sleep(0.3)
        seen=set()
        uniq=[]
        for r in collected:
            if r["review_id"] not in seen:
                seen.add(r["review_id"])
                uniq.append(r)
        self._progress(f"API: собрано {len(uniq)} отзывов")
        if not uniq:
            raise ValueError("API не вернул отзывы")
        return uniq[:limit]

    def _merge_reviews(self, api_reviews: list[dict], dom_reviews: list[dict], limit: int) -> list[dict]:
        """
        Слить отзывы из API и DOM по review_id — поле за полем, а не выбором
        одной версии целиком. Для каждого review_id:
        - текст — из источника, где он длиннее (DOM полнее после «Ещё»);
        - фото — из источника, где их больше (API отдаёт список сразу);
        - ответ владельца / рейтинг / дата / автор — из источника, где поле заполнено;
        - лайки — максимум из двух;
        - is_verified — True, если хоть один источник нашёл бейдж (детект
          в источниках разный, ложных True практически не бывает).

        Отзывы без review_id из DOM добавляются как есть. Итог сортируется
        по дате (новые сверху), чтобы порядок не зависел от источника.
        """
        by_id: dict[str, dict[str, Any]] = {}
        order: list[str] = []

        def _absorb(dst: dict[str, Any], src: dict[str, Any]) -> None:
            """Дописать в dst более полные значения полей из src."""
            if len(src.get("text") or "") > len(dst.get("text") or ""):
                dst["text"] = src["text"]
            if len(src.get("photos") or []) > len(dst.get("photos") or []):
                dst["photos"] = src["photos"]
            if src.get("owner_response") and not dst.get("owner_response"):
                dst["owner_response"] = src["owner_response"]
            if dst.get("rating") is None and src.get("rating") is not None:
                dst["rating"] = src["rating"]
            if (src.get("likes") or 0) > (dst.get("likes") or 0):
                dst["likes"] = src["likes"]
            if not dst.get("date") and src.get("date"):
                dst["date"] = src["date"]
                dst["raw_date"] = src.get("raw_date", "")
            src_author = src.get("author") or ""
            dst_author = dst.get("author") or ""
            if src_author and src_author != "Аноним" and (not dst_author or dst_author == "Аноним"):
                dst["author"] = src_author
            if src.get("is_verified"):
                dst["is_verified"] = True

        for source in (api_reviews, dom_reviews):
            for r in source:
                rid = r.get("review_id")
                if not rid:
                    continue
                if rid in by_id:
                    _absorb(by_id[rid], r)
                else:
                    by_id[rid] = dict(r)
                    order.append(rid)

        merged = [by_id[rid] for rid in order]
        merged += [dict(r) for r in dom_reviews if not r.get("review_id")]
        merged.sort(key=lambda x: x.get("date") or "", reverse=True)
        return merged[:limit]

    async def _scroll_and_collect(self, page: Page, limit: int) -> list[dict[str, Any]]:
        """Скролл с паузами и сбор отзывов после каждой итерации."""
        cfg = AntiBotConfig.from_env()
        max_attempts = cfg.max_scroll_attempts if cfg.max_scroll_attempts else 80
        # Увеличиваем лимит попыток исходя из ожидаемого кол-ва отзывов
        max_attempts = max(max_attempts, 30)

        prev_count = 0
        empty_iters = 0
        all_reviews: list[dict[str, Any]] = []

        # Попробуем найти скролл-контейнер: обычно боковая панель с отзывами
        scroll_selectors = [
            "div[class*='scroll']",
            "div[class*='business-reviews']",
            "div[class*='reviews-view']",
            "div[class*='sidebar']",
            "body",
        ]
        # Определим контейнер (первый видимый)
        scroll_target = "body"
        for sel in scroll_selectors:
            try:
                cnt = await page.query_selector(sel)
                if cnt and await cnt.is_visible():
                    scroll_target = sel
                    break
            except Exception:
                continue
        logger.info(f"Скролл-контейнер: {scroll_target}")

        for attempt in range(max_attempts):
            try:
                for _ in range(random.randint(3, 5)):
                    dy = random.randint(300, 700)
                    if scroll_target == "body":
                        await page.mouse.wheel(0, dy)
                    else:
                        await page.evaluate("""({sel, dy}) => {
                            const el = document.querySelector(sel);
                            if (el) el.scrollBy(0, dy);
                            else window.scrollBy(0, dy);
                        }""", {"sel": scroll_target, "dy": dy})
                    await asyncio.sleep(random.uniform(0.3, 0.8))
                    if random.random() < 0.4:
                        try: await human_mouse_move(page)
                        except Exception: pass
                if scroll_target == "body":
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                else:
                    await page.evaluate("""({sel}) => {
                        const el = document.querySelector(sel);
                        if (el) el.scrollTo(0, el.scrollHeight);
                    }""", {"sel": scroll_target})
            except Exception as e:
                logger.warning(f"Скролл ошибка: {e}")
            pause = max(random.uniform(cfg.min_delay_sec, cfg.max_delay_sec), cfg.scroll_pause_sec)
            await asyncio.sleep(pause + random.uniform(0, 0.7))

            # Проверка капчи на каждой итерации
            try:
                if await is_captcha_page_playwright(page):
                    await handle_captcha(page, pause_sec=30)
            except CaptchaDetectedError:
                raise
            except Exception:
                pass

            # Собираем HTML и парсим
            try:
                html = await page.content()
                batch = parse_reviews_html(html)
                # Мержим
                # Используем review_id для дедупликации
                ids = {r.get("review_id") for r in all_reviews if r.get("review_id")}
                new = [r for r in batch if r.get("review_id") not in ids or not r.get("review_id")]
                # Если review_id пустые, считаем по длине текста как фолбэк
                if not ids:
                    all_reviews = batch
                else:
                    all_reviews.extend(new)
                cur_count = len(all_reviews)
            except Exception as e:
                logger.warning(f"Парсинг после скролла {attempt}: {e}")
                cur_count = prev_count

            self._progress(f"Скролл {attempt+1}/{max_attempts}: {cur_count} отзывов (было {prev_count})")

            if cur_count >= limit:
                logger.info(f"Достигнут лимит {limit}, стоп скролла")
                break

            if cur_count == prev_count:
                empty_iters += 1
                if empty_iters >= 3:
                    logger.info(f"Нет новых отзывов 3 итерации подряд — стоп на {attempt+1}")
                    break
            else:
                empty_iters = 0
                prev_count = cur_count

            # Ранний выход если отзывов мало и 2 пустых итерации
            if cur_count > 0 and cur_count < 20 and empty_iters >= 2:
                break

        # Раскрываем все усечённые отзывы ("Ещё") перед финальным парсингом
        try:
            self._progress("Раскрываю усечённые отзывы (кнопки «Ещё»)...")
            expanded = await self._expand_all_reviews(page)
            if expanded > 0:
                await asyncio.sleep(1.5)
                html = await page.content()
                expanded_batch = parse_reviews_html(html)
                by_id = {r.get("review_id"): r for r in expanded_batch if r.get("review_id")}
                for i, r in enumerate(all_reviews):
                    rid = r.get("review_id")
                    if rid and rid in by_id and len((by_id[rid].get("text") or "")) > len((r.get("text") or "")):
                        all_reviews[i] = by_id[rid]
                logger.info(f"Раскрыто {expanded} кнопок «Ещё», обновлён текст {len([r for r in all_reviews if 'Ещё' not in (r.get('text') or '')])} отзывов")
        except Exception as e:
            logger.warning(f"Не удалось раскрыть «Ещё»: {e}")

        # Подгружаем ленивые фото — прокручиваем каждую карточку в зону видимости
        try:
            self._progress("Подгружаю фото (ленивая загрузка)...")
            await page.evaluate("""() => {
                const cards = document.querySelectorAll('div.business-review-view, div[class*=\"business-review-view\"]');
                for (const c of cards) { c.scrollIntoView({block: 'center'}); }
            }""")
            await asyncio.sleep(1.0)
            # Ещё раз скроллим по 1 карточке
            cards = await page.query_selector_all("div.business-review-view")
            for card in cards[:30]:
                try:
                    await card.scroll_into_view_if_needed()
                    await asyncio.sleep(0.15)
                except Exception: pass
            html = await page.content()
            photo_batch = parse_reviews_html(html)
            by_id2 = {r.get("review_id"): r for r in photo_batch if r.get("review_id")}
            for i, r in enumerate(all_reviews):
                rid = r.get("review_id")
                if rid and rid in by_id2 and len(by_id2[rid].get("photos") or []) > len(r.get("photos") or []):
                    all_reviews[i]["photos"] = by_id2[rid]["photos"]
        except Exception as e:
            logger.warning(f"Подгрузка фото пропущена: {e}")

        # Добираем пагинацию "Показать ещё" если осталась (для 222 → 151)
        try:
            for _ in range(4):
                btn = None
                for sel in ["button:has-text('Показать ещё')", "button:has-text('Показать еще')", "span:has-text('Показать ещё')"]:
                    try:
                        cand = await page.query_selector(sel)
                        if cand and await cand.is_visible():
                            btn = cand
                            break
                    except Exception: continue
                if not btn:
                    break
                self._progress("Кликаю «Показать ещё» для догрузки...")
                await btn.click()
                await asyncio.sleep(3)
                html = await page.content()
                batch = parse_reviews_html(html)
                ids = {r.get("review_id") for r in all_reviews if r.get("review_id")}
                new = [r for r in batch if r.get("review_id") not in ids]
                if new:
                    all_reviews.extend(new)
                    self._progress(f"Догружено {len(new)} через «Показать ещё», всего {len(all_reviews)}")
                else:
                    break
        except Exception as e:
            logger.warning(f"Пагинация «Показать ещё» пропущена: {e}")

        return all_reviews

    async def _expand_all_reviews(self, page: Page) -> int:
        """Кликнуть по всем кнопкам «Ещё» чтобы раскрыть полный текст отзыва."""
        total_clicked = 0
        for _ in range(6):  # до 6 проходов — новые кнопки могут появиться после кликов
            try:
                clicked = await page.evaluate("""() => {
                    const els = Array.from(document.querySelectorAll('button, span, a, div'));
                    let n = 0;
                    for (const el of els) {
                        const t = (el.textContent || '').trim();
                        if (t === 'Ещё' || t === 'Ещё ') {
                            const rect = el.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) {
                                el.click();
                                n++;
                            }
                        }
                    }
                    return n;
                }""")
                if clicked == 0:
                    break
                total_clicked += clicked
                await asyncio.sleep(0.7)
            except Exception:
                break
        if total_clicked:
            logger.info(f"Кликнуто {total_clicked} кнопок «Ещё»")
        return total_clicked


# ---------------------------------------------------------------------------
# Удобная синхронная обёртка для вызова из Gradio (запускает asyncio)
# ---------------------------------------------------------------------------

def scrape_sync(url: str, max_reviews: Optional[int] = None, headless: bool = True, on_progress: Optional[Callable[[str], None]] = None) -> dict[str, Any]:
    """Синхронная обёртка над YandexScraper.scrape (для Gradio/BG tasks)."""
    scraper = YandexScraper(headless=headless, on_progress=on_progress)
    return asyncio.run(scraper.scrape(url, max_reviews=max_reviews))
