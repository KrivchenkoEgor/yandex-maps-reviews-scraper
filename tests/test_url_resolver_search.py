from app.url_resolver import extract_oid, resolve_yandex_url


def test_extract_oid_from_poi_uri():
    assert extract_oid("https://yandex.ru/maps/?mode=poi&poi[uri]=ymapsbm1://org?oid=1659941740") == "1659941740"


def test_extract_oid_from_path_search():
    # CTDqNYNS -> /org/dobryanka/1275165507/ mode=search
    url = "https://yandex.ru/maps/org/dobryanka/1275165507/?mode=search&ll=82.98%2C55.04&z=14.53"
    assert extract_oid(url) == "1275165507"


def test_extract_oid_from_path_chain():
    # CTDqqLJR -> /org/dobryanka/85448204612/ (chain)
    url = "https://yandex.ru/maps/org/dobryanka/85448204612/?mode=search&ll=83.10%2C54.83"
    assert extract_oid(url) == "85448204612"


def test_resolve_search_normalizes_to_poi():
    # CTDqNYNS short link should normalize to poi
    r = resolve_yandex_url("https://yandex.ru/maps/-/CTDqNYNS")
    assert r["oid"] == "1275165507"
    assert r["params"]["mode"] == "poi"
    assert "ymapsbm1://org?oid=1275165507" in r["resolved_url"]
    assert "tab=reviews" in r["resolved_url"]


def test_resolve_search_chain_normalizes():
    r = resolve_yandex_url("https://yandex.ru/maps/-/CTDqqLJR")
    assert r["oid"] == "85448204612"
    assert r["params"]["mode"] == "poi"


def test_resolve_poi_unchanged():
    r = resolve_yandex_url("https://yandex.ru/maps/-/CTwsUYyk")
    assert r["oid"] == "1659941740"
    assert r["params"]["mode"] == "poi"
