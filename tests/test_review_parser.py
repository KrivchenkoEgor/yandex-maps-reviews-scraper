from datetime import datetime

from app.review_parser import parse_yandex_date


def test_parse_yandex_date_relative():
    now = datetime(2024, 3, 15, 12, 0, 0)
    assert parse_yandex_date("3 дня назад", now=now) == "2024-03-12"
    assert parse_yandex_date("2 часа назад", now=now) == "2024-03-15"
    assert parse_yandex_date("вчера", now=now) == "2024-03-14"
    assert parse_yandex_date("сегодня", now=now) == "2024-03-15"
    assert parse_yandex_date("позавчера", now=now) == "2024-03-13"


def test_parse_yandex_date_absolute_ru():
    now = datetime(2024, 3, 15)
    assert parse_yandex_date("15 марта 2024", now=now) == "2024-03-15"
    assert parse_yandex_date("15 марта", now=now) == "2024-03-15"


def test_parse_yandex_date_dots():
    now = datetime(2024, 3, 15)
    assert parse_yandex_date("15.03.2024", now=now) == "2024-03-15"
    assert parse_yandex_date("15.03.24", now=now) == "2024-03-15"


def test_parse_yandex_date_iso():
    now = datetime(2024, 3, 15)
    assert parse_yandex_date("2024-03-15", now=now) == "2024-03-15"


def test_parse_yandex_date_empty():
    assert parse_yandex_date("") == ""
