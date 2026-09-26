# archive.py — aroma_web / архив закупок.
#
# Жизнь закупки:
#   1) открыта            — заказывают;
#   2) закрыта            — видна как была, заказать по закупке нельзя (orders_state);
#   3) в архиве           — организатор нажала «Перевести в архив»: делаем СНИМОК книги
#                           (ассортимент с «набрано», кто что взял и на какую сумму, счета)
#                           и кладём в базу вместе со ссылкой на книгу. Витрина показывает
#                           «закупка завершена, следующая скоро», пока организатор не вставит
#                           ссылку на книгу новой закупки.
# Снимок хранится бессрочно: вкладка «Архив» открывает его как обычную витрину (только
# смотреть), у каждой девочки там своё «Моё». Гугл-книгу организатор тоже хранит — ссылка
# на неё лежит рядом (видит только организатор). Снимок можно переснять из книги.

import json
from contextlib import closing
from datetime import datetime

import admin
import buyers_db
import core
import sheets

_KEY_ARCHIVED_URL = "archived_source_url"    # книга, которую перевели в архив последней
_ROW_KEYS = ("aroma_name", "category", "status", "view", "prices", "price", "goal", "unit",
             "is_new", "note")


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _get_state(key):
    with closing(buyers_db.connect()) as con:
        r = con.execute("SELECT value FROM site_state WHERE key = ?", (key,)).fetchone()
    return r[0] if r else ""


def _set_state(key, value):
    with closing(buyers_db.connect()) as con, con:
        con.execute("INSERT INTO site_state (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def _book_id(url):
    try:
        return sheets.spreadsheet_id(url)
    except Exception:
        return ""


def current_is_archived():
    """Текущая книга закупки уже в архиве (ждём ссылку на новую)? Тогда по закупке
    не заказывают и витрина её не показывает."""
    arch = _get_state(_KEY_ARCHIVED_URL)
    return bool(arch) and _book_id(arch) == _book_id(core.current_source_url())


def book_title(url=None):
    try:
        return sheets.open_book(url or core.current_source_url()).title
    except Exception:
        return ""


# ======================================================================
#  Снимок
# ======================================================================

def build_snapshot(url):
    """Снимок книги: витрина как была + заказы каждой девочки с суммами (тем же расчётом,
    что счета) + счета из базы (доставка, к оплате, оплачено)."""
    rows, _ = core.prepare_dataframe(core.load_data(url))
    inv = admin.build_invoices(url)
    collected = {}
    for d in inv["details"]:
        for p in d["positions"]:
            collected[p["aroma"]] = collected.get(p["aroma"], 0) + p["volume"]
        for p in d["problems"]:
            collected[p["aroma"]] = collected.get(p["aroma"], 0) + p["volume"]
    positions = []
    for r in rows:
        if r["status"] in ("hide", "сервис"):
            continue
        x = {k: r.get(k) for k in _ROW_KEYS}
        x["collected"] = collected.get(r["aroma_name"], 0)
        x["remaining"] = max((x.get("goal") or 0) - x["collected"], 0)
        x["is_dobor"] = "добор" in (r["status"] or "")
        positions.append(x)
    cat = {r["aroma_name"].lower(): r for r in rows}
    bills = {b["phone"]: b for b in buyers_db.all_buyers()} if url_is_current(url) else {}
    buyers = {}
    for d in inv["details"]:
        items = []
        for p in d["positions"]:
            meta = cat.get(p["aroma"].lower(), {})
            items.append({"aroma": p["aroma"], "volume": p["volume"], "amount": p["amount"],
                          "category": meta.get("category", ""), "unit": meta.get("unit", "мл")})
        for p in d["problems"]:
            items.append({"aroma": p["aroma"], "volume": p["volume"], "amount": 0,
                          "category": "", "unit": "мл", "problem": p["reason"]})
        b = bills.get(d["phone"]) or {}
        buyers[d["phone"]] = {
            "name": d["buyer"], "items": items, "total": d["total"],
            "bill": {"pay_amount": b.get("pay_amount", ""), "pay_delivery": b.get("pay_delivery", ""),
                     "paid": bool(b.get("paid"))} if b.get("pay_amount") or b.get("paid") else {},
        }
    return {"positions": positions, "buyers": buyers, "grand_total": inv["grand_total"],
            "title": book_title(url), "taken_at": _now()}


def url_is_current(url):
    return _book_id(url) == _book_id(core.current_source_url())


# ======================================================================
#  Записи архива
# ======================================================================

def _row(r, with_snapshot=False):
    d = {"id": r["id"], "name": r["name"], "url": r["url"], "archived_at": r["archived_at"]}
    snap = json.loads(r["snapshot"] or "{}")
    d["n_buyers"] = len(snap.get("buyers", {}))
    d["grand_total"] = snap.get("grand_total", 0)
    if with_snapshot:
        d["snapshot"] = snap
    return d


def entries():
    """Все закупки архива, новые сверху (без снимков — для списка)."""
    with closing(buyers_db.connect()) as con:
        return [_row(r) for r in con.execute("SELECT * FROM archive ORDER BY archived_at DESC, id DESC")]


def get(entry_id):
    with closing(buyers_db.connect()) as con:
        r = con.execute("SELECT * FROM archive WHERE id = ?", (entry_id,)).fetchone()
    return _row(r, with_snapshot=True) if r else None


def add(name, url):
    """Снять книгу и положить в архив. → id записи. Ошибка чтения книги — исключение."""
    snap = build_snapshot(url)
    name = (name or "").strip() or snap["title"] or "Закупка"
    with closing(buyers_db.connect()) as con, con:
        cur = con.execute("INSERT INTO archive (name, url, archived_at, snapshot) VALUES (?, ?, ?, ?)",
                          (name, url, _now(), json.dumps(snap, ensure_ascii=False)))
        return cur.lastrowid


def archive_current(name):
    """«Перевести в архив» текущую закупку: снимок + витрина переходит в «следующая скоро»."""
    url = core.current_source_url()
    entry_id = add(name, url)
    _set_state(_KEY_ARCHIVED_URL, url)
    return entry_id


def refresh(entry_id):
    """Переснять снимок из книги (если в книге что-то поправили уже после архива)."""
    e = get(entry_id)
    if not e:
        return False
    snap = build_snapshot(e["url"])
    old_bills = {p: b.get("bill") for p, b in e["snapshot"].get("buyers", {}).items() if b.get("bill")}
    for p, bill in old_bills.items():         # счета того времени берём из старого снимка
        if p in snap["buyers"] and not snap["buyers"][p]["bill"]:
            snap["buyers"][p]["bill"] = bill
    with closing(buyers_db.connect()) as con, con:
        con.execute("UPDATE archive SET snapshot = ? WHERE id = ?",
                    (json.dumps(snap, ensure_ascii=False), entry_id))
    return True


def rename(entry_id, name):
    name = (name or "").strip()
    if not name:
        return False
    with closing(buyers_db.connect()) as con, con:
        return con.execute("UPDATE archive SET name = ? WHERE id = ?", (name, entry_id)).rowcount > 0


def delete(entry_id):
    with closing(buyers_db.connect()) as con, con:
        return con.execute("DELETE FROM archive WHERE id = ?", (entry_id,)).rowcount > 0


# ======================================================================
#  «Моё» (и для текущей закупки, и для архивной) — одна форма для экрана и выгрузки
# ======================================================================

def mine_of(entry, phone):
    """«Моё» девочки в архивной закупке: {name, items, total, bill} или None."""
    return entry["snapshot"].get("buyers", {}).get(phone)


def mine_current(phone):
    """«Моё» в текущей закупке тем же расчётом, что счёт: {name, items, total, bill}."""
    inv = admin.build_invoices()
    d = next((x for x in inv["details"] if x["phone"] == phone), None)
    if not d:
        return None
    rows, _ = core.prepare_dataframe(core.load_data())
    cat = {r["aroma_name"].lower(): r for r in rows}
    items = []
    for p in d["positions"]:
        meta = cat.get(p["aroma"].lower(), {})
        items.append({"aroma": p["aroma"], "volume": p["volume"], "amount": p["amount"],
                      "category": meta.get("category", ""), "unit": meta.get("unit", "мл")})
    b = buyers_db.get(phone) or {}
    return {"name": d["buyer"], "items": items, "total": d["total"],
            "bill": {"pay_amount": b.get("pay_amount", ""), "pay_delivery": b.get("pay_delivery", ""),
                     "paid": bool(b.get("paid"))} if b.get("pay_amount") or b.get("paid") else {}}
