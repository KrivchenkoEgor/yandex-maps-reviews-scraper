from pathlib import Path
import tempfile

from app.anti_bot import FALLBACK_USER_AGENTS, get_random_user_agent, _get_ua
from app.database import Database


def test_fallback_no_mobile_ua():
    joined = " ".join(FALLBACK_USER_AGENTS).lower()
    assert "android" not in joined
    assert "mobile" not in joined
    assert "iphone" not in joined.lower()


def test_useragent_singleton():
    a = _get_ua()
    b = _get_ua()
    assert a is b
    # get_random_user_agent should use singleton
    ua1 = get_random_user_agent()
    ua2 = get_random_user_agent()
    assert isinstance(ua1, str) and len(ua1) > 10
    assert isinstance(ua2, str)


def test_database_wal_pragma():
    with tempfile.TemporaryDirectory() as tmp:
        p = str(Path(tmp) / "test_wal.db")
        db = Database(p)
        import asyncio
        asyncio.run(db.init())
        import sqlite3
        con = sqlite3.connect(p)
        cur = con.execute("PRAGMA journal_mode;")
        mode = cur.fetchone()[0]
        assert mode.lower() == "wal"
        cur = con.execute("PRAGMA busy_timeout;")
        to = cur.fetchone()[0]
        assert to == 5000
        con.close()


def test_exporter_handles_none_text():
    from app.exporter import _stats_sheet
    reviews = [
        {"review_id": "1", "rating": 5, "date": "2024-03-15", "text": None},
        {"review_id": "2", "rating": 4, "date": "2024-03-14", "text": "ok"},
    ]
    df = _stats_sheet(reviews)
    assert not df.empty
    # top_text should not crash
    assert "Всего отзывов" in df["Метрика"].values


def test_grep_no_bare_except():
    # hygiene: no bare except: in yandex_scraper and review_parser
    for path in ["app/yandex_scraper.py", "app/review_parser.py"]:
        txt = Path(path).read_text(encoding="utf-8")
        assert "except:" not in txt or "except Exception:" in txt
        # ensure no naked except: remains (allow except Exception)
        lines = [l for l in txt.splitlines() if l.strip() == "except:"]
        assert not lines, f"bare except: in {path}"


def test_database_touch_monitor_exists():
    assert hasattr(Database, "touch_monitor")
    import inspect
    sig = inspect.signature(Database.touch_monitor)
    assert "monitor_id" in sig.parameters
