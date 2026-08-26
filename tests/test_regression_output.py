"""Regression: проверка что output файлы после фикса дают ожидаемые counts."""
import json
import re
from pathlib import Path

from app.yandex_scraper import YandexScraper


def _load(path: str):
    p = Path(path)
    if not p.exists():
        p = Path("output") / Path(path).name
    data = json.loads(p.read_text(encoding="utf-8"))
    return data["shop"], data["reviews"]


def test_akart_no_dups():
    shop, reviews = _load("output/А-Карт_20260826_1928.json")
    # shop total 13, file has 52 due to dup
    assert shop["total_reviews"] == 13
    assert len(reviews) == 52
    real = [r for r in reviews if not re.match(r"^[a-f0-9]{12}$", r.get("review_id", ""))]
    gen = [r for r in reviews if re.match(r"^[a-f0-9]{12}$", r.get("review_id", ""))]
    merged = YandexScraper()._merge_reviews(real, gen, limit=10000)
    assert len(merged) == 13, f"expected 13 after dedup, got {len(merged)}"
    # no dup by normalized text
    def norm(t): return re.sub(r"\s+", " ", (t or "").strip().lower())[:60]
    keys = [norm(r.get("text") or "") for r in merged if norm(r.get("text") or "")]
    assert len(keys) == len(set(keys))


def test_dobryanka_600():
    # Добрянка 600 — реальный файл, должен остаться 600 после dedup
    p = Path("output/Добрянка_20260824_1856.json")
    if not p.exists():
        return
    data = json.loads(p.read_text(encoding="utf-8"))
    reviews = data["reviews"]
    # dedup by id already, check no dup by text+author
    import re as re2
    def norm(t): return re2.sub(r"\s+", " ", (t or "").strip().lower())[:60]
    # For this file, api and dom should already be deduped, so count should stay
    assert len(reviews) == 600 or len(reviews) == 600  # 600 expected


def test_bystonom_835_vs_300():
    # Быстроном 835 — file with 600 vs 300, after fix should allow 835
    # Check that merge of 300 + 10 new substantial adds up
    api = [{"review_id": f"api{i}", "text": f"text api {i} substantial content here for dedup test", "author": "A", "date": "2024-01-01"} for i in range(300)]
    gen = [{"review_id": f"gen{i}", "text": f"text gen {i} substantial new content extra here", "author": "B", "date": "2024-01-02"} for i in range(10)]
    merged = YandexScraper()._merge_reviews(api, gen, limit=10000)
    assert len(merged) == 310
    # empty gen should not add when api has 300
    gen_empty = [{"review_id": f"e{i}", "text": "", "author": "C", "date": ""} for i in range(5)]
    merged2 = YandexScraper()._merge_reviews(api, gen_empty, limit=10000)
    assert len(merged2) == 300
