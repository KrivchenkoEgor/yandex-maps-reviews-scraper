import tempfile
from pathlib import Path

from app.exporter import export_csv, export_excel, export_json


def test_export_excel_three_sheets():
    with tempfile.TemporaryDirectory() as tmp:
        import app.config as cfg
        old = cfg.OUTPUT_DIR
        cfg.OUTPUT_DIR = tmp
        try:
            shop = {"oid": "999", "name": "Тест Магнит", "address": "Красный 1", "rating": 4.3, "total_reviews": 2, "url": "https://yandex.ru/maps/-/CTwsUYyk"}
            reviews = [
                {"review_id": "a1", "author": "Иван", "rating": 5, "date": "2024-03-15", "text": "Отлично", "photos": ["https://ex.com/1.jpg"], "owner_response": {"text": "Спасибо", "date": "2024-03-16"}, "likes": 2, "is_verified": True},
                {"review_id": "a2", "author": "Мария", "rating": 2, "date": "2024-03-10", "text": "Очереди", "photos": [], "owner_response": None, "likes": 0, "is_verified": False},
            ]
            p = export_excel(shop, reviews, filename="test_export.xlsx")
            assert p.exists()
            import pandas as pd
            xls = pd.ExcelFile(p)
            assert set(xls.sheet_names) == {"Отзывы", "Статистика", "Магазин"}
            df = pd.read_excel(p, sheet_name="Отзывы")
            assert len(df) == 2

            p_csv = export_csv(shop, reviews, filename="test_export.csv")
            assert p_csv.exists()
            assert p_csv.read_text(encoding="utf-8-sig").count("\n") >= 2

            p_json = export_json(shop, reviews, filename="test_export.json")
            assert p_json.exists()
            import json
            data = json.loads(p_json.read_text(encoding="utf-8"))
            assert data["shop"]["oid"] == "999"
            assert len(data["reviews"]) == 2
        finally:
            cfg.OUTPUT_DIR = old
