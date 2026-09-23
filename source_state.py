# source_state.py — aroma_web / активная ссылка на книгу закупки.
#
# Раньше ссылка была захардкожена в core.SHEET_URL и менялась правкой кода на
# старте каждого круга. Теперь текущая книга закупки хранится в файле-настройке
# (переживает рестарт, лежит вне git). Меняется кнопкой в организаторской.
#
# Нет файла / пусто / мусор -> отдаём default (константа из кода, подстраховка).

import os
from urllib.parse import urlparse

URL_PATH = os.environ.get("AROMA_SOURCE_PATH", "/etc/aroma-web-source.url")


def valid_url(url: str) -> bool:
    """Похоже ли на ссылку Google Sheets (docs.google.com/spreadsheets/d/<id>)."""
    try:
        p = urlparse((url or "").strip())
        return p.scheme in ("http", "https") \
            and "docs.google.com" in p.netloc \
            and "/spreadsheets/d/" in p.path
    except Exception:
        return False


def get_url(default: str = "") -> str:
    """Активная ссылка закупки; при отсутствии/ошибке — default (обычно core.SHEET_URL)."""
    try:
        with open(URL_PATH, encoding="utf-8") as f:
            url = f.read().strip()
        return url if valid_url(url) else default
    except FileNotFoundError:
        return default
    except Exception:
        return default


def is_custom() -> bool:
    """Задана ли своя ссылка (а не дефолт из кода)?"""
    try:
        with open(URL_PATH, encoding="utf-8") as f:
            return valid_url(f.read().strip())
    except Exception:
        return False


def set_url(url: str) -> bool:
    """Записать активную ссылку закупки. Возвращает успех (валидна и записана)."""
    url = (url or "").strip()
    if not valid_url(url):
        return False
    try:
        with open(URL_PATH, "w", encoding="utf-8") as f:
            f.write(url)
        return True
    except Exception:
        return False


def reset() -> bool:
    """Вернуться на ссылку из кода (удалить файл-настройку)."""
    try:
        if os.path.exists(URL_PATH):
            os.remove(URL_PATH)
        return True
    except Exception:
        return False
