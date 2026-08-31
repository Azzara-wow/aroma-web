# nalichie.py — aroma_web / модуль "Наличие" (склад, доступен всегда).
#
# Отдельная книга (статичный файл склада), которую читает ещё и дашборд, поэтому
# читаем/пишем ПО ИМЕНАМ ЗАГОЛОВКОВ (с синонимами), а не по позициям — двигаешь
# столбцы, ничего не ломается.
#
# Модель остатка:
#   Остаток наличия (доступно) = остаток (закуплено, накопительно) − Набрано (расход).
#   Набрано — сумма "заказа" по всему потоку (статус не влияет), значит доступное
#   уменьшается сразу при заказе. Формулы Набрано/Остаток наличия живут в самой
#   таблице (для тебя и дашборда), а программа считает Набрано сама из потока.
#
# Статус "выполнено" влияет ТОЛЬКО на "Моё" покупателя (показываем невыполненное).
#
# Цена: "Цена за 1мл" — истина для суммы (Сумма = кол-во × цена за 1мл);
#       "цена 10/50мл" — витринная, как привыкли покупатели.

from datetime import datetime

import core
import sheets
import users

# Ссылка на книгу склада (id из неё же используется для кэша подключения).
NALICHIE_URL = "https://docs.google.com/spreadsheets/d/10-tDVQSOsMi099qbX21RdwNC5IoD35IfcX7GMpgTNKY/edit"

# Листы адресуем по gid (стабильно, регистр названия не важен).
ASSORT_GID = 2027082947   # ассортимент наличия ("Лист1")
FLOW_GID = 590096728      # поток заказов ("заказы")
STATUS_DONE = "выполнено"

# --- синонимы заголовков ---
A_NAME = ["наличие", "наименование", "товар", "название"]
A_STOCK = ["остаток"]                    # ровно "остаток" (не "остаток наличия")
A_PERML = ["цена за 1мл", "за 1мл", "цена 1мл", "цена за 1 мл"]
A_SHOW = ["цена 10/50мл", "за 10/50мл", "цена 10/50", "за 10", "за 50"]

F_DATE = ["дата"]
F_PHONE = ["телефон", "тел"]
F_STATUS = ["статус"]
F_INAME = ["имя"]
F_ITEM = ["наименование", "товар", "наличие", "название"]
F_QTY = ["заказ", "кол-во", "количество", "колво", "объём", "объем"]
F_PRICE1 = ["цена за 1мл", "за 1мл"]
F_SHOW = ["цена 10/50мл", "за 10/50мл", "цена 10/50"]
F_SUM = ["сумма"]
F_DIR = ["направление", "напр"]


# ---------- утилиты заголовков ----------

def _resolve(header, aliases):
    """Индекс столбца по первому совпавшему синониму (точное совпадение). None если нет."""
    low = [core.norm(h).lower() for h in header]
    for a in aliases:
        if a in low:
            return low.index(a)
    return None


def _cell(row, idx):
    return row[idx] if (idx is not None and idx < len(row)) else ""


# ---------- чтение листов ----------

def _assort_rows():
    ws = sheets.worksheet_by_gid(ASSORT_GID, NALICHIE_URL)
    values = ws.get_all_values()
    header = values[0] if values else []
    ci = {"name": _resolve(header, A_NAME), "stock": _resolve(header, A_STOCK),
          "perml": _resolve(header, A_PERML), "show": _resolve(header, A_SHOW)}
    return values, header, ci


def _flow_rows():
    ws = sheets.worksheet_by_gid(FLOW_GID, NALICHIE_URL)
    values = ws.get_all_values()
    header = values[0] if values else []
    ci = {"date": _resolve(header, F_DATE), "phone": _resolve(header, F_PHONE),
          "status": _resolve(header, F_STATUS), "iname": _resolve(header, F_INAME),
          "item": _resolve(header, F_ITEM), "qty": _resolve(header, F_QTY),
          "perml": _resolve(header, F_PRICE1), "show": _resolve(header, F_SHOW),
          "sum": _resolve(header, F_SUM), "dir": _resolve(header, F_DIR)}
    return values, header, ci


def _consumed_map(fvalues, fci):
    """Набрано по каждому товару = сумма 'заказа' по всему потоку (с направлением)."""
    out = {}
    for r in range(1, len(fvalues)):
        row = fvalues[r]
        item = core.norm(_cell(row, fci["item"]))
        if item == "":
            continue
        qty = core.to_num(_cell(row, fci["qty"]))
        signed = -qty if core.norm(_cell(row, fci["dir"])).lower() == "минус" else qty
        out[item.lower()] = out.get(item.lower(), 0) + signed
    return out


# ---------- ассортимент с остатком ----------

def assortment():
    """
    Список товаров склада:
      {name, per_ml, show, stock, consumed, available}
    available = stock − consumed (доступно к заказу).
    Секции/пустые строки пропускаем.
    """
    avalues, aheader, aci = _assort_rows()
    fvalues, fheader, fci = _flow_rows()
    consumed = _consumed_map(fvalues, fci)

    items = []
    for r in range(1, len(avalues)):
        row = avalues[r]
        name = core.norm(_cell(row, aci["name"]))
        if name == "":
            continue
        stock_raw = core.norm(_cell(row, aci["stock"]))
        # строка-секция (напр. "Отдушки") — без остатка и цены
        if name.lower() in core.SECTION_WORDS and stock_raw == "":
            continue
        stock = core.to_num(stock_raw)
        per_ml = core.to_num(_cell(row, aci["perml"]))
        show = core.to_num(_cell(row, aci["show"]))
        cons = consumed.get(name.lower(), 0)
        items.append({
            "name": name, "per_ml": per_ml, "show": show,
            "stock": int(stock) if stock == int(stock) else stock,
            "consumed": int(cons) if cons == int(cons) else cons,
            "available": stock - cons,
        })
    return items


def view(phone_raw=None):
    """
    Для витрины за один проход: (items, mine_sum).
    items: [{name, per_ml, show, available, mine}] — mine = невыполненный заказ
    покупателя (0 если не задан телефон). mine_sum — сумма склада покупателя (₽).
    """
    avalues, ah, aci = _assort_rows()
    fvalues, fh, fci = _flow_rows()
    consumed = _consumed_map(fvalues, fci)

    phone = users.normalize_phone(phone_raw) if phone_raw else None
    mine = {}
    if phone and users.valid_phone(phone):
        for r in range(1, len(fvalues)):
            row = fvalues[r]
            if users.normalize_phone(_cell(row, fci["phone"])) != phone:
                continue
            if core.norm(_cell(row, fci["status"])).lower() == STATUS_DONE:
                continue
            item = core.norm(_cell(row, fci["item"]))
            if item == "":
                continue
            qty = core.to_num(_cell(row, fci["qty"]))
            signed = -qty if core.norm(_cell(row, fci["dir"])).lower() == "минус" else qty
            mine[item.lower()] = mine.get(item.lower(), 0) + signed

    def _i(x):
        return int(x) if x == int(x) else x

    items = []
    mine_sum = 0
    for r in range(1, len(avalues)):
        row = avalues[r]
        name = core.norm(_cell(row, aci["name"]))
        if name == "":
            continue
        stock_raw = core.norm(_cell(row, aci["stock"]))
        if name.lower() in core.SECTION_WORDS and stock_raw == "":
            continue
        stock = core.to_num(stock_raw)
        per_ml = core.to_num(_cell(row, aci["perml"]))
        show = core.to_num(_cell(row, aci["show"]))
        avail = stock - consumed.get(name.lower(), 0)
        mq = mine.get(name.lower(), 0)
        # прячем распроданное (остаток<=0), кроме позиций с активным заказом покупателя
        if avail <= 0 and mq <= 0:
            continue
        if mq > 0:
            mine_sum += round(mq * per_ml)
        items.append({"name": name, "per_ml": per_ml, "show": show,
                      "available": _i(avail), "mine": _i(mq) if mq > 0 else 0})
    return items, int(mine_sum)


# ---------- "Моё" покупателя (только невыполненные) ----------

def board(phone_raw):
    """
    Заказ покупателя по складу (невыполненные строки):
      {name: {"qty", "sum"}}, плюс общий total_sum.
    Сумма = кол-во × цена за 1мл (цена берётся из ассортимента).
    """
    phone = users.normalize_phone(phone_raw)
    perml = {i["name"].lower(): i["per_ml"] for i in assortment()}
    fvalues, fheader, fci = _flow_rows()

    mine = {}
    for r in range(1, len(fvalues)):
        row = fvalues[r]
        if users.normalize_phone(_cell(row, fci["phone"])) != phone:
            continue
        if core.norm(_cell(row, fci["status"])).lower() == STATUS_DONE:
            continue  # выполненные — не в активном заказе
        item = core.norm(_cell(row, fci["item"]))
        if item == "":
            continue
        qty = core.to_num(_cell(row, fci["qty"]))
        signed = -qty if core.norm(_cell(row, fci["dir"])).lower() == "минус" else qty
        d = mine.setdefault(item, {"qty": 0})
        d["qty"] += signed

    out = {}
    total = 0
    for item, d in mine.items():
        q = d["qty"]
        if q <= 0:
            continue
        s = round(q * perml.get(item.lower(), 0))
        out[item] = {"qty": int(q) if q == int(q) else q, "sum": s}
        total += s
    return {"items": out, "total_sum": int(total)}


# ---------- запись заказа (с проверкой остатка) ----------

def add_batch(phone_raw, name, additions: dict, status=""):
    """
    Добавить в поток заказы покупателя по складу (только прибавление).
    additions: {товар: сколько_добавить}. Проверяем остаток: нельзя больше available.
    status="" (обычный) или "выполнено" (организатор сразу закрывает).
    Возвращает {"ok", "changes":[{item,added,sum}], "rejected":[{item,reason}]}.
    """
    phone = users.normalize_phone(phone_raw)
    if not users.valid_phone(phone):
        return {"ok": False, "reason": "Телефон в формате 7XXXXXXXXXX"}

    items = {i["name"].lower(): i for i in assortment()}
    fvalues, fheader, fci = _flow_rows()
    if fci["item"] is None or fci["qty"] is None:
        return {"ok": False, "reason": "В листе 'Заказы' не найдены столбцы товара/количества"}

    stamp = datetime.now().strftime("%d.%m.%Y")
    rows, changes, rejected = [], [], []

    for item_name, add in (additions or {}).items():
        item_name = core.norm(item_name)
        add = int(core.to_num(add))
        if item_name == "" or add <= 0:
            continue
        info = items.get(item_name.lower())
        if info is None:
            rejected.append({"item": item_name, "reason": "нет в складе"})
            continue
        if add > info["available"]:
            rejected.append({"item": item_name,
                             "reason": f"на складе только {int(info['available'])}"})
            continue
        per_ml = info["per_ml"]
        summ = round(add * per_ml)
        mapping = {"date": stamp, "phone": phone, "status": status,
                   "iname": (name or "").strip(), "item": info["name"], "qty": add,
                   "perml": per_ml or "", "show": info["show"] or "", "sum": summ,
                   "dir": "плюс"}
        rows.append(_row_for(fheader, fci, mapping))
        changes.append({"item": info["name"], "added": add, "sum": summ})

    if rows:
        sheets.worksheet_by_gid(FLOW_GID, NALICHIE_URL).append_rows(
            rows, value_input_option="USER_ENTERED")
    return {"ok": True, "changes": changes, "rejected": rejected}


def _row_for(header, ci, mapping):
    """Собрать строку длиной как шапка, разложив значения по индексам столбцов."""
    row = [""] * len(header)
    for key, idx in ci.items():
        if idx is not None and idx < len(row) and mapping.get(key) not in (None,):
            row[idx] = mapping[key]
    return row


# ---------- организаторское ----------

def add_item(name, stock, per_ml, show=""):
    """Добавить/пополнить товар в ассортименте склада (одна строка в конец Лист1)."""
    name = core.norm(name)
    if name == "":
        return {"ok": False, "reason": "Укажите наименование"}
    avalues, aheader, aci = _assort_rows()
    if aci["name"] is None:
        return {"ok": False, "reason": "В листе ассортимента не найден столбец наименования"}
    mapping = {"name": name, "stock": int(core.to_num(stock)) or "",
               "perml": core.to_num(per_ml) or "", "show": core.to_num(show) or ""}
    row = _row_for(aheader, aci, mapping)
    sheets.worksheet_by_gid(ASSORT_GID, NALICHIE_URL).append_row(
        row, value_input_option="USER_ENTERED")
    return {"ok": True, "name": name}


def _append_flow(phone, name, item, qty, direction, status, per_ml, show):
    """Записать одну строку в поток наличия (кол-во положительное, знак — в 'Направление').
    Сумма минусовой строки отрицательная (для корректных итогов в дашборде)."""
    fvalues, fheader, fci = _flow_rows()
    stamp = datetime.now().strftime("%d.%m.%Y")
    summ = round(qty * per_ml)
    if direction == "минус":
        summ = -summ
    mapping = {"date": stamp, "phone": phone, "status": status,
               "iname": (name or "").strip(), "item": item, "qty": qty,
               "perml": per_ml or "", "show": show or "", "sum": summ, "dir": direction}
    sheets.worksheet_by_gid(FLOW_GID, NALICHIE_URL).append_row(
        _row_for(fheader, fci, mapping), value_input_option="USER_ENTERED")
    return summ


def add_order(phone_raw, name, item, qty, done=False, direction="плюс"):
    """Организатор вносит заказ покупателя по складу (подстраховка из чата).
    direction='плюс' (с проверкой остатка) или 'минус' (уменьшить, без проверки).
    done=True -> сразу статус 'выполнено'."""
    phone = users.normalize_phone(phone_raw)
    if not users.valid_phone(phone):
        return {"ok": False, "reason": "Телефон в формате 7XXXXXXXXXX"}
    item = core.norm(item)
    q = int(core.to_num(qty))
    if item == "" or q <= 0:
        return {"ok": False, "reason": "Укажите товар и количество больше 0"}
    status = STATUS_DONE if done else ""

    if core.norm(direction).lower() == "минус":
        info = next((i for i in assortment() if i["name"].lower() == item.lower()), None)
        per_ml = info["per_ml"] if info else 0
        show = info["show"] if info else ""
        name_item = info["name"] if info else item
        summ = _append_flow(phone, name, name_item, q, "минус", status, per_ml, show)
        return {"ok": True, "change": {"item": name_item, "added": -q, "sum": summ}}

    # плюс — через add_batch (с проверкой остатка)
    res = add_batch(phone_raw, name, {item: qty}, status=status)
    if not res.get("ok"):
        return res
    if res["rejected"]:
        return {"ok": False, "reason": res["rejected"][0]["reason"]}
    ch = res["changes"][0] if res["changes"] else None
    return {"ok": True, "change": ch}
