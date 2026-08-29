# admin.py — aroma_web / режим организатора (Шаг 2, подшаг 2: форма ввода)
#
# Что делает:
#   GET  /admin           — страница с формой ввода заказов
#   POST /admin/add       — внести заказ (плюс/минус): пишет в лист "Журнал",
#                           пересчитывает клетку матрицы этого покупателя/аромата.
#
# Пишет в таблицу через gspread + сервисный аккаунт (ключ в /etc/secrets/service_account.json
# на Render, либо service_account.json рядом — локально).
#
# ВАЖНО про листы:
#   - основной лист (матрица) — sheet1 (первый лист книги)
#   - лист "Журнал" — по имени JOURNAL_SHEET_NAME
#   Журнал заводится заново каждую закупку (пустой, с шапкой A..F).
#
# Контракт журнала (A..F): дата-время | покупатель | аромат | объём | направление | тег
#   направление: "минус" вычитает; всё прочее (плюс/пусто/мусор) — прибавляет.

import os
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import core

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# --- Настройки таблицы (пишем в ту же книгу, что читает морда) ---
# ID достаём из core.SHEET_URL, чтобы источник был один.
def _spreadsheet_id():
    from urllib.parse import urlparse
    parts = urlparse(core.SHEET_URL).path.split("/")
    return parts[parts.index("d") + 1]

JOURNAL_SHEET_NAME = "Журнал"

KEY_PATHS = [
    "/etc/secrets/service_account.json",   # Render
    "service_account.json",                # локально
]
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


# ---------- подключение к таблице через gspread ----------

def _find_key_path():
    for p in KEY_PATHS:
        if os.path.exists(p):
            return p
    raise FileNotFoundError("Ключ сервисного аккаунта не найден: " + " или ".join(KEY_PATHS))


def _client():
    creds = Credentials.from_service_account_file(_find_key_path(), scopes=SCOPES)
    return gspread.authorize(creds)


def _open_book():
    return _client().open_by_key(_spreadsheet_id())


def _get_main_ws(book):
    return book.sheet1


def _get_journal_ws(book):
    """Лист 'Журнал'. Если его нет — создаём с шапкой."""
    try:
        return book.worksheet(JOURNAL_SHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = book.add_worksheet(title=JOURNAL_SHEET_NAME, rows=1000, cols=6)
        ws.update("A1:F1", [["дата-время", "покупатель", "аромат", "объём", "направление", "тег"]])
        return ws


# ---------- утилиты работы с матрицей (через gspread values) ----------

def _norm(s):
    return core.norm(s).lower()


def read_matrix(main_ws):
    """
    Читаем весь основной лист как values (список строк).
    Возвращаем (values, header, buyer_start).
    """
    values = main_ws.get_all_values()
    header = values[0] if values else []
    # позиция якоря "Наличие"
    buyer_start = None
    for pos, name in enumerate(header):
        if core.norm(name).lower() == core.BUYERS_ANCHOR:
            buyer_start = pos
            break
    if buyer_start is None:
        buyer_start = 13
    return values, header, buyer_start


def find_buyer_col(header, buyer_start, buyer_name):
    """Позиция столбца покупателя по имени (0-индекс). None если нет."""
    target = _norm(buyer_name)
    for pos in range(buyer_start, len(header)):
        if _norm(header[pos]) == target:
            return pos
    return None


def find_last_buyer_col(header, buyer_start):
    """Позиция последнего заполненного столбца покупателя."""
    last = buyer_start - 1
    for pos in range(buyer_start, len(header)):
        if core.norm(header[pos]) != "":
            last = pos
    return last


def find_aroma_row(values, aroma_name):
    """Индекс строки (0-индекс в values) аромата по столбцу D. None если нет."""
    target = _norm(aroma_name)
    for r in range(1, len(values)):
        row = values[r]
        name = row[core.COL_NAME] if core.COL_NAME < len(row) else ""
        if _norm(name) == target:
            return r
    return None


def journal_sum(journal_ws, buyer_name, aroma_name):
    """Текущий объём пары (покупатель, аромат) = сумма плюс/минус из журнала."""
    rows = journal_ws.get_all_values()
    tb = _norm(buyer_name)
    ta = _norm(aroma_name)
    total = 0
    for r in range(1, len(rows)):
        row = rows[r] + [""] * (6 - len(rows[r]))  # добить до 6 колонок
        b = _norm(row[1])
        a = _norm(row[2])
        if b == tb and a == ta:
            vol = core.to_num(row[3])
            direction = core.norm(row[4]).lower()
            if direction == "минус":
                total -= vol
            else:  # плюс / пусто / мусор -> прибавить
                total += vol
    return int(total)


def buyer_exists_in_journal(journal_ws, buyer_name):
    """Есть ли уже строки этого покупателя в журнале (для тега основной/добор)."""
    rows = journal_ws.get_all_values()
    tb = _norm(buyer_name)
    for r in range(1, len(rows)):
        row = rows[r]
        b = _norm(row[1]) if len(row) > 1 else ""
        if b == tb:
            return True
    return False


def col_to_a1(col_idx_0):
    """0-индекс столбца -> буква(ы) A1. 0->A, 26->AA."""
    n = col_idx_0
    s = ""
    while True:
        s = chr(ord("A") + n % 26) + s
        n = n // 26 - 1
        if n < 0:
            break
    return s


# ---------- основная операция: внести заказ ----------

def add_order(buyer_name, aroma_name, volume, direction):
    """
    direction: 'плюс' или 'минус'.
    1) пишем строку в журнал
    2) пересчитываем клетку матрицы = journal_sum
    Возвращаем dict с результатом для показа.
    """
    buyer_name = buyer_name.strip()
    aroma_name = aroma_name.strip()
    volume = int(core.to_num(volume))

    book = _open_book()
    main_ws = _get_main_ws(book)
    journal_ws = _get_journal_ws(book)

    values, header, buyer_start = read_matrix(main_ws)

    # найти строку аромата
    aroma_row = find_aroma_row(values, aroma_name)
    if aroma_row is None:
        return {"ok": False, "msg": f"Аромат не найден: {aroma_name}"}

    # тег: основной, если покупателя ещё нет в журнале; иначе добор
    tag = "добор" if buyer_exists_in_journal(journal_ws, buyer_name) else "основной"

    # 1) строка в журнал
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    journal_ws.append_row(
        [stamp, buyer_name, aroma_name, volume, direction, tag],
        value_input_option="USER_ENTERED",
    )

    # 2) найти/создать столбец покупателя
    buyer_col = find_buyer_col(header, buyer_start, buyer_name)
    created = False
    if buyer_col is None:
        # новый покупатель — в конец, сразу за последним заполненным
        buyer_col = find_last_buyer_col(header, buyer_start) + 1
        main_ws.update_cell(1, buyer_col + 1, buyer_name)  # +1: gspread 1-индекс
        created = True

    # 3) пересчитать клетку из журнала и записать
    new_total = journal_sum(journal_ws, buyer_name, aroma_name)
    a1 = f"{col_to_a1(buyer_col)}{aroma_row + 1}"  # +1: gspread 1-индекс строки
    main_ws.update_acell(a1, new_total if new_total != 0 else "")

    return {
        "ok": True,
        "buyer": buyer_name,
        "aroma": aroma_name,
        "volume": volume,
        "direction": direction,
        "total": new_total,
        "created_buyer": created,
        "tag": tag,
    }


# ---------- сборка счетов из матрицы ----------

def build_invoices():
    """
    Проходит матрицу, считает счёт по каждому покупателю.
    Возвращает dict:
      summary  — [{buyer, total}], отсортировано, + grand_total
      details  — [{buyer, positions:[{aroma, volume, per_ml, amount}], total, problems:[...]}]
      export   — [[Имя, Аромат, Объём, Цена(за 1мл), Сумма], ...] для дашборда
      problems — [{buyer, aroma, volume, reason}]  общий список проблемных позиций
    Правила: hide-строки исключаются; сумма = ceil(цена_за_мл*объём);
             пустой грейд -> позиция помечается проблемной, в суммы не идёт.
    """
    book = _open_book()
    main_ws = _get_main_ws(book)
    values, header, buyer_start = read_matrix(main_ws)

    # столбцы покупателей (позиция, имя), кроме "Наличие"
    buyers = []
    for pos in range(buyer_start, len(header)):
        nm = core.norm(header[pos])
        if nm == "" or nm.lower() == core.BUYERS_ANCHOR:
            continue
        buyers.append((pos, nm))

    details = {nm: {"buyer": nm, "positions": [], "total": 0, "problems": []} for _, nm in buyers}
    export_rows = []
    all_problems = []

    for r in range(1, len(values)):
        row = values[r]
        name = core.norm(row[core.COL_NAME]) if core.COL_NAME < len(row) else ""
        if name == "":
            continue
        status = core.norm(row[core.COL_STATUS]).lower() if core.COL_STATUS < len(row) else ""
        if status in ("hide", "сервис"):
            continue  # не выкупили / служебное — в счёт не идёт

        category_raw = core.norm(row[core.COL_CATEGORY]).lower() if core.COL_CATEGORY < len(row) else ""
        category = core.CATEGORY_MAP.get(category_raw, category_raw.capitalize() if category_raw else "Разное")

        per_ml_list = [
            core.to_num(row[core.COL_PERML_1]) if core.COL_PERML_1 < len(row) else 0,
            core.to_num(row[core.COL_PERML_2]) if core.COL_PERML_2 < len(row) else 0,
            core.to_num(row[core.COL_PERML_3]) if core.COL_PERML_3 < len(row) else 0,
        ]

        for pos, buyer in buyers:
            vol = core.to_num(row[pos]) if pos < len(row) else 0
            if vol <= 0:
                continue
            calc = core.price_of_position(category, vol, per_ml_list)
            if not calc["ok"]:
                prob = {"buyer": buyer, "aroma": name, "volume": int(vol), "reason": calc["reason"]}
                details[buyer]["problems"].append(prob)
                all_problems.append(prob)
                continue
            per_ml = calc["per_ml"]
            amount = calc["amount"]
            details[buyer]["positions"].append({
                "aroma": name, "volume": int(vol), "per_ml": per_ml, "amount": amount,
            })
            details[buyer]["total"] += amount
            export_rows.append([buyer, name, int(vol), per_ml, amount])

    # только покупатели с позициями
    details_list = [d for d in details.values() if d["positions"] or d["problems"]]
    details_list.sort(key=lambda d: d["buyer"].lower())

    summary = [{"buyer": d["buyer"], "total": d["total"]} for d in details_list]
    grand_total = sum(d["total"] for d in details_list)

    return {
        "summary": summary,
        "grand_total": grand_total,
        "details": details_list,
        "export": export_rows,
        "problems": all_problems,
    }


# ---------- маршруты ----------

@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    df = core.load_data()
    rows, buyers = core.prepare_dataframe(df)
    # только показываемые (не hide/сервис) — вносим заказы по живым позициям
    aromas = [x["aroma_name"] for x in rows if x["status"] not in ("hide", "сервис")]

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "aromas": sorted(set(aromas), key=str.lower),
            "buyers": sorted(set(buyers), key=str.lower),
        },
    )


@router.get("/admin/current")
async def admin_current(buyer: str, aroma: str):
    """Текущее количество пары (покупатель, аромат) из матрицы + всего набрано."""
    try:
        book = _open_book()
        main_ws = _get_main_ws(book)
        values, header, buyer_start = read_matrix(main_ws)

        aroma_row = find_aroma_row(values, aroma)
        collected = 0
        buyer_qty = 0
        if aroma_row is not None:
            row = values[aroma_row]
            # всего набрано — столбец L (COL_COLLECTED)
            if core.COL_COLLECTED < len(row):
                collected = int(core.to_num(row[core.COL_COLLECTED]))
            # количество этого покупателя — из его клетки
            buyer_col = find_buyer_col(header, buyer_start, buyer)
            if buyer_col is not None and buyer_col < len(row):
                buyer_qty = int(core.to_num(row[buyer_col]))
        return {"buyer_qty": buyer_qty, "collected": collected}
    except Exception as e:
        return {"buyer_qty": "?", "collected": "?", "error": f"{type(e).__name__}: {e}"}


@router.post("/admin/add", response_class=HTMLResponse)
async def admin_add(
    request: Request,
    buyer: str = Form(...),
    aroma: str = Form(...),
    volume: str = Form(...),
    direction: str = Form("плюс"),
):
    try:
        result = add_order(buyer, aroma, volume, direction)
    except Exception as e:
        result = {"ok": False, "msg": f"{type(e).__name__}: {e}"}

    # После записи — снова показываем форму с результатом и сохранённым покупателем.
    df = core.load_data()
    rows, buyers = core.prepare_dataframe(df)
    aromas = [x["aroma_name"] for x in rows if x["status"] not in ("hide", "сервис")]

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "aromas": sorted(set(aromas), key=str.lower),
            "buyers": sorted(set(buyers), key=str.lower),
            "result": result,
            "last_buyer": buyer,
        },
    )


@router.get("/admin/invoices", response_class=HTMLResponse)
async def admin_invoices(request: Request):
    try:
        data = build_invoices()
        error = None
    except Exception as e:
        data = None
        error = f"{type(e).__name__}: {e}"
    return templates.TemplateResponse(
        "invoices.html",
        {"request": request, "data": data, "error": error},
    )