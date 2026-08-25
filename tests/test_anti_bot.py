from app.anti_bot import is_captcha_page


def test_is_captcha_page_visible():
    html_no_captcha = "<html><body><h1>Отзывы магазина</h1><div class='review'>Текст отзыва</div></body></html>"
    html_captcha = "<html><body><div class='CheckboxCaptcha'>Подтвердите, что вы не робот</div></body></html>"
    html_smartcaptcha_script = "<html><body><script src='https://smartcaptcha.yandexcloud.net/captcha.js'></script><h1>Отзывы</h1></body></html>"

    assert is_captcha_page(html_no_captcha) is False
    assert is_captcha_page(html_captcha) is True
    assert is_captcha_page(html_smartcaptcha_script) is False


def test_is_captcha_page_captchapgrd():
    html_with_captchapgrd = "<html><body><script src='https://yandex.ru/captchapgrd?key=abc'></script><div>Отзывы</div></body></html>"
    assert is_captcha_page(html_with_captchapgrd) is False
