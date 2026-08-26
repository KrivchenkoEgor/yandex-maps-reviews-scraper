"""Тесты слияния API и DOM отзывов (YandexScraper._merge_reviews)."""
from app.yandex_scraper import YandexScraper


def _mk(rid: str, **kw) -> dict:
    """Заготовка отзыва с дефолтами как из парсера/API."""
    base = {
        "review_id": rid,
        "author": "Аноним",
        "rating": None,
        "date": "",
        "raw_date": "",
        "text": "",
        "photos": [],
        "owner_response": None,
        "likes": 0,
        "is_verified": False,
    }
    base.update(kw)
    return base


def test_merge_field_level_best_of_both():
    """Текст длиннее — из DOM, фото/ответ/рейтинг — из API, лайки — максимум."""
    api = [_mk(
        "r1", text="Хорошо", photos=["p1", "p2", "p3"],
        owner_response={"text": "Спасибо", "date": "2024-03-16"},
        likes=5, rating=5, date="2024-03-15",
    )]
    dom = [_mk(
        "r1", text="Хорошо — полный текст после раскрытия «Ещё», гораздо длиннее",
        likes=12, is_verified=True, date="2024-03-15",
    )]
    merged = YandexScraper()._merge_reviews(api, dom, limit=100)
    assert len(merged) == 1
    r = merged[0]
    assert r["text"].startswith("Хорошо — полный")
    assert r["photos"] == ["p1", "p2", "p3"]
    assert r["owner_response"]["text"] == "Спасибо"
    assert r["likes"] == 12
    assert r["rating"] == 5
    assert r["is_verified"] is True


def test_merge_is_verified_either_source():
    api = [_mk("r1", is_verified=True)]
    dom = [_mk("r1", is_verified=False)]
    (r,) = YandexScraper()._merge_reviews(api, dom, limit=10)
    assert r["is_verified"] is True


def test_merge_union_and_no_duplicates():
    """Объединение ID из обоих источников, дубли внутри источника схлопываются."""
    api = [_mk("r1"), _mk("r2", date="2024-03-10"), _mk("r2", date="2024-03-10", likes=7)]
    dom = [_mk("r1", text="текст"), _mk("r3", date="2024-03-20")]
    merged = YandexScraper()._merge_reviews(api, dom, limit=100)
    ids = sorted(r["review_id"] for r in merged)
    assert ids == ["r1", "r2", "r3"]
    r2 = next(r for r in merged if r["review_id"] == "r2")
    assert r2["likes"] == 7


def test_merge_limit_and_order_newest_first():
    api = [_mk("a", date="2024-01-01"), _mk("b", date="2024-03-01")]
    dom = [_mk("c", date="2024-02-01")]
    merged = YandexScraper()._merge_reviews(api, dom, limit=2)
    assert [r["review_id"] for r in merged] == ["b", "c"]


def test_merge_fills_empty_fields():
    """Пустые поля одной версии заполняются заполненными полями другой."""
    api = [_mk("r1", rating=4, date="2024-05-01", author="Иван")]
    dom = [_mk("r1", text="текст")]
    (r,) = YandexScraper()._merge_reviews(api, dom, limit=10)
    assert r["rating"] == 4
    assert r["date"] == "2024-05-01"
    assert r["author"] == "Иван"
    assert r["text"] == "текст"


def test_merge_keeps_dom_review_without_id():
    api = [_mk("r1", date="2024-05-05")]
    dom_no_id = _mk("", text="без id")
    merged = YandexScraper()._merge_reviews(api, [dom_no_id], limit=10)
    assert len(merged) == 2
    assert any(r["review_id"] == "" and r["text"] == "без id" for r in merged)


def test_merge_does_not_mutate_inputs():
    api = [_mk("r1", text="коротко", likes=1)]
    dom = [_mk("r1", text="значительно более длинный текст", likes=9)]
    YandexScraper()._merge_reviews(api, dom, limit=10)
    assert api[0]["text"] == "коротко"
    assert api[0]["likes"] == 1
    assert dom[0]["likes"] == 9
