# users.py — aroma_web / лист "Пользователи" (Шаг 3).
#
# Отвечает ТОЛЬКО за личность покупателя: нормализация телефона, хеширование
# кода, регистрация, вход, сброс/установка кода, правка адреса/имени.
# Веб-слой (формы, куки, лимит попыток) — НЕ здесь, а в маршрутах.
#
# ПРИВАТНОСТЬ: телефоны, адреса и хеши кодов вынесены в ОТДЕЛЬНУЮ приватную книгу
# «Покупатели» (USERS_URL), не в книге закупки. Читаем/пишем только через сервисный
# аккаунт. Публичный CSV для Пользователей не используется никогда.
#
# КОНТРАКТ ЛИСТА (позиции, 0-индексация; шапка в строке 1):
#   0  A — телефон (канон 7XXXXXXXXXX) — КЛЮЧ личности
#   1  B — имя (только отображение, не ключ)
#   2  C — код-хеш (самоописывающаяся строка pbkdf2_sha256$iters$salt$hash)
#   3  D — адрес / доставка (свободный текст)
#   4  E — роль (покупатель / организатор)
#   5  F — создан (дата-время регистрации)
#   6  G — заметка организатора (свободное поле для Елены)
#
# Восстановление кода: организатор вручную ОЧИЩАЕТ ячейку C у нужной строки.
# Тогда при следующем входе телефон найдётся, но кода нет -> человек задаёт код
# заново (set_code). SMS/почты у нас нет — это осознанно ручной путь.

import os
import base64
import hashlib
import secrets
from datetime import datetime

import core
import sheets

# Пользователи живут в ОТДЕЛЬНОЙ книге «Покупатели» (не в книге закупки).
USERS_URL = "https://docs.google.com/spreadsheets/d/15PjPHqSl6Iju41VIZOGkCMwy4kyBomr_X9F60hYwo0U/edit"
USERS_SHEET_NAME = "Пользователи"

# --- позиции столбцов ---
COL_PHONE = 0
COL_NAME = 1
COL_CODE_HASH = 2
COL_ADDRESS = 3
COL_ROLE = 4
COL_CREATED = 5
COL_NOTE = 6

HEADER = ["телефон", "имя", "код-хеш", "адрес", "роль", "создан", "заметка"]

ROLE_BUYER = "покупатель"
ROLE_ADMIN = "организатор"

MIN_CODE_LEN = 4
PBKDF2_ITERATIONS = 260000  # можно поднять в будущем: старые записи хранят своё число внутри


# ======================================================================
#  Телефон: нормализация к канону 7XXXXXXXXXX
# ======================================================================

def normalize_phone(raw) -> str:
    """
    Приводим любой ввод к канону: 11 цифр, ведущая 7.
      +7 900 123-45-67 -> 79001234567
      8(900)1234567    -> 79001234567
      9001234567       -> 79001234567 (10 цифр без кода страны)
    Возвращаем строку из цифр (может быть невалидной — проверяй valid_phone).
    """
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    elif len(digits) == 10:  # без кода страны -> добавляем 7
        digits = "7" + digits
    return digits


def valid_phone(canon: str) -> bool:
    """Канон валиден, если это ровно 11 цифр с ведущей 7."""
    return len(canon) == 11 and canon[0] == "7" and canon.isdigit()


# ======================================================================
#  Код: хеширование через pbkdf2 (стандартная библиотека, без зависимостей)
# ======================================================================

def hash_code(code: str) -> str:
    """
    Хеш кода в самоописывающейся строке:
        pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>
    Соль у каждого своя; число итераций хранится внутри -> можно менять со временем.
    """
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", code.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    )


def verify_code(code: str, stored: str) -> bool:
    """Сверить введённый код с хранимым хешем. Сравнение постоянного времени."""
    try:
        algo, iters, salt_b64, hash_b64 = str(stored).split("$")
        if algo != "pbkdf2_sha256":
            return False
        iters = int(iters)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, AttributeError, TypeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", code.encode("utf-8"), salt, iters)
    return secrets.compare_digest(dk, expected)


# ======================================================================
#  Доступ к листу
# ======================================================================

def _ws():
    """Лист 'Пользователи' в книге «Покупатели» (создаётся с шапкой, если его нет)."""
    return sheets.get_or_create_ws(USERS_SHEET_NAME, HEADER, USERS_URL)


def _find_row(values, canon: str):
    """0-индекс строки пользователя в values по канону телефона. None если нет."""
    for r in range(1, len(values)):  # строка 0 — шапка
        stored = values[r][COL_PHONE] if COL_PHONE < len(values[r]) else ""
        if normalize_phone(stored) == canon:
            return r
    return None


def _row_to_user(row, idx: int) -> dict:
    """Строка листа -> словарь пользователя (код-хеш наружу не отдаём как значение
    для показа, но он нужен маршруту входа, поэтому оставляем в 'code_hash')."""
    def c(i):
        return core.norm(row[i]) if i < len(row) else ""
    return {
        "row": idx,                       # 0-индекс в values (для точечной правки)
        "phone": normalize_phone(c(COL_PHONE)),
        "name": c(COL_NAME),
        "code_hash": c(COL_CODE_HASH),
        "address": c(COL_ADDRESS),
        "role": c(COL_ROLE) or ROLE_BUYER,
        "created": c(COL_CREATED),
        "note": c(COL_NOTE),
    }


def get_user(phone_raw):
    """Пользователь по телефону -> dict или None."""
    canon = normalize_phone(phone_raw)
    if not valid_phone(canon):
        return None
    values = _ws().get_all_values()
    idx = _find_row(values, canon)
    return _row_to_user(values[idx], idx) if idx is not None else None


def is_admin(user) -> bool:
    """Роль организатора? Заменяет прежнее секретное слово ADMIN_NAME."""
    return bool(user) and core.norm(user.get("role")).lower() == ROLE_ADMIN


def list_users():
    """Список зарегистрированных: [{'phone','name'}] (для выбора в админке)."""
    values = _ws().get_all_values()
    out = []
    for r in range(1, len(values)):
        row = values[r]
        phone = normalize_phone(row[COL_PHONE] if COL_PHONE < len(row) else "")
        if not valid_phone(phone):
            continue
        name = core.norm(row[COL_NAME]) if COL_NAME < len(row) else ""
        out.append({"phone": phone, "name": name})
    return out


def list_full():
    """Полный список: [{phone, name, role, has_code}] — для страницы покупателей."""
    values = _ws().get_all_values()
    out = []
    for r in range(1, len(values)):
        row = values[r]
        phone = normalize_phone(row[COL_PHONE] if COL_PHONE < len(row) else "")
        if not valid_phone(phone):
            continue
        code = core.norm(row[COL_CODE_HASH]) if COL_CODE_HASH < len(row) else ""
        out.append({
            "phone": phone,
            "name": core.norm(row[COL_NAME]) if COL_NAME < len(row) else "",
            "role": (core.norm(row[COL_ROLE]) if COL_ROLE < len(row) else "") or ROLE_BUYER,
            "has_code": code.startswith("pbkdf2"),
        })
    out.sort(key=lambda x: x["name"].lower())
    return out


def add_buyer(phone_raw, name, address="", role=ROLE_BUYER):
    """
    Предзавести покупателя (организатором): телефон + имя, БЕЗ кода.
    Код покупатель задаёт сам при первом входе (verify_login -> no_code -> set_code).
    """
    canon = normalize_phone(phone_raw)
    if not valid_phone(canon):
        return {"ok": False, "reason": "Телефон в формате 7XXXXXXXXXX"}
    name = (name or "").strip()
    if not name:
        return {"ok": False, "reason": "Укажите имя"}

    ws = _ws()
    values = ws.get_all_values()
    if _find_row(values, canon) is not None:
        return {"ok": False, "reason": "Этот телефон уже есть в списке"}

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [canon, name, "", (address or "").strip(), role, stamp, ""]  # код пустой
    ws.append_row(row, value_input_option="RAW")
    return {"ok": True, "phone": canon, "name": name}


# ======================================================================
#  Операции
# ======================================================================

def register(phone_raw, name, code, address=""):
    """
    Регистрация нового покупателя.
    Возвращает {"ok": True, "user": {...}} либо {"ok": False, "reason": "..."}.
    """
    canon = normalize_phone(phone_raw)
    if not valid_phone(canon):
        return {"ok": False, "reason": "Телефон в формате 7XXXXXXXXXX"}
    name = (name or "").strip()
    if not name:
        return {"ok": False, "reason": "Укажите имя"}
    code = (code or "").strip()
    if len(code) < MIN_CODE_LEN:
        return {"ok": False, "reason": f"Код минимум {MIN_CODE_LEN} символа"}

    ws = _ws()
    values = ws.get_all_values()
    if _find_row(values, canon) is not None:
        return {"ok": False, "reason": "Этот телефон уже зарегистрирован"}

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [canon, name, hash_code(code), (address or "").strip(), ROLE_BUYER, stamp, ""]
    # RAW: пишем как есть, без интерпретации формул; телефон из 11 цифр Sheets
    # хранит как целое и отдаёт обратно теми же цифрами — normalize_phone это стерпит.
    ws.append_row(row, value_input_option="RAW")
    return {"ok": True, "user": _row_to_user(row, len(values))}


def verify_login(phone_raw, code):
    """
    Проверка входа по телефону+коду.
    Успех:            {"ok": True, "user": {...}}
    Нет телефона:     {"ok": False, "reason": "not_found"}
    Код сброшен:      {"ok": False, "reason": "no_code"}   -> предложить set_code
    Неверный код:     {"ok": False, "reason": "bad_code"}
    """
    user = get_user(phone_raw)
    if user is None:
        return {"ok": False, "reason": "not_found"}
    if not user["code_hash"]:
        return {"ok": False, "reason": "no_code"}
    if not verify_code((code or "").strip(), user["code_hash"]):
        return {"ok": False, "reason": "bad_code"}
    return {"ok": True, "user": user}


def set_code(phone_raw, code):
    """
    Задать/сменить код существующему телефону (используется после ручного сброса
    организатором — очистки ячейки C). Телефон должен уже существовать.
    """
    canon = normalize_phone(phone_raw)
    code = (code or "").strip()
    if len(code) < MIN_CODE_LEN:
        return {"ok": False, "reason": f"Код минимум {MIN_CODE_LEN} символа"}

    ws = _ws()
    values = ws.get_all_values()
    idx = _find_row(values, canon)
    if idx is None:
        return {"ok": False, "reason": "not_found"}

    a1 = f"{sheets.col_a1(COL_CODE_HASH)}{idx + 1}"  # +1: gspread 1-индекс строки
    ws.update_acell(a1, hash_code(code))
    return {"ok": True}


def update_address(phone_raw, address):
    """Обновить адрес доставки (покупатель правит в профиле / при заказе)."""
    canon = normalize_phone(phone_raw)
    ws = _ws()
    values = ws.get_all_values()
    idx = _find_row(values, canon)
    if idx is None:
        return {"ok": False, "reason": "not_found"}
    a1 = f"{sheets.col_a1(COL_ADDRESS)}{idx + 1}"
    ws.update_acell(a1, (address or "").strip())
    return {"ok": True}
