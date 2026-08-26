"""
review_parser — извлечение структурированных данных из HTML отзывов Яндекс.Карт.

Отвечает за Шаг 5 из AGENTS.md: для каждого отзыва извлекает автора, рейтинг,
дату (с преобразованием "3 дня назад" → ISO), текст, фото, ответ владельца, лайки,
признак "Проверенный отзыв".

Работает как с BeautifulSoup (синхронно), так и с Playwright locators (async support
через отдельные хелперы). Селекторы вынесены в константы — при смене вёрстки
меняется только они, плюс пишется урок в LESSONS.md.
"""

import re
from datetime import date, datetime, timedelta
from typing import Any, Optional

from bs4 import BeautifulSoup, Tag
from dateutil.relativedelta import relativedelta
from loguru import logger

# ---------------------------------------------------------------------------
# Селекторы — меняются при редизайне Яндекса (см. AGENTS.md: "Вёрстка меняется")
# Указываем несколько фолбэков через запятую
# ---------------------------------------------------------------------------
SELECTORS = {
    # Карточка отзыва целиком
    "card": [
        "div.business-review-view",
        "div[data-testid='review-card']",
        "div[class*='business-review-view']",
        "div[class*='reviews-view__review']",
    ],
    # Автор
    "author": [
        "span.business-review-view__author-name",
        "div[class*='review-view__author'] span",
        "span[class*='author']",
        "[data-testid='review-author']",
    ],
    # Рейтинг — звёзды, aria-label или текст "5 из 5"
    "rating": [
        "span.business-review-view__rating",
        "div[class*='stars']",
        "[aria-label*='звезд']",
        "[aria-label*='stars']",
        "span[class*='rating']",
    ],
    # Дата
    "date": [
        "span.business-review-view__date",
        "div[class*='review-view__date']",
        "span[class*='review'] [class*='date']",
        "[data-testid='review-date']",
    ],
    # Текст отзыва
    "text": [
        "span.business-review-view__body-text",
        "div.business-review-view__body",
        "span[class*='review-view__body']",
        "div[class*='spoiler'] span",
        "[data-testid='review-text']",
    ],
    # Фото
    "photos": [
        "div.business-review-view__photos img",
        "div[class*='review-view__photo'] img",
        "img[class*='photo']",
        "a[class*='photo'] img",
    ],
    # Ответ владельца
    "owner_response": [
        "div.business-review-view__reply",
        "div[class*='review-view__reply']",
        "div[class*='owner-response']",
    ],
    "owner_response_text": [
        "span.business-review-view__reply-text",
        "div[class*='reply'] span",
    ],
    "owner_response_date": [
        "span.business-review-view__reply-date",
        "div[class*='reply'] [class*='date']",
    ],
    # Лайки
    "likes": [
        "span.business-review-view__likes-count",
        "button[class*='like'] span",
        "span[class*='likes']",
    ],
    # Дизлайки
    "dislikes": [
        "span.business-review-view__dislikes-count",
        "button[class*='dislike'] span",
        "span[class*='dislikes']",
        "span.business-review-view__dislike-count",
    ],
    # Проверенный отзыв — иконка/бейдж
    "verified": [
        "span:has-text('Проверенный')",
        "span[class*='verified']",
        "div[class*='verified']",
        "[data-testid='verified-badge']",
        "span[class*='badge']",
    ],
    "shop_name": [
        "h1",
        "h1[class*='business-card-view__title']",
        "h1[data-testid='business-title']",
        "div[class*='business-title'] h1",
        "[class*='card-title']",
        "[class*='business-name']",
        "div[class*='org-name']",
        "a[class*='business-link'] h1",
    ],
    "shop_address": [
        "div[class*='business-card-view__address']",
        "[data-testid='business-address']",
        "div[class*='address']",
        "div[class*='business-address']",
        "span[class*='address']",
    ],
    "shop_rating": [
        "span[class*='business-rating']",
        "div[class*='rating'] span",
        "[class*='rating__value']",
    ],
    "shop_total_reviews": [
        "span[class*='reviews-count']",
        "div[class*='review-count']",
        "a:has-text('отзыв')",
        "span:has-text('отзыв')",
        "div:has-text('отзывов')",
    ],
}

# ---------------------------------------------------------------------------
# Парсинг дат вида "3 дня назад", "вчера", "15 марта 2024"
# ---------------------------------------------------------------------------
RELATIVE_RE = re.compile(r"(\d+)\s*(день|дня|дней|час|часа|часов|минут|недел|месяц|год)", re.I)
MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "июн": 6,
    "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}


def parse_yandex_date(raw: str, now: Optional[datetime] = None) -> str:
    """
    Преобразует строку даты Яндекса в ISO-дату YYYY-MM-DD.

    Поддерживает:
    - "сегодня", "вчера", "позавчера"
    - "3 дня назад", "2 часа назад", "5 минут назад", "2 недели назад"
    - "15 марта", "15 марта 2024", "15.03.2024"
    - ISO "2024-03-15"
    """
    if not raw:
        return ""
    raw = raw.strip().lower()
    if now is None:
        now = datetime.now()

    # Сегодня / вчера / позавчера
    if "сегодня" in raw:
        return now.date().isoformat()
    if "позавчера" in raw:
        return (now.date() - timedelta(days=2)).isoformat()
    if "вчера" in raw:
        return (now.date() - timedelta(days=1)).isoformat()

    # "N дней/часов/минут/недель назад"
    m = RELATIVE_RE.search(raw)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        if "день" in unit or "дней" in unit or "дня" in unit:
            return (now - timedelta(days=n)).date().isoformat()
        if "час" in unit:
            return (now - timedelta(hours=n)).date().isoformat()
        if "минут" in unit:
            return (now - timedelta(minutes=n)).date().isoformat()
        if "недел" in unit:
            return (now - timedelta(weeks=n)).date().isoformat()
        if "месяц" in unit:
            return (now - relativedelta(months=n)).date().isoformat()
        if "год" in unit:
            return (now - relativedelta(years=n)).date().isoformat()

    # "15 марта", "15 марта 2024"
    for name, num in MONTHS_RU.items():
        if name in raw:
            # ищем день
            dm = re.search(r"(\d{1,2})", raw)
            ym = re.search(r"(20\d{2})", raw)
            if dm:
                d = int(dm.group(1))
                y = int(ym.group(1)) if ym else now.year
                try:
                    return date(y, num, d).isoformat()
                except ValueError:
                    pass

    # "15.03.2024" или "15.03.24"
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})", raw)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            pass

    # ISO уже
    m = re.search(r"(\d{4}-\d{2}-\d{2})", raw)
    if m:
        return m.group(1)

    # Фолбэк — возвращаем как есть, пусть вызывающий решает
    logger.warning(f"Не удалось распарсить дату: '{raw}' — возвращаю как есть")
    return raw.strip()


def _find_first(soup: Tag, selectors: list[str]) -> Optional[Tag]:
    """Найти первый элемент по списку фолбэк-селекторов."""
    for sel in selectors:
        try:
            # :has-text не поддерживается bs4 — пропускаем такие селекторы для bs4
            if ":has-text" in sel:
                continue
            el = soup.select_one(sel)
            if el:
                return el
        except Exception:
            continue
    return None


def _find_all_cards(soup: BeautifulSoup) -> list[Tag]:
    """Найти все карточки отзывов (пробуем все селекторы card)."""
    seen = set()
    cards: list[Tag] = []
    for sel in SELECTORS["card"]:
        try:
            for el in soup.select(sel):
                eid = id(el)
                if eid not in seen:
                    seen.add(eid)
                    cards.append(el)
        except Exception:
            continue
    return cards


def _get_text(el: Optional[Tag]) -> str:
    if el is None:
        return ""
    return el.get_text(strip=True)


def _get_rating(el: Optional[Tag]) -> Optional[int]:
    """Извлечь рейтинг 1-5 из элемента."""
    if el is None:
        return None
    # 1. aria-label "Оценка 5 из 5"
    aria = el.get("aria-label", "")
    m = re.search(r"(\d)\s*из\s*5", aria)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d)", aria)
    if m and 1 <= int(m.group(1)) <= 5:
        return int(m.group(1))
    # 2. текст "5" или "★ ★ ★ ★ ★"
    text = el.get_text(strip=True)
    # количество звёзд-символов
    stars = text.count("★")
    if 1 <= stars <= 5:
        return stars
    m = re.search(r"([1-5])", text)
    if m:
        return int(m.group(1))
    # 3. классы вида stars_5, rating-5
    cls = " ".join(el.get("class", []))
    m = re.search(r"stars[_-]?(\d)|rating[_-]?(\d)", cls)
    if m:
        for g in m.groups():
            if g and 1 <= int(g) <= 5:
                return int(g)
    return None


def _get_likes(el: Optional[Tag]) -> int:
    if el is None:
        return 0
    text = el.get_text(strip=True)
    m = re.search(r"(\d+)", text)
    if m:
        return int(m.group(1))
    return 0


def _get_dislikes(el: Optional[Tag]) -> int:
    if el is None:
        return 0
    text = el.get_text(strip=True)
    m = re.search(r"(\d+)", text)
    if m:
        return int(m.group(1))
    return 0


def _get_photos(card: Tag) -> list[str]:
    urls: list[str] = []
    # Фото отзыва — get-altay / get-business-photos, аватарка — get-yapic (игнорируем)
    # Ищем по всей карточке, но фильтруем строго по get-altay, чтобы не схватить аватарку
    for sel in SELECTORS["photos"]:
        try:
            for img in card.select(sel):
                src = img.get("src") or img.get("data-src") or img.get("data-srcset") or img.get("href") or img.get("data-href")
                if src:
                    if " " in src and "," in src:
                        src = src.split(",")[0].strip().split(" ")[0]
                    if src.startswith("//"):
                        src = "https:" + src
                    elif src.startswith("/"):
                        src = "https://yandex.ru" + src
                    # Строго get-altay для фото отзыва, отбрасываем yapic/islands-68
                    if src.startswith("http") and "get-altay" in src and src not in urls:
                        urls.append(src)
                    elif src.startswith("http") and "altay" in src and "get-yapic" not in src and src not in urls:
                        urls.append(src)
                style = img.get("style") or ""
                if "url(" in style:
                    m = re.search(r"url\(['\"]?(.*?)['\"]?\)", style)
                    if m:
                        u = m.group(1)
                        if u.startswith("//"):
                            u = "https:" + u
                        elif u.startswith("/"):
                            u = "https://yandex.ru" + u
                        if u.startswith("http") and "get-altay" in u and u not in urls:
                            urls.append(u)
        except Exception: continue
    # Дополнительно: любые background-image с get-altay по всей карточке
    try:
        for el in card.find_all(style=re.compile(r"url\(")):
            style = el.get("style") or ""
            for m in re.finditer(r"url\(['\"]?(.*?)['\"]?\)", style):
                u = m.group(1)
                if u.startswith("//"):
                    u = "https:" + u
                elif u.startswith("/"):
                    u = "https://yandex.ru" + u
                if u.startswith("http") and "get-altay" in u and u not in urls:
                    urls.append(u)
    except Exception: pass
    # Резерв: ищем все URL с get-altay в сыром HTML карточки (на случай data-атрибутов)
    try:
        raw = str(card)
        for m in re.finditer(r"https?://[^\"'\s]+get-altay[^\"'\s]+", raw):
            u = m.group(0)
            # Обрезаем хвост до кавычки/пробела
            u = re.split(r"[\"'\s]", u)[0]
            if u not in urls:
                urls.append(u)
        for m in re.finditer(r"//[^\"'\s]+get-altay[^\"'\s]+", raw):
            u = "https:" + m.group(0)
            if u not in urls:
                urls.append(u)
    except Exception: pass
    return urls


def _is_verified(card: Tag) -> bool:
    text = card.get_text()
    if "Проверен" in text or "проверен" in text.lower():
        return True
    for sel in SELECTORS["verified"]:
        if ":has-text" in sel:
            continue
        try:
            if card.select_one(sel):
                return True
        except Exception:
            continue
    return False


def parse_review_card(card: Tag) -> dict[str, Any]:
    """
    Разобрать одну карточку отзыва (BeautifulSoup Tag) в dict.

    Возвращает ключи: review_id, author, rating, date, text, photos,
    owner_response ({text, date}|None), likes, dislikes, is_verified
    """
    review_id = card.get("data-review-id") or card.get("data-id") or card.get("id") or card.get("data-review_id") or ""
    if not review_id:
        import hashlib
        raw = "|".join([
            _get_text(_find_first(card, SELECTORS["text"])),
            _get_text(_find_first(card, SELECTORS["author"])),
            _get_text(_find_first(card, SELECTORS["date"])),
            str(_get_rating(_find_first(card, SELECTORS["rating"])) or ""),
            str(_get_likes(_find_first(card, SELECTORS["likes"])) or 0),
            str(_get_dislikes(_find_first(card, SELECTORS["dislikes"])) or 0),
            str(len(_get_photos(card))),
        ])
        review_id = hashlib.md5(raw.encode()).hexdigest()[:12] if raw.strip("|") else ""

    author_el = _find_first(card, SELECTORS["author"])
    rating_el = _find_first(card, SELECTORS["rating"])
    date_el = _find_first(card, SELECTORS["date"])
    text_el = _find_first(card, SELECTORS["text"])
    likes_el = _find_first(card, SELECTORS["likes"])
    dislikes_el = _find_first(card, SELECTORS["dislikes"])

    # Ответ владельца — отдельный блок внутри карточки
    owner_block = _find_first(card, SELECTORS["owner_response"])
    owner_response = None
    if owner_block:
        rt = _find_first(owner_block, SELECTORS["owner_response_text"])
        rd = _find_first(owner_block, SELECTORS["owner_response_date"])
        # фолбэк — весь текст блока минус дата
        resp_text = _get_text(rt) or _get_text(owner_block)
        # убираем дублирование даты из текста
        if resp_text:
            owner_response = {
                "text": resp_text.strip(),
                "date": parse_yandex_date(_get_text(rd)) if rd else "",
            }

    raw_date = _get_text(date_el)
    return {
        "review_id": review_id.strip(),
        "author": _get_text(author_el) or "Аноним",
        "rating": _get_rating(rating_el),
        "date": parse_yandex_date(raw_date) if raw_date else "",
        "raw_date": raw_date,
        "text": _get_text(text_el),
        "photos": _get_photos(card),
        "owner_response": owner_response,
        "likes": _get_likes(likes_el),
        "dislikes": _get_dislikes(dislikes_el),
        "is_verified": _is_verified(card),
    }


def parse_reviews_html(html: str) -> list[dict[str, Any]]:
    """
    Разобрать HTML страницы отзывов (после скролла) → список отзывов.

    Устойчив к смене вёрстки: перебирает все фолбэк-селекторы.
    """
    soup = BeautifulSoup(html, "lxml")
    cards = _find_all_cards(soup)
    logger.info(f"Найдено карточек отзывов: {len(cards)}")
    reviews = []
    for card in cards:
        try:
            r = parse_review_card(card)
            # Фильтруем пустые карточки без текста и автора
            if r["text"] or r["author"] != "Аноним":
                reviews.append(r)
        except Exception as e:
            logger.warning(f"Не удалось распарсить карточку: {e}")
    return reviews


def parse_shop_info_html(html: str) -> dict[str, Any]:
    """Извлечь инфо о магазине из HTML шапки (имя, адрес, рейтинг, всего отзывов)."""
    soup = BeautifulSoup(html, "lxml")
    name_el = _find_first(soup, SELECTORS["shop_name"])
    addr_el = _find_first(soup, SELECTORS["shop_address"])
    rating_el = _find_first(soup, SELECTORS["shop_rating"])
    total_el = _find_first(soup, SELECTORS["shop_total_reviews"])

    total_raw = _get_text(total_el)
    total = None
    if total_raw:
        m = re.search(r"(\d+)", total_raw.replace(" ", ""))
        if m:
            total = int(m.group(1))

    rating = None
    if rating_el:
        rt = _get_rating(rating_el)
        if rt is not None:
            rating = float(rt)
        else:
            m = re.search(r"(\d[.,]\d)", _get_text(rating_el))
            if m:
                rating = float(m.group(1).replace(",", "."))

    return {
        "name": _get_text(name_el),
        "address": _get_text(addr_el),
        "rating": rating,
        "total_reviews": total,
    }


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Тест парсинга дат
    assert parse_yandex_date("сегодня") == datetime.now().date().isoformat()
    assert parse_yandex_date("вчера") == (datetime.now().date() - timedelta(days=1)).isoformat()
    assert parse_yandex_date("3 дня назад") == (datetime.now() - timedelta(days=3)).date().isoformat()
    assert parse_yandex_date("2 часа назад") == datetime.now().date().isoformat()
    assert parse_yandex_date("15 марта 2024") == "2024-03-15"
    assert parse_yandex_date("15.03.2024") == "2024-03-15"
    print("✅ parse_yandex_date OK")

    # Тест парсинга карточки из фейкового HTML
    fake_html = """
    <div class="business-review-view" data-review-id="abc123">
        <span class="business-review-view__author-name">Иван П.</span>
        <span class="business-review-view__rating" aria-label="Оценка 5 из 5"></span>
        <span class="business-review-view__date">3 дня назад</span>
        <span class="business-review-view__body-text">Отличный магазин, свежие продукты!</span>
        <span class="business-review-view__likes-count">12</span>
        <span>Проверенный отзыв</span>
        <img src="https://example.com/photo.jpg" class="business-review-view__photo">
        <div class="business-review-view__reply">
            <span class="business-review-view__reply-text">Спасибо за отзыв!</span>
            <span class="business-review-view__reply-date">вчера</span>
        </div>
    </div>
    """
    reviews = parse_reviews_html(fake_html)
    assert len(reviews) == 1, reviews
    r = reviews[0]
    assert r["review_id"] == "abc123", r
    assert r["author"] == "Иван П.", r
    assert r["rating"] == 5, r
    assert r["likes"] == 12, r
    assert r["is_verified"] is True, r
    assert "Отличный магазин" in r["text"], r
    assert r["photos"] == ["https://example.com/photo.jpg"], r
    assert r["owner_response"]["text"] == "Спасибо за отзыв!", r
    print("✅ parse_reviews_html OK")
    print("Все тесты review_parser прошли!")
