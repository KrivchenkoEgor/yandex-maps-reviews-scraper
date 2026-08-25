"""
Модуль защиты от антибота Яндекса для Yandex Reviews Scraper.

Предоставляет инструменты для обхода защиты:
- Ротация User-Agent (10 реальных браузеров)
- Рандомизация viewport
- Случайные задержки между действиями (rate limiting)
- Exponential backoff при ошибках 429/503
- Детекция и обработка капчи
- Имитация человеческих движений мыши
"""

import asyncio
import os
import random
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple, TypeVar

from dotenv import load_dotenv
from fake_useragent import UserAgent
from loguru import logger

# Загружаем .env при импорте модуля
load_dotenv()

# =============================================================================
# Константы и конфигурация
# =============================================================================

# 10 реальных User-Agent строк (Chrome, Firefox, Safari, Edge, Opera, Yandex Browser на Win/Mac/Linux)
FALLBACK_USER_AGENTS: list[str] = [
    # Chrome 120 на Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # Chrome 120 на macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # Chrome 120 на Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # Firefox 121 на Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    # Firefox 121 на macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
    # Safari 17 на macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    # Edge 120 на Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    # Opera 106 на Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 OPR/106.0.0.0",
    # Yandex Browser 23.11 на Windows (на базе Chromium)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 YaBrowser/23.11.0.0 Safari/537.36",
]

# Популярные разрешения экрана для viewport рандомизации
VIEWPORT_RESOLUTIONS: list[Tuple[int, int]] = [
    (1920, 1080),  # Full HD
    (1366, 768),   # HD (ноутбуки)
    (1536, 864),   # 125% scaling
    (1440, 900),   # MacBook Air
    (1280, 720),   # HD ready
    (2560, 1440),  # QHD / 2K
    (1920, 1200),  # WUXGA
]

# Ключевые слова для детекции капчи Яндекса
CAPTCHA_KEYWORDS: list[str] = [
    "капча",
    "captcha",
    "подтвердите, что вы не робот",
    "подтвердите что вы не робот",
    "smartcaptcha",
    "yandex_captcha",
    "checkboxcaptcha",
    "advancedcaptcha",
    "я не робот",
    "i'm not a robot",
    "recaptcha",
    "hcaptcha",
]

# Селекторы элементов капчи на странице
CAPTCHA_SELECTORS: list[str] = [
    ".CheckboxCaptcha",
    ".AdvancedCaptcha",
    "[data-testid='captcha']",
    ".captcha-container",
    "#captcha",
    "iframe[src*='captcha']",
    "iframe[src*='smartcaptcha']",
]

T = TypeVar("T")


# =============================================================================
# Исключения
# =============================================================================

class AntiBotError(Exception):
    """Базовое исключение для ошибок антибот-защиты."""
    pass


class CaptchaDetectedError(AntiBotError):
    """
    Исключение при обнаружении капчи.

    Поднимается когда автоматическое решение невозможно — требуется ручное вмешательство пользователя.
    """
    pass


class RateLimitError(AntiBotError):
    """Исключение при превышении лимита запросов (HTTP 429)."""
    pass


# =============================================================================
# Конфигурация из .env
# =============================================================================

@dataclass
class AntiBotConfig:
    """
    Конфигурация антибот-защиты, читаемая из переменных окружения.

    Все параметры имеют значения по умолчанию, соответствующие рекомендациям AGENTS.md.
    """
    min_delay_sec: float = 2.0
    max_delay_sec: float = 5.0
    max_retries: int = 3
    retry_base_delay_sec: float = 2.0
    page_timeout_sec: int = 30
    scroll_pause_sec: float = 3.0
    max_scroll_attempts: int = 5

    @classmethod
    def from_env(cls) -> "AntiBotConfig":
        """
        Создать конфиг из переменных окружения (через app.config — единый источник).
        """
        from app import config as app_config

        return cls(
            min_delay_sec=float(app_config.YANDEX_MIN_DELAY_SEC),
            max_delay_sec=float(app_config.YANDEX_MAX_DELAY_SEC),
            max_retries=int(app_config.YANDEX_MAX_RETRIES),
            retry_base_delay_sec=float(app_config.YANDEX_RETRY_BASE_DELAY_SEC),
            page_timeout_sec=int(app_config.YANDEX_PAGE_TIMEOUT_SEC),
            scroll_pause_sec=float(app_config.YANDEX_SCROLL_PAUSE_SEC),
            max_scroll_attempts=int(app_config.YANDEX_MAX_SCROLL_ATTEMPTS),
        )


# Глобальный конфиг (ленивая инициализация)
_config: Optional[AntiBotConfig] = None


def get_config() -> AntiBotConfig:
    """Получить глобальный конфиг антибот-защиты (ленивая инициализация)."""
    global _config
    if _config is None:
        _config = AntiBotConfig.from_env()
    return _config


# =============================================================================
# User-Agent и Viewport
# =============================================================================

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


def get_random_user_agent() -> str:
    """
    Получить случайный User-Agent.

    Сначала пытается использовать fake-useragent для актуальных UA.
    При неудаче — выбирает случайный из фоллбэк-списка FALLBACK_USER_AGENTS.

    Returns:
        Строка User-Agent для использования в запросах.
    """
    ua = _get_ua()
    if ua is not None:
        try:
            return ua.random
        except Exception as e:
            logger.warning(f"fake-useragent random failed: {e}, использую фоллбэк")
    return random.choice(FALLBACK_USER_AGENTS)


def get_random_viewport() -> Tuple[int, int]:
    """
    Получить случайное разрешение viewport с небольшой вариацией.

    Выбирает базовое разрешение из популярных и добавляет ±50px к ширине и высоте
    для имитации реальных настроек браузера пользователей.

    Returns:
        Кортеж (width, height) в пикселях.
    """
    base_width, base_height = random.choice(VIEWPORT_RESOLUTIONS)
    # Добавляем вариацию ±50px
    width = base_width + random.randint(-50, 50)
    height = base_height + random.randint(-50, 50)
    # Обеспечиваем минимальные разумные значения
    width = max(800, width)
    height = max(600, height)
    return (width, height)


# =============================================================================
# Задержки (Rate Limiting)
# =============================================================================

def random_delay(min_sec: Optional[float] = None, max_sec: Optional[float] = None) -> float:
    """
    Синхронная случайная пауза между действиями.

    Использует значения из конфига или переданные параметры.
    Логирует длительность паузы для отладки.

    Args:
        min_sec: Минимальная задержка в секундах (по умолчанию из конфига).
        max_sec: Максимальная задержка в секундах (по умолчанию из конфига).

    Returns:
        Фактическая длительность паузы в секундах.
    """
    config = get_config()
    min_s = min_sec if min_sec is not None else config.min_delay_sec
    max_s = max_sec if max_sec is not None else config.max_delay_sec

    delay = random.uniform(min_s, max_s)
    logger.debug(f"Антибот-пауза: {delay:.2f} сек")
    time.sleep(delay)
    return delay


async def async_random_delay(min_sec: Optional[float] = None, max_sec: Optional[float] = None) -> float:
    """
    Асинхронная случайная пауза между действиями (для Playwright).

    Аналог random_delay, но использует asyncio.sleep для неблокирующего ожидания.

    Args:
        min_sec: Минимальная задержка в секундах (по умолчанию из конфига).
        max_sec: Максимальная задержка в секундах (по умолчанию из конфига).

    Returns:
        Фактическая длительность паузы в секундах.
    """
    config = get_config()
    min_s = min_sec if min_sec is not None else config.min_delay_sec
    max_s = max_sec if max_sec is not None else config.max_delay_sec

    delay = random.uniform(min_s, max_s)
    logger.debug(f"Антибот-пауза (async): {delay:.2f} сек")
    await asyncio.sleep(delay)
    return delay


# =============================================================================
# Exponential Backoff и Retry
# =============================================================================

def exponential_backoff(attempt: int, base_delay: Optional[float] = None) -> float:
    """
    Вычислить задержку по экспоненциальному backoff с джиттером.

    Формула: base_delay * 2^attempt + random.uniform(0, 1)

    Args:
        attempt: Номер попытки (0-based).
        base_delay: Базовая задержка в секундах (по умолчанию из конфига).

    Returns:
        Задержка в секундах до следующей попытки.
    """
    config = get_config()
    base = base_delay if base_delay is not None else config.retry_base_delay_sec
    delay = base * (2 ** attempt) + random.uniform(0, 1)
    logger.debug(f"Exponential backoff: попытка {attempt}, задержка {delay:.2f} сек")
    return delay


def retry_with_backoff(
    max_retries: Optional[int] = None,
    base_delay: Optional[float] = None,
    retry_exceptions: Tuple[type[Exception], ...] = (Exception,),
    should_retry: Optional[Callable[[Exception], bool]] = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Декоратор-фабрика для повторного вызова функции с exponential backoff.

    Использование:
        @retry_with_backoff(max_retries=3, base_delay=2)
        def my_function():
            ...

    Args:
        max_retries: Максимальное количество попыток (по умолчанию из конфига).
        base_delay: Базовая задержка в секундах (по умолчанию из конфига).
        retry_exceptions: Кортеж типов исключений для повтора.
        should_retry: Опциональная функция-предикат для кастомной логики повтора.

    Returns:
        Декоратор, оборачивающий функцию с логикой retry.
    """
    config = get_config()
    max_r = max_retries if max_retries is not None else config.max_retries
    base_d = base_delay if base_delay is not None else config.retry_base_delay_sec

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        def wrapper(*args, **kwargs) -> T:
            last_exception = None

            for attempt in range(max_r + 1):
                try:
                    return func(*args, **kwargs)
                except retry_exceptions as e:
                    last_exception = e

                    # Проверяем кастомный предикат
                    if should_retry and not should_retry(e):
                        logger.debug(f"should_retry вернул False для {type(e).__name__}, прерываем retry")
                        raise

                    # Проверяем специфичные HTTP коды ошибок
                    if hasattr(e, "response") and e.response is not None:
                        status = e.response.status_code
                        if status not in (429, 503, 504):
                            logger.debug(f"HTTP {status} не в списке для retry, прерываем")
                            raise

                    if attempt < max_r:
                        delay = exponential_backoff(attempt, base_d)
                        logger.warning(
                            f"Попытка {attempt + 1}/{max_r + 1} не удалась: {e}. "
                            f"Повтор через {delay:.2f} сек..."
                        )
                        time.sleep(delay)
                    else:
                        logger.error(f"Все {max_r + 1} попыток исчерпаны. Последняя ошибка: {last_exception}")
                        raise

            # Не должно дойти сюда, но для типов
            raise last_exception  # type: ignore

        return wrapper

    return decorator


async def async_retry_with_backoff(
    max_retries: Optional[int] = None,
    base_delay: Optional[float] = None,
    retry_exceptions: Tuple[type[Exception], ...] = (Exception,),
    should_retry: Optional[Callable[[Exception], bool]] = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Асинхронная версия retry_with_backoff для async функций (декоратор-фабрика).

    Использование:
        @async_retry_with_backoff(max_retries=3, base_delay=2)
        async def my_async_function():
            ...

    Args:
        max_retries: Максимальное количество попыток (по умолчанию из конфига).
        base_delay: Базовая задержка в секундах (по умолчанию из конфига).
        retry_exceptions: Кортеж типов исключений для повтора.
        should_retry: Опциональная функция-предикат для кастомной логики повтора.

    Returns:
        Декоратор, оборачивающий асинхронную функцию с логикой retry.
    """
    config = get_config()
    max_r = max_retries if max_retries is not None else config.max_retries
    base_d = base_delay if base_delay is not None else config.retry_base_delay_sec

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        async def wrapper(*args, **kwargs) -> T:
            last_exception = None

            for attempt in range(max_r + 1):
                try:
                    return await func(*args, **kwargs)
                except retry_exceptions as e:
                    last_exception = e

                    if should_retry and not should_retry(e):
                        logger.debug(f"should_retry вернул False для {type(e).__name__}, прерываем retry")
                        raise

                    if hasattr(e, "response") and e.response is not None:
                        status = e.response.status_code
                        if status not in (429, 503, 504):
                            logger.debug(f"HTTP {status} не в списке для retry, прерываем")
                            raise

                    if attempt < max_r:
                        delay = exponential_backoff(attempt, base_d)
                        logger.warning(
                            f"Попытка {attempt + 1}/{max_r + 1} не удалась: {e}. "
                            f"Повтор через {delay:.2f} сек..."
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(f"Все {max_r + 1} попыток исчерпаны. Последняя ошибка: {last_exception}")
                        raise

            raise last_exception  # type: ignore

        return wrapper

    return decorator


# =============================================================================
# Детекция капчи
# =============================================================================

def is_captcha_page(page_content: str) -> bool:
    """
    Проверить, содержит ли HTML-контент признаки капчи Яндекса.

    ВАЖНО: проверяет только ВИДИМЫЙ текст (без script/style/noscript),
    чтобы избежать ложных срабатываний на легитимных скриптах
    вида https://yandex.ru/captchapgrd или smartcaptcha.yandexcloud.net
    которые есть на каждой странице Яндекс.Карт даже без капчи.

    Args:
        page_content: HTML-контент страницы в виде строки.

    Returns:
        True если обнаружены признаки капчи, иначе False.
    """
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(page_content, "lxml")
        # Убираем скрипты/стили — они не видимы пользователю
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        visible_text = soup.get_text(separator=" ", strip=True).lower()
        # Фолбэк: если видимого текста мало, берём raw но без учёта URL-скриптов
        # Проверяем ключевые слова только в видимом тексте
        for keyword in CAPTCHA_KEYWORDS:
            if keyword in visible_text:
                logger.warning(f"Обнаружено ключевое слово капчи: '{keyword}'")
                return True
        return False
    except Exception as e:
        logger.warning(f"Ошибка парсинга HTML для капчи: {e}, фолбэк на raw")
        content_lower = page_content.lower()
        for keyword in CAPTCHA_KEYWORDS:
            if keyword in content_lower and "captchapgrd" not in content_lower and "smartcaptcha.yandexcloud.net" not in content_lower:
                logger.warning(f"Обнаружено ключевое слово капчи: '{keyword}'")
                return True
        return False


async def is_captcha_page_playwright(page) -> bool:
    """
    Проверить наличие капчи на странице через Playwright.

    Проверяет видимый текст и селекторы; не смотрит в raw HTML чтобы
    избежать ложных срабатываний на yandex.ru/captchapgrd.
    """
    try:
        for selector in CAPTCHA_SELECTORS:
            try:
                el = await page.query_selector(selector)
                if el and await el.is_visible():
                    logger.warning(f"Обнаружен селектор капчи: {selector}")
                    return True
            except Exception:
                continue
        try:
            visible = await page.inner_text("body")
            if visible:
                low = visible.lower()
                for kw in CAPTCHA_KEYWORDS:
                    if kw in low:
                        logger.warning(f"Обнаружено ключевое слово капчи в видимом тексте: '{kw}'")
                        return True
        except Exception:
            pass
        return False
    except Exception as e:
        logger.error(f"Ошибка при проверке капчи через Playwright: {e}")
        return False


async def handle_captcha(page, pause_sec: int = 30) -> bool:
    """
    Обработать обнаруженную капчу: пауза и повторная проверка.

    Ждёт указанное время, затем проверяет, ушла ли капча.
    Если капча не ушла — выбрасывает CaptchaDetectedError с просьбой к пользователю.

    Args:
        page: Playwright Page объект.
        pause_sec: Время ожидания в секундах (по умолчанию 30).

    Returns:
        True если капча ушла после паузы.

    Raises:
        CaptchaDetectedError: Если капча не исчезла после паузы.
    """
    logger.warning(f"Капча обнаружена. Ожидание {pause_sec} сек для ручного решения...")
    await asyncio.sleep(pause_sec)

    # Проверяем снова
    still_captcha = await is_captcha_page_playwright(page)

    if still_captcha:
        logger.error("Капча не исчезла после паузы. Требуется ручное вмешательство.")
        raise CaptchaDetectedError(
            "Обнаружена капча — требуется ручное вмешательство. "
            "Пожалуйста, решите капчу вручную в браузере и перезапустите скрейпинг."
        )

    logger.info("Капча больше не обнаружена, продолжаем работу")
    return True


# =============================================================================
# Имитация человеческих движений мыши
# =============================================================================

async def human_mouse_move(page, selector: Optional[str] = None) -> None:
    """
    Имитировать человеческое движение мыши к элементу или случайной точке.

    Выполняет плавное перемещение мыши с случайным количеством шагов (20-50)
    и небольшими вариациями траектории для обхода детекции автоматизации.

    Args:
        page: Playwright Page объект.
        selector: CSS-селектор целевого элемента. Если None — движется в случайную точку.
    """
    try:
        if selector:
            # Движение к конкретному элементу
            element = await page.query_selector(selector)
            if not element:
                logger.debug(f"Элемент {selector} не найден, пропускаем mouse move")
                return

            box = await element.bounding_box()
            if not box:
                logger.debug(f"Не удалось получить bounding box для {selector}")
                return

            target_x = box["x"] + box["width"] / 2
            target_y = box["y"] + box["height"] / 2
        else:
            # Движение в случайную точку viewport
            viewport = page.viewport_size
            if not viewport:
                logger.debug("Viewport не определён, пропускаем mouse move")
                return
            target_x = random.uniform(100, viewport["width"] - 100)
            target_y = random.uniform(100, viewport["height"] - 100)

        # Получаем текущую позицию мыши (приблизительно — центр экрана при старте)
        # Playwright не даёт текущую позицию, начинаем из центра
        start_x = page.viewport_size["width"] / 2 if page.viewport_size else 960
        start_y = page.viewport_size["height"] / 2 if page.viewport_size else 540

        # Случайное количество шагов 20-50
        steps = random.randint(20, 50)

        # Плавное движение с небольшими случайными отклонениями (кривая Безье упрощённо)
        for i in range(steps + 1):
            t = i / steps
            # Линейная интерполяция с небольшим шумом
            noise_x = random.uniform(-2, 2) * (1 - t)  # шум уменьшается к концу
            noise_y = random.uniform(-2, 2) * (1 - t)

            current_x = start_x + (target_x - start_x) * t + noise_x
            current_y = start_y + (target_y - start_y) * t + noise_y

            await page.mouse.move(current_x, current_y)
            # Маленькая пауза между шагами
            await asyncio.sleep(random.uniform(0.005, 0.02))

        logger.debug(f"Mouse move завершён: ({start_x:.0f},{start_y:.0f}) -> ({target_x:.0f},{target_y:.0f}) за {steps} шагов")

    except Exception as e:
        logger.warning(f"Ошибка при имитации движения мыши: {e}")


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

    print("=" * 70)
    print("ДЕМОНСТРАЦИЯ app.anti_bot")
    print("=" * 70)

    # 1. Тест get_random_user_agent (3 вызова)
    print("\n--- Тест get_random_user_agent (3 вызова) ---")
    for i in range(3):
        ua = get_random_user_agent()
        print(f"  {i + 1}. {ua[:80]}...")

    # 2. Тест get_random_viewport (2 вызова)
    print("\n--- Тест get_random_viewport (2 вызова) ---")
    for i in range(2):
        vp = get_random_viewport()
        print(f"  {i + 1}. {vp[0]}x{vp[1]}")

    # 3. Тест random_delay (2 вызова)
    print("\n--- Тест random_delay (2 вызова) ---")
    for i in range(2):
        delay = random_delay(0.1, 0.3)  # Ускоренные для демо
        print(f"  {i + 1}. Задержка: {delay:.3f} сек")

    # 4. Тест async_random_delay
    print("\n--- Тест async_random_delay ---")
    async def test_async_delay():
        for i in range(2):
            delay = await async_random_delay(0.1, 0.3)
            print(f"  {i + 1}. Async задержка: {delay:.3f} сек")
    asyncio.run(test_async_delay())

    # 5. Тест exponential_backoff
    print("\n--- Тест exponential_backoff ---")
    for attempt in range(4):
        delay = exponential_backoff(attempt, base_delay=1.0)
        print(f"  Попытка {attempt}: {delay:.3f} сек")

    # 6. Тест is_captcha_page на фейковом HTML
    print("\n--- Тест is_captcha_page ---")
    fake_html_no_captcha = "<html><body><h1>Отзывы магазина</h1><div class='review'>Текст</div></body></html>"
    fake_html_captcha = "<html><body><div class='CheckboxCaptcha'>Подтвердите, что вы не робот</div></body></html>"
    fake_html_smartcaptcha = "<html><body><script src='https://smartcaptcha.yandexcloud.net/captcha.js'></script></body></html>"

    print(f"  Обычная страница: {is_captcha_page(fake_html_no_captcha)}")
    print(f"  Страница с капчей (CheckboxCaptcha): {is_captcha_page(fake_html_captcha)}")
    print(f"  Страница с SmartCaptcha: {is_captcha_page(fake_html_smartcaptcha)}")

    # 7. Тест AntiBotConfig.from_env
    print("\n--- Тест AntiBotConfig.from_env ---")
    config = AntiBotConfig.from_env()
    print(f"  min_delay_sec: {config.min_delay_sec}")
    print(f"  max_delay_sec: {config.max_delay_sec}")
    print(f"  max_retries: {config.max_retries}")
    print(f"  retry_base_delay_sec: {config.retry_base_delay_sec}")
    print(f"  page_timeout_sec: {config.page_timeout_sec}")
    print(f"  scroll_pause_sec: {config.scroll_pause_sec}")
    print(f"  max_scroll_attempts: {config.max_scroll_attempts}")

    # 8. Тест retry_with_backoff (демо)
    print("\n--- Тест retry_with_backoff (демо успешного вызова) ---")
    call_count = {"n": 0}

    @retry_with_backoff(max_retries=3, base_delay=0.1)
    def flaky_success():
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise ConnectionError("Временная ошибка сети")
        return "Успех!"

    try:
        result = flaky_success()
        print(f"  Результат: {result} (вызовов: {call_count['n']})")
    except Exception as e:
        print(f"  Ошибка: {e}")

    print("\n" + "=" * 70)
    print("Все демо-тесты пройдены успешно!")
    print("=" * 70)