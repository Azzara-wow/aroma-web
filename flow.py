# flow.py — aroma_web / лист "Поток" (Шаг 3).
#
# Поток — ЕДИНСТВЕННЫЙ источник заказов (матрицы больше нет). Каждый заказ —
# отдельная строка (плюс/минус), как в прежнем "Журнале", но ключ покупателя —
# ТЕЛЕФОН (стабильный), а имя пишется СНИМКОМ на момент заказа.
#
# "Набрано по аромату", "Моё" и позиции для счёта считаются свёрткой Потока
# (сумма плюс/минус по нужному срезу). Ничего не пересчитываем в клетки — считаем
# на лету при чтении.
#
# КОНТРАКТ ЛИСТА (позиции, 0-индексация; шапка в строке 1):
#   0  A — дата-время
#   1  B — телефон (канон 7XXXXXXXXXX) — КЛЮЧ покупателя
#   2  C — имя (снимок на момент заказа)
#   3  D — аромат
#   4  E — объём (мл)
#   5  F — направление (плюс / минус)  — 'минус' вычитает, всё прочее прибавляет
#   6  G — тег (основной / добор)
#
# Пишем/читаем только через сервисный аккаунт (книга приватная).

from datetime import datetime

import core
import sheets
import users

FLOW_SHEET_NAME = "Поток"

# --- позиции столбцов ---
COL_TS = 0
COL_PHONE = 1
COL_NAME = 2
COL_AROMA = 3
COL_VOLUME = 4
COL_DIRECTION = 5
COL_TAG = 6
NCOLS = 7

HEADER = ["дата-время", "телефон", "имя", "аромат", "объём", "направление", "тег"]

DIR_MINUS = "минус"
DIR_PLUS = "плюс"
TAG_MAIN = "основной"
TAG_EXTRA = "добор"


# ======================================================================
#  Доступ к листу
# ======================================================================

def _ws():
    """Лист 'Поток' (создаётся с шапкой, если его ещё нет)."""
    return sheets.get_or_create_ws(FLOW_SHEET_NAME, HEADER)


def _pad(row):
    """Добить строку до NCOLS пустыми — чтобы не ловить IndexError на рваных строках."""
    return row + [""] * (NCOLS - len(row))


# ======================================================================
#  Свёртка Потока
# ======================================================================

def _aggregate(values):
    """
    Свернуть Поток в чистые остатки.
    Возвращает dict: phone -> {"name": <последнее непустое имя>,
                              "aromas": {аромат: net_ml (>0)}}.
    'минус' вычитает, всё прочее прибавляет; позиции с net<=0 отбрасываем.
    """
    agg = {}
    for r in range(1, len(values)):  # строка 0 — шапка
        row = _pad(values[r])
        phone = users.normalize_phone(row[COL_PHONE])
        if not users.valid_phone(phone):
            continue
        aroma = core.norm(row[COL_AROMA])
        if aroma == "":
            continue
        name = core.norm(row[COL_NAME])
        vol = core.to_num(row[COL_VOLUME])
        signed = -vol if core.norm(row[COL_DIRECTION]).lower() == DIR_MINUS else vol

        u = agg.setdefault(phone, {"name": name, "aromas": {}})
        if name:
            u["name"] = name  # последнее непустое имя — снимок побеждает старый
        u["aromas"][aroma] = u["aromas"].get(aroma, 0) + signed

    for u in agg.values():
        u["aromas"] = {a: int(v) for a, v in u["aromas"].items() if v > 0}
    return agg


def _net_pair(values, phone, aroma):
    """Чистый остаток пары (телефон, аромат) по сырым values."""
    ta = core.norm(aroma).lower()
    total = 0
    for r in range(1, len(values)):
        row = _pad(values[r])
        if users.normalize_phone(row[COL_PHONE]) != phone:
            continue
        if core.norm(row[COL_AROMA]).lower() != ta:
            continue
        vol = core.to_num(row[COL_VOLUME])
        total += -vol if core.norm(row[COL_DIRECTION]).lower() == DIR_MINUS else vol
    return total


def _phone_seen(values, phone):
    """Есть ли уже строки этого телефона (для тега основной/добор)."""
    for r in range(1, len(values)):
        row = _pad(values[r])
        if users.normalize_phone(row[COL_PHONE]) == phone:
            return True
    return False


# ======================================================================
#  Операции
# ======================================================================

def add_order(phone_raw, name, aroma, volume, direction=DIR_PLUS):
    """
    Внести заказ строкой в Поток.
    Возвращает {"ok": True, "total": <новый остаток пары>, ...} либо
    {"ok": False, "reason": "..."}.
    """
    phone = users.normalize_phone(phone_raw)
    if not users.valid_phone(phone):
        return {"ok": False, "reason": "Телефон в формате 7XXXXXXXXXX"}
    aroma = (aroma or "").strip()
    if not aroma:
        return {"ok": False, "reason": "Не указан аромат"}
    vol = int(core.to_num(volume))
    if vol <= 0:
        return {"ok": False, "reason": "Объём должен быть больше 0"}
    direction = DIR_MINUS if core.norm(direction).lower() == DIR_MINUS else DIR_PLUS

    ws = _ws()
    values = ws.get_all_values()
    tag = TAG_EXTRA if _phone_seen(values, phone) else TAG_MAIN

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ws.append_row(
        [stamp, phone, (name or "").strip(), aroma, vol, direction, tag],
        value_input_option="RAW",
    )

    # новый остаток = прежний (по values до записи) +/- этот объём
    net = _net_pair(values, phone, aroma)
    net = net - vol if direction == DIR_MINUS else net + vol

    return {
        "ok": True,
        "phone": phone,
        "aroma": aroma,
        "volume": vol,
        "direction": direction,
        "tag": tag,
        "total": int(net),
    }


# ======================================================================
#  Чтение (для витрины, "Моё" и счёта)
# ======================================================================

def net_orders():
    """Полная свёртка Потока: phone -> {"name", "aromas": {аромат: мл}}. Для счёта."""
    return _aggregate(_ws().get_all_values())


def collected_map():
    """Набрано по каждому аромату (сумма по всем покупателям): {аромат: мл}."""
    out = {}
    for u in _aggregate(_ws().get_all_values()).values():
        for a, v in u["aromas"].items():
            out[a] = out.get(a, 0) + v
    return out


def board(phone_raw=None):
    """
    За ОДНО чтение Потока вернуть (collected, mine):
      collected — {аромат: набрано всеми, мл};
      mine      — {аромат: мой остаток, мл} для phone_raw (или {}).
    Используется витриной, чтобы не читать Поток дважды.
    """
    agg = _aggregate(_ws().get_all_values())
    collected = {}
    for u in agg.values():
        for a, v in u["aromas"].items():
            collected[a] = collected.get(a, 0) + v
    mine = {}
    if phone_raw:
        u = agg.get(users.normalize_phone(phone_raw))
        if u:
            mine = dict(u["aromas"])
    return collected, mine


def buyer_positions(phone_raw):
    """Заказ конкретного покупателя: {аромат: мл} (только положительные остатки)."""
    phone = users.normalize_phone(phone_raw)
    u = _aggregate(_ws().get_all_values()).get(phone)
    return dict(u["aromas"]) if u else {}


def pair_volume(phone_raw, aroma):
    """Текущий остаток пары (телефон, аромат) в мл."""
    phone = users.normalize_phone(phone_raw)
    return int(_net_pair(_ws().get_all_values(), phone, aroma))


def pair_and_collected(phone_raw, aroma):
    """За одно чтение: остаток пары (телефон, аромат) и всего набрано по аромату.
    Для формы организатора ('покажу, сколько уже есть')."""
    phone = users.normalize_phone(phone_raw)
    values = _ws().get_all_values()
    ta = core.norm(aroma).lower()
    pair = 0
    total = 0
    for r in range(1, len(values)):
        row = _pad(values[r])
        if core.norm(row[COL_AROMA]).lower() != ta:
            continue
        vol = core.to_num(row[COL_VOLUME])
        signed = -vol if core.norm(row[COL_DIRECTION]).lower() == DIR_MINUS else vol
        total += signed
        if users.normalize_phone(row[COL_PHONE]) == phone:
            pair += signed
    return {"buyer_qty": int(pair), "collected": int(total)}


def apply_desired(phone_raw, name, desired: dict):
    """
    Привести заказ покупателя к ЖЕЛАЕМЫМ остаткам одним заходом (для витрины).

    desired: {аромат: желаемый_остаток_мл}. Для каждого аромата считаем дельту
    (желаемое минус текущее) и, если она не ноль, пишем строку плюс/минус.
    Ароматов, которых нет в desired, не касаемся. desired=0 -> убрать позицию.

    Тег всей пачки: 'основной', если покупателя ещё не было в Потоке; иначе 'добор'
    (одна отправка формы = одно «событие заказа»).
    Возвращает {"ok": True, "changes": [{"aroma","from","to"}...]} либо reason.
    """
    phone = users.normalize_phone(phone_raw)
    if not users.valid_phone(phone):
        return {"ok": False, "reason": "Телефон в формате 7XXXXXXXXXX"}

    ws = _ws()
    values = ws.get_all_values()
    tag = TAG_EXTRA if _phone_seen(values, phone) else TAG_MAIN
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    rows_to_add = []
    changes = []
    for aroma, want in (desired or {}).items():
        aroma = core.norm(aroma)
        if aroma == "":
            continue
        want = int(core.to_num(want))
        if want < 0:
            want = 0
        cur = int(_net_pair(values, phone, aroma))
        delta = want - cur
        if delta == 0:
            continue
        direction = DIR_PLUS if delta > 0 else DIR_MINUS
        rows_to_add.append([stamp, phone, (name or "").strip(), aroma, abs(delta), direction, tag])
        changes.append({"aroma": aroma, "from": cur, "to": want})

    if rows_to_add:
        ws.append_rows(rows_to_add, value_input_option="RAW")
    return {"ok": True, "changes": changes}
