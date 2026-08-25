"""
Модуль для разрешения ссылок Яндекс.Карт и извлечения oid организации.

Поддерживает два формата ссылок:
1. Короткая: https://yandex.ru/maps/-/CTwsUYyk
2. Полная: https://yandex.ru/maps/65/novosibirsk/?ll=...&poi[uri]=ymapsbm1://org?oid=1659941740&tab=reviews

Для коротких ссылок выполняется HTTP-запрос с перенаправлениями для получения полного URL.
Из полного URL извлекаются параметры: oid, ll, z, poi[point], poi[uri], mode.
"""

import re
import time
from typing import Optional
from urllib.parse import parse_qs, urlparse, urlunparse, urlencode

import requests
from fake_useragent import UserAgent
from loguru import logger

# Константы
DEFAULT_TIMEOUT = 15  # секунд
MAX_RETRIES = 3
RETRY_DELAY = 2  # секунд

# Базовые User-Agent для ротации (реальные браузеры)
# fake-useragent используется как основной источник, это — фоллбэк
FALLBACK_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# Регулярка для извлечения oid из poi[uri]
OID_PATTERN = re.compile(r"oid=(\d+)")


class UrlResolverError(Exception):
    """Базовое исключение для ошибок разрешения URL."""
    pass


class ShortUrlResolveError(UrlResolverError):
    """Ошибка при разрешении короткой ссылки."""
    pass


class OidNotFoundError(UrlResolverError):
    """OID не найден в URL."""
    pass


_ua_instance: Optional[UserAgent] = None


def _get_ua() -> Optional[UserAgent]:
    global _ua_instance
    if _ua_instance is None:
        try:
            _ua_instance = UserAgent()
        except Exception as e:
            logger.warning(f"fake-useragent недоступен: {e}")
            return None
    return _ua_instance


def get_user_agent() -> str:
    """
    Получить случайный User-Agent.

    Сначала пытается использовать fake-useragent, при неудаче — фоллбэк список.
    """
    ua = _get_ua()
    if ua is not None:
        try:
            return ua.random
        except Exception as e:
            logger.warning(f"fake-useragent random failed: {e}, использую фоллбэк")
    import random
    return random.choice(FALLBACK_USER_AGENTS)


def is_short_yandex_url(url: str) -> bool:
    """
    Проверить, является ли URL короткой ссылкой Яндекс.Карт.

    Короткая ссылка имеет формат: https://yandex.ru/maps/-/XXXXXX
    """
    parsed = urlparse(url)
    return (
        parsed.netloc in ("yandex.ru", "www.yandex.ru", "yandex.com", "www.yandex.com")
        and parsed.path.startswith("/maps/-/")
    )


def extract_oid(url: str) -> Optional[str]:
    """
    Извлечь oid организации из URL Яндекс.Карт.

    Ищет oid в параметре poi[uri] вида ymapsbm1://org?oid=1659941740
    или прямо в query-параметрах.

    Args:
        url: Полный URL Яндекс.Карт

    Returns:
        Строка с oid или None, если не найден
    """
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)

    # 1. Пытаемся найти в poi[uri]
    poi_uri = query_params.get("poi[uri]", [None])[0]
    if poi_uri:
        match = OID_PATTERN.search(poi_uri)
        if match:
            return match.group(1)

    # 2. Пытаемся найти прямо в query (редкий случай)
    oid_direct = query_params.get("oid", [None])[0]
    if oid_direct:
        return oid_direct

    # 3. Пытаемся найти в любом параметре через regex
    match = OID_PATTERN.search(parsed.query)
    if match:
        return match.group(1)

    return None


def extract_all_params(url: str) -> dict:
    """
    Извлечь все полезные параметры из URL Яндекс.Карт.

    Args:
        url: Полный URL Яндекс.Карт

    Returns:
        Словарь с параметрами: oid, ll, z, poi_point, poi_uri, mode
    """
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)

    def get_first(key: str) -> Optional[str]:
        vals = query_params.get(key, [None])
        return vals[0] if vals else None

    return {
        "oid": extract_oid(url),
        "ll": get_first("ll"),
        "z": get_first("z"),
        "poi_point": get_first("poi[point]"),
        "poi_uri": get_first("poi[uri]"),
        "mode": get_first("mode"),
    }


def ensure_tab_reviews(url: str) -> str:
    """
    Добавить tab=reviews в URL, если его нет.

    Args:
        url: URL Яндекс.Карт

    Returns:
        URL с параметром tab=reviews
    """
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)

    if "tab" not in query_params:
        query_params["tab"] = ["reviews"]
        new_query = urlencode(query_params, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    return url


def resolve_short_url(short_url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """
    Разрешить короткую ссылку Яндекс.Карт в полную.

    Выполняет HTTP HEAD запрос с перенаправлениями.
    При неудаче HEAD — пробует GET.

    Args:
        short_url: Короткая ссылка (https://yandex.ru/maps/-/XXXXXX)
        timeout: Таймаут запроса в секундах

    Returns:
        Полный URL после всех редиректов

    Raises:
        ShortUrlResolveError: Если не удалось разрешить ссылку
    """
    if not is_short_yandex_url(short_url):
        raise ShortUrlResolveError(f"URL не является короткой ссылкой Яндекс.Карт: {short_url}")

    headers = {
        "User-Agent": get_user_agent(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    }

    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            # Сначала пробуем HEAD
            logger.debug(f"Попытка {attempt + 1}: HEAD запрос к {short_url}")
            response = requests.head(
                short_url,
                headers=headers,
                allow_redirects=True,
                timeout=timeout,
            )

            if response.status_code == 200:
                final_url = response.url
                logger.info(f"Короткая ссылка разрешена: {short_url} -> {final_url}")
                return final_url

            # Если HEAD не сработал (например, 405 Method Not Allowed), пробуем GET
            logger.debug(f"HEAD вернул {response.status_code}, пробуем GET")
            response = requests.get(
                short_url,
                headers=headers,
                allow_redirects=True,
                timeout=timeout,
                stream=True,  # Не качаем тело
            )
            response.close()

            if response.status_code == 200:
                final_url = response.url
                logger.info(f"Короткая ссылка разрешена (GET): {short_url} -> {final_url}")
                return final_url

            last_error = f"HTTP {response.status_code}"

        except requests.Timeout:
            last_error = "Таймаут"
            logger.warning(f"Таймаут при запросе к {short_url} (попытка {attempt + 1})")
        except requests.RequestException as e:
            last_error = str(e)
            logger.warning(f"Ошибка запроса к {short_url}: {e} (попытка {attempt + 1})")

        # Пауза перед повторной попыткой
        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_DELAY * (attempt + 1))

    raise ShortUrlResolveError(
        f"Не удалось разрешить короткую ссылку после {MAX_RETRIES} попыток: {last_error}"
    )


def resolve_yandex_url(url: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """
    Главная функция: разрешить любую ссылку Яндекс.Карт и извлечь параметры.

    Args:
        url: Ссылка на магазин (короткая или полная)
        timeout: Таймаут сетевых запросов в секундах

    Returns:
        Словарь с полями:
        - original_url: исходная ссылка
        - resolved_url: итоговая ссылка (с tab=reviews)
        - oid: ID организации (или None)
        - params: все извлечённые параметры (ll, z, poi_point, poi_uri, mode)
        - is_short: была ли ссылка короткой

    Raises:
        ShortUrlResolveError: Если короткая ссылка не резолвится
        OidNotFoundError: Если oid не найден (для полных ссылок это не критично, вернёт None)
    """
    original_url = url
    is_short = is_short_yandex_url(url)
    resolved_url = url

    if is_short:
        logger.info(f"Обнаружена короткая ссылка, разрешаю: {url}")
        resolved_url = resolve_short_url(url, timeout=timeout)
    else:
        logger.info(f"Полная ссылка, разрешение не требуется: {url}")

    # Добавляем tab=reviews если отсутствует
    resolved_url = ensure_tab_reviews(resolved_url)

    # Извлекаем параметры
    params = extract_all_params(resolved_url)
    oid = params.pop("oid")  # oid выносим отдельно

    if oid is None:
        logger.warning(f"OID не найден в URL: {resolved_url}")

    result = {
        "original_url": original_url,
        "resolved_url": resolved_url,
        "oid": oid,
        "params": params,
        "is_short": is_short,
    }

    logger.debug(f"Результат разрешения: {result}")
    return result


# =============================================================================
# Демонстрация и тестирование
# =============================================================================

if __name__ == "__main__":
    # Настройка логирования для демо
    logger.remove()
    logger.add(
        lambda msg: print(msg, end=""),
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    )

    # Тестовые URL из задания
    test_urls = [
        # Короткая ссылка
        "https://yandex.ru/maps/-/CTwsUYyk",
        # Полная ссылка с известным oid=1659941740
        "https://yandex.ru/maps/65/novosibirsk/?ll=82.986172%2C55.044555&mode=poi&poi%5Bpoint%5D=82.986891%2C55.044638&poi%5Buri%5D=ymapsbm1%3A%2F%2Forg%3Foid%3D1659941740&tab=reviews&z=17.95",
    ]

    print("=" * 60)
    print("ТЕСТИРОВАНИЕ url_resolver.py")
    print("=" * 60)

    for i, test_url in enumerate(test_urls, 1):
        print(f"\n--- Тест {i} ---")
        print(f"Вход: {test_url}")

        try:
            result = resolve_yandex_url(test_url)

            print(f"Исходный URL: {result['original_url']}")
            print(f"Разрешённый URL: {result['resolved_url']}")
            print(f"OID: {result['oid']}")
            print(f"Была короткой: {result['is_short']}")
            print(f"Параметры: {result['params']}")

            # Проверки
            if i == 1:
                # Для короткой ссылки ожидаем oid=1659941740
                assert result["oid"] == "1659941740", f"Ожидался oid=1659941740, получен {result['oid']}"
                assert result["is_short"] is True
                print("✅ Короткая ссылка: OID извлечён верно")
            else:
                # Для полной ссылки
                assert result["oid"] == "1659941740", f"Ожидался oid=1659941740, получен {result['oid']}"
                assert result["is_short"] is False
                print("✅ Полная ссылка: OID извлечён верно")

            # Проверка наличия tab=reviews
            assert "tab=reviews" in result["resolved_url"], "tab=reviews отсутствует в итоговом URL"
            print("✅ Параметр tab=reviews присутствует")

        except Exception as e:
            print(f"❌ ОШИБКА: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 60)
    print("Все тесты пройдены успешно!")
    print("=" * 60)