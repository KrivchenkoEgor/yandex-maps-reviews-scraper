import asyncio
import tempfile
from pathlib import Path

import pytest

from app.database import Database


@pytest.mark.asyncio
async def test_upsert_dedup_rowcount():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        db = Database(db_path)
        await db.init()
        await db.upsert_shop({"oid": "999", "name": "Тест", "address": "ул 1", "rating": 4.5, "total_reviews": 10, "url": "https://yandex.ru/maps/1"})
        reviews = [
            {"review_id": "r1", "author": "Иван", "rating": 5, "date": "2024-03-15", "text": "Отлично", "photos": [], "owner_response": None, "likes": 2, "is_verified": True},
            {"review_id": "r2", "author": "Мария", "rating": 4, "date": "2024-03-14", "text": "Норм", "photos": [], "owner_response": None, "likes": 0, "is_verified": False},
        ]
        inserted = await db.upsert_reviews("999", reviews)
        assert inserted == 2
        assert await db.count_reviews("999") == 2

        # duplicate r1 + new r3
        inserted2 = await db.upsert_reviews("999", [
            {"review_id": "r1", "author": "Иван", "rating": 5, "date": "2024-03-15", "text": "Дубль", "photos": [], "owner_response": None, "likes": 0, "is_verified": False},
            {"review_id": "r3", "author": "Петр", "rating": 3, "date": "2024-03-13", "text": "Так себе", "photos": [], "owner_response": None, "likes": 1, "is_verified": False},
        ])
        assert inserted2 == 1  # only r3
        assert await db.count_reviews("999") == 3

        # all duplicates
        inserted3 = await db.upsert_reviews("999", reviews)
        assert inserted3 == 0
        assert await db.count_reviews("999") == 3


@pytest.mark.asyncio
async def test_upsert_empty():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        db = Database(db_path)
        await db.init()
        inserted = await db.upsert_reviews("999", [])
        assert inserted == 0
