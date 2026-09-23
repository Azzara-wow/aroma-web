# orders_state.py — aroma_web / приём заказов открыт/закрыт.
#
# Состояние в файле (переживает рестарт), путь вне git-репозитория.
# Нет файла -> ОТКРЫТО (значение по умолчанию). Файл со словом "closed" -> ЗАКРЫТО.
# Переключается кнопкой в организаторской, без правки сервера и без рестарта.

import os

FLAG_PATH = os.environ.get("ORDERS_FLAG_PATH", "/etc/aroma-web-orders.flag")


def is_open() -> bool:
    """Приём заказов открыт? (по умолчанию — да)."""
    try:
        with open(FLAG_PATH, encoding="utf-8") as f:
            return f.read().strip().lower() != "closed"
    except FileNotFoundError:
        return True
    except Exception:
        return True   # при любой ошибке чтения — не блокируем витрину


def set_open(open_: bool) -> bool:
    """Открыть (True) или закрыть (False) приём заказов. Возвращает успех записи."""
    try:
        if open_:
            if os.path.exists(FLAG_PATH):
                os.remove(FLAG_PATH)
        else:
            with open(FLAG_PATH, "w", encoding="utf-8") as f:
                f.write("closed")
        return True
    except Exception:
        return False
