# buyers_db.py — aroma_web / база покупателей (вместо листа «Покупатели» в гугл-таблице).
#
# Две таблицы, как решили с Еленой:
#   buyers — ГЛОБАЛЬНОЕ, не меняется от закупки к закупке: телефон (ключ), имя, код входа,
#            роль, ФИО, город, ПВЗ, перевозчик, e-mail, заметка;
#   bills  — ЗАКУПОЧНОЕ, текущий счёт девочки: ссылка на оплату, сумма, доставка, «оплачено»,
#            реквизиты перевода, ссылка отслеживания. Хозяин — дашборд: он присылает счёт
#            через закрытый канал (sync_api), витрина только показывает.
#
# Файл базы — рядом с кодом (buyers.db) или путь из BUYERS_DB. Лист «Покупатели» больше не
# источник: раз в сутки туда выгружается копия только для просмотра (buyers_sheet_io.py).

import os
import sqlite3
from contextlib import closing
from datetime import datetime

DB_PATH = os.environ.get("BUYERS_DB") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "buyers.db")

PROFILE = ("name", "code_hash", "address", "role", "created", "note",
           "last_name", "first_name", "patronymic", "city", "pvz_address", "pvz_id",
           "carrier", "email")
BILL = ("pay_link", "pay_amount", "pay_delivery", "paid", "pay_to", "tracking_url")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS buyers (
    phone TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    code_hash TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    created TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '',
    first_name TEXT NOT NULL DEFAULT '',
    patronymic TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    pvz_address TEXT NOT NULL DEFAULT '',
    pvz_id TEXT NOT NULL DEFAULT '',
    carrier TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    updated TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS bills (
    phone TEXT PRIMARY KEY REFERENCES buyers(phone) ON DELETE CASCADE,
    pay_link TEXT NOT NULL DEFAULT '',
    pay_amount TEXT NOT NULL DEFAULT '',
    pay_delivery TEXT NOT NULL DEFAULT '',
    paid INTEGER NOT NULL DEFAULT 0,
    pay_to TEXT NOT NULL DEFAULT '',
    tracking_url TEXT NOT NULL DEFAULT '',
    updated TEXT NOT NULL DEFAULT ''
);
"""

_ready = set()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect():
    """Соединение с базой (схема создаётся при первом обращении к файлу)."""
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=15000")
    con.execute("PRAGMA foreign_keys=ON")
    if DB_PATH not in _ready:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(_SCHEMA)
        con.commit()
        _ready.add(DB_PATH)
    return con


_SELECT = ("SELECT b.*, COALESCE(k.pay_link,'') AS pay_link, COALESCE(k.pay_amount,'') AS pay_amount, "
           "COALESCE(k.pay_delivery,'') AS pay_delivery, COALESCE(k.paid,0) AS paid, "
           "COALESCE(k.pay_to,'') AS pay_to, COALESCE(k.tracking_url,'') AS tracking_url "
           "FROM buyers b LEFT JOIN bills k ON k.phone = b.phone")


def get(phone):
    """Покупатель + его текущий счёт одним словарём, или None."""
    with closing(connect()) as con:
        r = con.execute(_SELECT + " WHERE b.phone = ?", (phone,)).fetchone()
    return dict(r) if r else None


def all_buyers():
    with closing(connect()) as con:
        return [dict(r) for r in con.execute(_SELECT + " ORDER BY b.rowid")]


def count():
    with closing(connect()) as con:
        return con.execute("SELECT COUNT(*) FROM buyers").fetchone()[0]


def insert(phone, **fields):
    """Новый покупатель. False, если телефон уже есть."""
    vals = {k: str(fields.get(k) or "").strip() for k in PROFILE if k in fields}
    if "code_hash" in fields:
        vals["code_hash"] = fields["code_hash"] or ""      # хеш не трогаем .strip()
    vals.setdefault("created", _now())
    cols = ["phone"] + list(vals) + ["updated"]
    try:
        with closing(connect()) as con, con:
            con.execute(f"INSERT INTO buyers ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [phone] + list(vals.values()) + [_now()])
    except sqlite3.IntegrityError:
        return False
    return True


def update(phone, **fields):
    """Правка глобальных полей. False, если телефона нет."""
    vals = {k: ("" if v is None else (v if k == "code_hash" else str(v).strip()))
            for k, v in fields.items() if k in PROFILE}
    if not vals:
        return get(phone) is not None
    sets = ", ".join(f"{k} = ?" for k in vals) + ", updated = ?"
    with closing(connect()) as con, con:
        n = con.execute(f"UPDATE buyers SET {sets} WHERE phone = ?",
                        list(vals.values()) + [_now(), phone]).rowcount
    return n > 0


def delete(phone):
    with closing(connect()) as con, con:
        con.execute("DELETE FROM bills WHERE phone = ?", (phone,))
        return con.execute("DELETE FROM buyers WHERE phone = ?", (phone,)).rowcount > 0


def _bill_vals(fields):
    out = {}
    for k, v in (fields or {}).items():
        if k not in BILL:
            continue
        if k == "paid":
            out[k] = 1 if (v is True or str(v or "").strip().lower().startswith(("оплач", "1", "true"))) else 0
        else:
            out[k] = "" if v is None else str(v).strip()
    return out


def set_bills(updates, clear_others=False):
    """Счета разом: {phone: {поле: значение}} — поле, которого нет, остаётся как было.
    clear_others — у всех остальных счёт стирается (новая закупка). Одна транзакция.
    Возвращает {"updated": n, "not_found": [телефоны, которых нет в базе]}."""
    now = _now()
    with closing(connect()) as con, con:
        known = {r[0] for r in con.execute("SELECT phone FROM buyers")}
        done, missing = 0, []
        for phone, fields in (updates or {}).items():
            if phone not in known:
                missing.append(phone)
                continue
            vals = _bill_vals(fields)
            con.execute("INSERT OR IGNORE INTO bills (phone, updated) VALUES (?, ?)", (phone, now))
            if vals:
                sets = ", ".join(f"{k} = ?" for k in vals)
                con.execute(f"UPDATE bills SET {sets}, updated = ? WHERE phone = ?",
                            list(vals.values()) + [now, phone])
            done += 1
        if clear_others:
            keep = [p for p in (updates or {}) if p in known]
            q = "DELETE FROM bills"
            if keep:
                q += f" WHERE phone NOT IN ({','.join('?' * len(keep))})"
            con.execute(q, keep)
    return {"updated": done, "not_found": missing}
