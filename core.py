# core.py — aroma_web
# Чистый модуль чтения файла закупки по КОНТРАКТУ (по позициям столбцов).
# НЕ знает про FastAPI/веб — только данные. Используется и мордой (main.py),
# и режимом организатора (admin.py). Обе стороны читают контракт одинаково.
#
# КОНТРАКТ (позиции, 0-индексация):
#   0  A  — свободное поле (игнор)
#   1  B  — категория (Духи / Отдушки / База / Разное)
#   2  C  — статус (пусто=показать, hide=скрыть везде, сервис=видно в шите/скрыто в морде)
#   3  D  — наименование
#   4  E  — описание
#   5  F  — цена ступени 1 (витрина)
#   6  G  — цена ступени 2 (витрина)
#   7  H  — цена ступени 3 (витрина)
#   8  I  — цена за 1 мл, ступень 1 (истина для счёта; I<->F)
#   9  J  — цена за 1 мл, ступень 2 (J<->G)
#   10 K  — цена за 1 мл, ступень 3 (K<->H)
#   11 L  — Набрано (считает программа: "Наличие" + все покупатели)
#   12 M  — Набрать (регулирует Елена; программа выводит)
#   далее — столбец "Наличие" (якорь) и покупатели правее.
#
# Ступень существует, только если её цена-за-1-мл (I/J/K) > 0. Пусто/0 = ступени сейчас нет.

import pandas as pd
from urllib.parse import urlparse, parse_qs

# === НАСТРОЙКА ЗАКУПКИ (общая для морды и админки) ===
# Один файл читают обе стороны — значит ссылка одна, здесь.
SHEET_URL = "https://docs.google.com/spreadsheets/d/14-QBelupnGSBsYlF96naacCWdKAVbYl6TttWbd-nejQ/edit?gid=0#gid=0"

# Границы ступеней по категориям — какой объём в какую ступень попадает.
# Нижние границы трёх ступеней; ступень включается с этого объёма. None = ступени нет.
STAGE_BOUNDS = {
    "Духи":    [10, 50, 100],
    "Отдушки": [50, 100, 500],
    "База":    [500, 1000, None],
    "Разное":  [1, None, None],
}

# --- Позиции столбцов (0-индексация) ---
COL_CATEGORY = 1
COL_STATUS = 2
COL_NAME = 3
COL_DESC = 4
COL_PRICE_1 = 5
COL_PRICE_2 = 6
COL_PRICE_3 = 7
COL_PERML_1 = 8
COL_PERML_2 = 9
COL_PERML_3 = 10
COL_COLLECTED = 11
COL_TARGET = 12

BUYERS_ANCHOR = "наличие"

CATEGORY_MAP = {
    "духи": "Духи",
    "отдушки": "Отдушки",
    "отдушка": "Отдушки",
    "база": "База",
    "разное": "Разное",
    "расходники": "Разное",
    "флаконы": "Флаконы",
}

SECTION_WORDS = {"духи", "отдушки", "база", "разное", "флаконы", "расходники", "парфюм", "добор"}

SHOWCASE_LABELS = {
    "Духи":    ["Цена 10 мл", "Цена 50 мл", "Цена 100 мл"],
    "Отдушки": ["Цена 50 мл", "Цена 100 мл", "Цена 500 мл"],
    "База":    ["Цена 500 мл", "Цена 1000 мл", "Цена"],
}


# ---------- утилиты чтения ----------

def _gid_from_url(sheet_url: str):
    """Номер листа (gid) из ссылки. Ищем и в ?gid=, и в #gid=. None если нет."""
    parsed = urlparse(sheet_url)
    query = parse_qs(parsed.query)
    gid = query.get("gid", [None])[0]
    if gid is None and parsed.fragment.startswith("gid="):
        gid = parsed.fragment.split("=", 1)[1]
    return int(gid) if gid is not None and str(gid).isdigit() else None


import time

# Короткий кэш Ассортимента: он меняется редко, а витрина дёргает его часто.
LOAD_TTL_SECONDS = 60
_data_cache = {"df": None, "ts": 0.0}


def load_data(sheet_url: str = None) -> pd.DataFrame:
    """
    Ассортимент через сервисный аккаунт (книга на Шаге 3 приватная — публичного
    CSV больше нет). Возвращаем DataFrame БЕЗ шапки (header=None по духу): строки
    и столбцы читаются по позициям, ровно как раньше из CSV.
    Лист выбираем по gid из SHEET_URL; если gid нет — первый лист книги.
    Результат кэшируется на LOAD_TTL_SECONDS (только для основного SHEET_URL).
    """
    import sheets  # локальный импорт: sheets импортирует core, разрываем цикл

    url = sheet_url or SHEET_URL
    now = time.time()
    if url == SHEET_URL and _data_cache["df"] is not None \
            and now - _data_cache["ts"] < LOAD_TTL_SECONDS:
        return _data_cache["df"]

    gid = _gid_from_url(url)
    ws = sheets.worksheet_by_gid(gid) if gid is not None else sheets.open_book().sheet1

    values = ws.get_all_values()  # список строк, все клетки — строки
    # Выравниваем строки по ширине самой длинной (get_all_values иногда даёт
    # рваные строки), чтобы DataFrame был прямоугольным.
    width = max((len(r) for r in values), default=0)
    values = [r + [""] * (width - len(r)) for r in values]
    df = pd.DataFrame(values, dtype=str)

    if url == SHEET_URL:
        _data_cache["df"] = df
        _data_cache["ts"] = now
    return df


def norm(value) -> str:
    if value is None:
        return ""
    s = str(value)
    if s.lower() in ("nan", "none"):
        return ""
    return s.strip().replace("\u00a0", " ").replace("\n", " ").strip()


def to_num(value) -> float:
    s = norm(value).replace(" ", "").replace(",", ".")
    if s == "":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def cell(df, row_idx, col_pos):
    if col_pos < df.shape[1]:
        return df.iat[row_idx, col_pos]
    return ""


def find_buyer_start(df) -> int:
    """Позиция столбца-якоря 'Наличие' в шапке. С него и правее — покупатели."""
    if df.shape[0] == 0:
        return 13
    header = df.iloc[0]
    for pos in range(df.shape[1]):
        if norm(header.iat[pos]).lower() == BUYERS_ANCHOR:
            return pos
    return 13


def stage_for_volume(category: str, volume: float):
    """
    По категории и объёму вернуть номер ступени (0/1/2) или None.
    Использует STAGE_BOUNDS. Для расчёта суммы в админке (Шаг 2).
    """
    bounds = STAGE_BOUNDS.get(category)
    if not bounds:
        return None
    stage = None
    for i, low in enumerate(bounds):
        if low is None:
            continue
        if volume >= low:
            stage = i
    return stage


import math


def price_of_position(category: str, volume: float, per_ml_list):
    """
    Считает сумму одной позиции по грейду.
    per_ml_list — [цена_за_мл_ст1, ст2, ст3] (столбцы I/J/K).
    Возвращает dict:
      ok=True  -> {ok, stage, per_ml, amount}
      ok=False -> {ok, reason}   (проблемная позиция: нет цены в нужном грейде)
    Правило: сумма = ceil(цена_за_мл * объём). Округление ВВЕРХ до рубля.
    Пустая/0 цена в грейде -> ступени нет: пробуем ближайшую доступную СНИЗУ,
      если и её нет -> проблема (по договорённости: помечаем, не считаем молча).
    """
    if volume <= 0:
        return {"ok": False, "reason": "нулевой объём"}

    stage = stage_for_volume(category, volume)
    if stage is None:
        return {"ok": False, "reason": f"объём {volume} вне ступеней категории {category}"}

    # цена нужного грейда; если пусто/0 — ищем ближайшую доступную ниже
    per_ml = 0.0
    used_stage = None
    for i in range(stage, -1, -1):
        if i < len(per_ml_list) and per_ml_list[i] and per_ml_list[i] > 0:
            per_ml = per_ml_list[i]
            used_stage = i
            break

    if per_ml <= 0:
        return {"ok": False, "reason": f"нет цены за 1 мл для объёма {volume} ({category})"}

    amount = math.ceil(per_ml * volume)
    return {"ok": True, "stage": used_stage, "per_ml": per_ml, "amount": amount}


def buyer_columns(df):
    """Список (позиция, имя) покупателей, начиная с якоря 'Наличие' (включая его)."""
    buyer_start = find_buyer_start(df)
    header = df.iloc[0] if df.shape[0] else None
    cols = []
    if header is not None:
        for pos in range(buyer_start, df.shape[1]):
            name = norm(header.iat[pos])
            if name == "":
                continue
            cols.append((pos, name))
    return cols


def prepare_dataframe(df: pd.DataFrame, user_name: str = ""):
    """
    Разбор сырого df (без шапки, по позициям) -> (rows, buyer_names).
    rows — список товаров-словарей; buyer_names — имена покупателей (без 'Наличие').
    """
    cols = buyer_columns(df)
    buyer_names = [n for (_, n) in cols if n.lower() != BUYERS_ANCHOR]

    # Столбец "Примечание" (по названию шапки, позиция свободная). "новинка" -> вкладка Новинки.
    note_col = None
    if df.shape[0]:
        header = df.iloc[0]
        for pos in range(df.shape[1]):
            if norm(header.iat[pos]).lower() in ("примечание", "заметка", "примечания"):
                note_col = pos
                break

    user_col_pos = None
    if user_name and user_name.strip():
        target = user_name.strip().lower()
        for pos, name in cols:
            if name.lower() == target:
                user_col_pos = pos
                break

    rows = []
    for r in range(1, df.shape[0]):
        name = norm(cell(df, r, COL_NAME))
        if name == "":
            continue

        category_raw = norm(cell(df, r, COL_CATEGORY)).lower()
        status = norm(cell(df, r, COL_STATUS)).lower()

        if name.lower() in SECTION_WORDS and category_raw == "":
            continue

        category = CATEGORY_MAP.get(
            category_raw, category_raw.capitalize() if category_raw else "Разное"
        )
        desc = norm(cell(df, r, COL_DESC))

        labels = SHOWCASE_LABELS.get(category, ["Цена 1", "Цена 2", "Цена 3"])
        showcase = []
        for i, cpos in enumerate([COL_PRICE_1, COL_PRICE_2, COL_PRICE_3]):
            val = to_num(cell(df, r, cpos))
            if val > 0:
                showcase.append({"label": labels[i], "value": val})
        first_price = showcase[0]["value"] if showcase else 0

        per_ml = [
            to_num(cell(df, r, COL_PERML_1)),
            to_num(cell(df, r, COL_PERML_2)),
            to_num(cell(df, r, COL_PERML_3)),
        ]

        collected = int(to_num(cell(df, r, COL_COLLECTED)))
        target = int(to_num(cell(df, r, COL_TARGET)))

        ordered_ml = 0
        if user_col_pos is not None:
            ordered_ml = int(to_num(cell(df, r, user_col_pos)))

        note = norm(cell(df, r, note_col)) if note_col is not None else ""

        rows.append({
            "row_index": r,                 # позиция строки в файле (пригодится админке)
            "aroma_name": name,
            "category": category,
            "status": status,
            "view": desc,
            "prices": showcase,
            "price": first_price,
            "per_ml": per_ml,
            "collected": collected,
            "remaining": target,
            "ordered_ml": ordered_ml,
            "note": note,
            "is_new": "новинка" in note.lower(),
        })

    return rows, buyer_names