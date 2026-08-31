# catalog.py — aroma_web / постоянный "Ассортимент" (полный каталог товаров).
#
# Отдельная книга-каталог: полный перечень наименований (по контракту совпадает
# с наименованиями закупки). Две роли:
#   1) подсказка наименований организатору (заводить в Наличие любую позицию);
#   2) view-only вкладка "Ассортимент" на витрине (полный каталог с ценами).
#
# Лист: "Лист1" (gid 0). Шапка: Парфюм | (пусто) | Цена 10 мл | Цена 50 мл | Цена 100 мл.
# Читаем по именам столбцов (с синонимами). Каталог меняется редко -> кэш.

import re
import time

import core
import sheets

CATALOG_URL = "https://docs.google.com/spreadsheets/d/1J0eu0AF7qhCUtmnnstW8r0kihDp_0pGuGd9TVrBVTlY/edit"
CATALOG_GID = 0

NAME_ALIASES = ["парфюм", "наименование", "наличие", "товар", "название"]

_TTL = 120
_cache = {"items": None, "ts": 0.0}


def _name_col(low):
    for a in NAME_ALIASES:
        if a in low:
            return low.index(a)
    return 0


def _price_col(low, n):
    """Столбец 'Цена N мл' (точно по числу N)."""
    for i, h in enumerate(low):
        if h.startswith("цена"):
            m = re.search(r"(\d+)", h)
            if m and m.group(1) == str(n):
                return i
    return None


def catalog():
    """
    Полный каталог: [{name, p10, p50, p100}] (цены — числа, 0 если пусто).
    Секции/пустые строки пропускаем. Кэш 2 мин.
    """
    now = time.time()
    if _cache["items"] is not None and now - _cache["ts"] < _TTL:
        return _cache["items"]

    ws = sheets.worksheet_by_gid(CATALOG_GID, CATALOG_URL)
    values = ws.get_all_values()
    header = values[0] if values else []
    low = [core.norm(h).lower() for h in header]
    nc = _name_col(low)
    c10, c50, c100 = _price_col(low, 10), _price_col(low, 50), _price_col(low, 100)

    def cell(row, i):
        return row[i] if (i is not None and i < len(row)) else ""

    seen, out = set(), []
    for r in range(1, len(values)):
        row = values[r]
        name = core.norm(cell(row, nc))
        if name == "" or name.lower() in core.SECTION_WORDS:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name,
                    "p10": core.to_num(cell(row, c10)),
                    "p50": core.to_num(cell(row, c50)),
                    "p100": core.to_num(cell(row, c100))})
    out.sort(key=lambda x: x["name"].lower())
    _cache["items"] = out
    _cache["ts"] = now
    return out


def names():
    """Только наименования (для автоподсказки организатору)."""
    return [c["name"] for c in catalog()]
