# buyers_sheet_io.py — aroma_web / покупатели: перенос из листа, ночная копия в лист, бэкап базы.
#
#   python buyers_sheet_io.py import [--force]  — разовый перенос листа «Покупатели» в buyers.db
#   python buyers_sheet_io.py export            — копия базы в лист (ТОЛЬКО для просмотра)
#   python buyers_sheet_io.py backup            — копия файла базы в backups/ (храним 14 дней)
#   python buyers_sheet_io.py nightly           — backup + export (запускает cron раз в сутки)
#
# После переноса лист больше не источник: всё, что в нём поправят руками, ночная копия
# перезапишет. Хеши кодов в копию не выгружаем — только отметку «есть код».

import glob
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime

import buyers_db
import sheets
import users

USERS_URL = "https://docs.google.com/spreadsheets/d/15PjPHqSl6Iju41VIZOGkCMwy4kyBomr_X9F60hYwo0U/edit"
USERS_SHEET_NAME = "Пользователи"

# Раскладка листа (0-индекс) — как было, пока лист был источником.
COLS = [
    ("phone", "телефон"), ("name", "имя"), ("code_hash", "код-хеш"), ("address", "адрес"),
    ("role", "роль"), ("created", "создан"), ("note", "заметка"),
    ("last_name", "Фамилия"), ("first_name", "Имя"), ("patronymic", "Отчество"),
    ("city", "Город"), ("pvz_address", "ПВЗ адрес"), ("pvz_id", "ПВЗ id"),
    ("tracking_url", "отслеживание"), ("carrier", "перевозчик"), ("email", "email"),
    ("pay_link", "ссылка на оплату"), ("pay_amount", "сумма к оплате"),
    ("pay_delivery", "доставка"), ("paid", "оплата"), ("pay_to", "реквизиты"),
]
PAID_MARK = "оплачено"
BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(buyers_db.DB_PATH)), "backups")
KEEP_BACKUPS = 14


def _ws():
    return sheets.open_book(USERS_URL).worksheet(USERS_SHEET_NAME)


def read_sheet():
    """Строки листа -> (записи {поле: значение}, пропущенные [(номер строки, причина)])."""
    values = _ws().get_all_values()
    recs, skipped, seen = [], [], set()
    for r in range(1, len(values)):
        row = values[r]
        cell = lambda i: (row[i] if i < len(row) else "") or ""
        phone = users.normalize_phone(cell(0))
        if not users.valid_phone(phone):
            if any(c.strip() for c in row):
                skipped.append((r + 1, f"телефон «{cell(0)}» не распознан"))
            continue
        if phone in seen:
            skipped.append((r + 1, f"повтор телефона {phone}"))
            continue
        seen.add(phone)
        rec = {"phone": phone}
        for i, (field, _) in enumerate(COLS[1:], start=1):
            v = cell(i)
            rec[field] = v if field == "code_hash" else v.strip()
        recs.append(rec)
    return recs, skipped


def do_import(force=False):
    n = buyers_db.count()
    if n and not force:
        sys.exit(f"В базе уже {n} покупателей — перенос не делаю (--force сотрёт и перенесёт заново).")
    recs, skipped = read_sheet()
    with closing(buyers_db.connect()) as con, con:
        if force:
            con.execute("DELETE FROM bills")
            con.execute("DELETE FROM buyers")
    bills = {}
    for rec in recs:
        buyers_db.insert(rec["phone"], **{k: rec[k] for k in buyers_db.PROFILE})
        bill = {k: rec[k] for k in buyers_db.BILL if rec[k]}
        if bill:
            bills[rec["phone"]] = bill
    buyers_db.set_bills(bills)
    print(f"Перенесено покупателей: {len(recs)}, со счётом: {len(bills)}")
    for row, why in skipped:
        print(f"  пропущена строка {row}: {why}")
    verify(recs)


def verify(recs=None):
    """Сверка листа и базы поле в поле (кроме строк, которые пропустили при переносе)."""
    recs = recs if recs is not None else read_sheet()[0]
    diffs = 0
    for rec in recs:
        got = buyers_db.get(rec["phone"])
        if got is None:
            print(f"  нет в базе: {rec['phone']}")
            diffs += 1
            continue
        for field, _ in COLS[1:]:
            want = rec[field]
            have = got[field]
            if field == "paid":
                want, have = want.lower().startswith("оплач"), bool(have)
            if (want or "") != (have or ""):
                print(f"  {rec['phone']} {field}: лист «{want}» ≠ база «{have}»")
                diffs += 1
    print("Сверка: всё совпадает" if not diffs else f"Сверка: расхождений {diffs}")
    return diffs


def do_export():
    rows = [[title for _, title in COLS]]
    rows[0][2] = "код"
    for rec in buyers_db.all_buyers():
        out = []
        for field, _ in COLS:
            v = rec.get(field)
            if field == "code_hash":
                v = "есть" if v else ""
            elif field == "paid":
                v = PAID_MARK if v else ""
            out.append(v or "")
        rows.append(out)
    ws = _ws()
    if ws.col_count < len(COLS):
        ws.add_cols(len(COLS) - ws.col_count)
    if ws.row_count < len(rows) + 5:
        ws.add_rows(len(rows) + 5 - ws.row_count)
    ws.batch_clear([f"A1:{sheets.col_a1(ws.col_count - 1)}{ws.row_count}"])
    ws.update(range_name="A1", values=rows, value_input_option="RAW")
    print(f"Копия в лист: {len(rows) - 1} покупателей")


def do_backup():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dst = os.path.join(BACKUP_DIR, f"buyers-{datetime.now():%Y-%m-%d}.db")
    with closing(buyers_db.connect()) as src, closing(sqlite3.connect(dst)) as out:
        src.backup(out)
    for old in sorted(glob.glob(os.path.join(BACKUP_DIR, "buyers-*.db")))[:-KEEP_BACKUPS]:
        os.remove(old)
    print(f"Бэкап: {dst}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "import":
        do_import(force="--force" in sys.argv)
    elif cmd == "verify":
        sys.exit(1 if verify() else 0)
    elif cmd == "export":
        do_export()
    elif cmd == "backup":
        do_backup()
    elif cmd == "nightly":
        do_backup()
        do_export()
    else:
        sys.exit(__doc__ or "import | verify | export | backup | nightly")
