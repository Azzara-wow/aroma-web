# users.py — aroma_web / покупатели: вход и профиль (Шаг 3).
#
# Отвечает ТОЛЬКО за личность покупателя: нормализация телефона, хеширование
# кода, регистрация, вход, сброс/установка кода, правка адреса/имени.
# Веб-слой (формы, куки, лимит попыток) — НЕ здесь, а в маршрутах.
#
# ХРАНЕНИЕ: база buyers.db (buyers_db.py). Глобальное (телефон, имя, код-хеш, ФИО, ПВЗ,
# перевозчик, e-mail) — таблица buyers; счёт текущей закупки — таблица bills, его
# присылает дашборд. Лист «Покупатели» в гугл-таблице больше НЕ источник: раз в сутки
# туда выгружается копия только для просмотра (buyers_sheet_io.py).
#
# Восстановление кода: организатор жмёт «Сбросить код» в дашборде. Тогда при следующем
# входе телефон найдётся, но кода нет -> человек задаёт код заново (set_code).

import os
import base64
import hashlib
import secrets

import buyers_db
import core

# Перевозчики, которых покупатель выбирает сам. Любое другое значение перевозчика
# («Почта России», «Озон», «Wildberries»…) вписывает ТОЛЬКО организатор — это ручная
# доставка по договорённости: вместо ПВЗ покупатель пишет свободный адрес (в поле ПВЗ-адреса).
SELF_CARRIERS = ("yandex", "cdek")


def manual_carrier(raw):
    """Название ручного перевозчика (как вписала организатор) или '' для Яндекса/СДЭКа."""
    v = (raw or "").strip()
    return "" if v.lower() in ("",) + SELF_CARRIERS else v


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
#  Доступ к базе
# ======================================================================

def _row_to_user(rec) -> dict:
    """Запись базы (покупатель + текущий счёт) -> словарь пользователя (код-хеш наружу
    не показываем, но он нужен маршруту входа, поэтому оставляем в 'code_hash')."""
    def c(k):
        return core.norm(rec.get(k))
    last, first, patr = c("last_name"), c("first_name"), c("patronymic")
    pvz_id = c("pvz_id")
    manual = manual_carrier(c("carrier"))
    return {
        "phone": rec["phone"],
        "name": c("name"),
        "code_hash": rec.get("code_hash") or "",
        "address": c("address"),
        "role": c("role") or ROLE_BUYER,
        "created": c("created"),
        "note": c("note"),
        # доставка (глобальное)
        "last_name": last,
        "first_name": first,
        "patronymic": patr,
        "city": c("city"),
        "pvz_address": c("pvz_address"),
        "pvz_id": pvz_id,
        "carrier": "manual" if manual else (c("carrier").lower() or "yandex"),
        "carrier_manual": manual,        # «Почта России» и т.п. — вписывает организатор
        "email": c("email"),
        # счёт текущей закупки (присылает дашборд)
        "tracking_url": c("tracking_url"),
        "pay_link": c("pay_link"),
        "pay_amount": c("pay_amount"),
        "pay_delivery": c("pay_delivery"),
        "paid": bool(rec.get("paid")),
        "pay_to": c("pay_to"),
        # заполнено, если есть Фамилия+Имя и выбран ПВЗ (отчество API не требует)
        # (ручная доставка: вместо ПВЗ — свободный адрес в pvz_address)
        "delivery_complete": bool(last and first and (c("pvz_address") if manual else pvz_id)),
    }


def get_user(phone_raw):
    """Пользователь по телефону -> dict или None."""
    canon = normalize_phone(phone_raw)
    if not valid_phone(canon):
        return None
    rec = buyers_db.get(canon)
    return _row_to_user(rec) if rec else None


def is_admin(user) -> bool:
    """Роль организатора? Заменяет прежнее секретное слово ADMIN_NAME."""
    return bool(user) and core.norm(user.get("role")).lower() == ROLE_ADMIN


def list_users():
    """Список зарегистрированных: [{'phone','name'}] (для выбора в админке)."""
    return [{"phone": r["phone"], "name": core.norm(r["name"]),
             # счёт из дашборда — для строки доставки на странице счетов
             "pay_delivery": core.norm(r["pay_delivery"]),
             "paid": bool(r["paid"])}
            for r in buyers_db.all_buyers() if valid_phone(r["phone"])]


def list_full():
    """Полный список: [{phone, name, role, has_code}] — для страницы покупателей."""
    out = [{"phone": r["phone"], "name": core.norm(r["name"]),
            "role": core.norm(r["role"]) or ROLE_BUYER,
            "has_code": (r["code_hash"] or "").startswith("pbkdf2")}
           for r in buyers_db.all_buyers() if valid_phone(r["phone"])]
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
    if not buyers_db.insert(canon, name=name, address=address or "", role=role):
        return {"ok": False, "reason": "Этот телефон уже есть в списке"}
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
    if not buyers_db.insert(canon, name=name, code_hash=hash_code(code),
                            address=address or "", role=ROLE_BUYER):
        return {"ok": False, "reason": "Этот телефон уже зарегистрирован"}
    return {"ok": True, "user": get_user(canon)}


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
    Задать/сменить код существующему телефону (после сброса организатором — кнопка
    «Сбросить код» в дашборде). Телефон должен уже существовать.
    """
    canon = normalize_phone(phone_raw)
    code = (code or "").strip()
    if len(code) < MIN_CODE_LEN:
        return {"ok": False, "reason": f"Код минимум {MIN_CODE_LEN} символа"}
    if not buyers_db.update(canon, code_hash=hash_code(code)):
        return {"ok": False, "reason": "not_found"}
    return {"ok": True}


def reset_code(phone_raw):
    """Сброс кода организатором: при следующем входе девочка задаст новый."""
    if not buyers_db.update(normalize_phone(phone_raw), code_hash=""):
        return {"ok": False, "reason": "not_found"}
    return {"ok": True}


def update_address(phone_raw, address):
    """Обновить адрес доставки (покупатель правит в профиле / при заказе)."""
    if not buyers_db.update(normalize_phone(phone_raw), address=address or ""):
        return {"ok": False, "reason": "not_found"}
    return {"ok": True}


def set_delivery(phone_raw, last_name="", first_name="", patronymic="",
                 city="", pvz_address="", pvz_id="", carrier=""):
    """Записать данные доставки (ФИО + город + ПВЗ) и перевозчика.
    E-mail сохраняется отдельно (set_email) — здесь не трогаем."""
    canon = normalize_phone(phone_raw)
    rec = buyers_db.get(canon)
    if rec is None:
        return {"ok": False, "reason": "not_found"}
    # Ручного перевозчика ставит только организатор: покупатель его не выберет и не
    # затрёт. У ручной доставки ПВЗ-id нет (адрес свободный) — чистим, чтобы старый
    # ПВЗ Яндекса/СДЭКа не ушёл в автоматическую отправку.
    fields = dict(last_name=last_name, first_name=first_name, patronymic=patronymic,
                  city=city, pvz_address=pvz_address, pvz_id=pvz_id)
    if manual_carrier(rec.get("carrier")):
        fields["pvz_id"] = ""
    else:
        c = (carrier or "").strip().lower()
        fields["carrier"] = c if c in SELF_CARRIERS else "yandex"
    buyers_db.update(canon, **fields)
    return {"ok": True}


def set_email(phone_raw, email=""):
    """Записать только e-mail покупателя. Отдельная кнопка на витрине."""
    if not buyers_db.update(normalize_phone(phone_raw), email=email or ""):
        return {"ok": False, "reason": "not_found"}
    return {"ok": True}
