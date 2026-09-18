# sheets.py — aroma_web / единая точка подключения к гуглтаблицам.
#
# Поддерживает НЕСКОЛЬКО книг (закупка + Наличие) на одном сервисном аккаунте:
#   - без аргумента url работаем с книгой закупки (core.SHEET_URL);
#   - с url="..." — с любой другой книгой (напр. Наличие).
# Подключение (клиент, книги, листы) кэшируется на процесс; ретраи и таймаут —
# на обрывах канала (частая беда доступа к Google из РФ).

import os
import time
from urllib.parse import urlparse

import gspread
from google.oauth2.service_account import Credentials

import core

KEY_PATHS = [
    "/etc/secrets/service_account.json",   # Render
    "service_account.json",                # локально
]
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# --- кэши на процесс ---
_client = None
_books = {}          # id -> Spreadsheet
_ws_title = {}       # (id, title) -> Worksheet
_ws_gid = {}         # (id, gid) -> Worksheet


def spreadsheet_id(url: str = None) -> str:
    """ID книги из url (или core.SHEET_URL по умолчанию)."""
    parts = urlparse(url or core.SHEET_URL).path.split("/")
    return parts[parts.index("d") + 1]


def _find_key_path() -> str:
    for p in KEY_PATHS:
        if os.path.exists(p):
            return p
    raise FileNotFoundError(
        "Ключ сервисного аккаунта не найден: " + " или ".join(KEY_PATHS)
    )


def _install_retries(client):
    """
    Автоповтор идемпотентных запросов (GET/PUT) на обрывах и 429/5xx + таймаут.
    POST (append) не повторяем, чтобы не задвоить заказ.

    ВАЖНО про лимиты времени (иначе один запрос уходит за 60 с nginx → белый экран):
      - таймаут (connect=4, read=8) применяется gspend'ом И к запросам данных,
        И к обновлению OAuth-токена (google-auth берёт тот же timeout);
      - повторов немного (total=2 → до 3 попыток), чтобы худший случай одного
        чтения был ~3×12 + бэкофф ≈ 37 с, а не 84 с.
    """
    try:
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        retry = Retry(
            total=2, connect=2, read=2, backoff_factor=0.4,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=frozenset(["GET", "PUT", "HEAD", "OPTIONS", "DELETE"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session = client.http_client.session
        session.mount("https://", adapter)
        session.mount("http://", adapter)
    except Exception:
        pass
    try:
        client.set_timeout((4, 8))   # (connect, read) в секундах
    except Exception:
        pass


# ---------- короткий кэш прочитанных значений листов ----------
# index на витрине делает 4–6 сетевых чтений; при флапе Google это копится.
# Кэшируем values по строковому ключу на TTL и сбрасываем при записи.
_vcache = {}   # key -> (ts, values)


def vget(key: str, ttl: float, fetch):
    """Вернуть закэшированные values (моложе ttl) или прочитать через fetch()."""
    now = time.time()
    hit = _vcache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    values = fetch()
    _vcache[key] = (now, values)
    return values


def vdrop(*keys):
    """Сбросить кэш: конкретные ключи или весь (без аргументов)."""
    if not keys:
        _vcache.clear()
    for k in keys:
        _vcache.pop(k, None)


def _get_client():
    global _client
    if _client is None:
        creds = Credentials.from_service_account_file(_find_key_path(), scopes=SCOPES)
        _client = gspread.authorize(creds)
        _install_retries(_client)
    return _client


def open_book(url: str = None):
    """Открытая книга (кэшируется по id). url=None -> книга закупки."""
    sid = spreadsheet_id(url)
    if sid not in _books:
        _books[sid] = _get_client().open_by_key(sid)
    return _books[sid]


def worksheet_by_gid(gid: int, url: str = None):
    """Лист по номеру gid в нужной книге (кэшируется)."""
    sid = spreadsheet_id(url)
    key = (sid, gid)
    if key not in _ws_gid:
        _ws_gid[key] = open_book(url).get_worksheet_by_id(gid)
    return _ws_gid[key]


def worksheet_by_title(title: str, url: str = None):
    """Лист по имени в нужной книге (кэшируется)."""
    sid = spreadsheet_id(url)
    key = (sid, title)
    if key not in _ws_title:
        _ws_title[key] = open_book(url).worksheet(title)
    return _ws_title[key]


def get_or_create_ws(title: str, header: list, url: str = None):
    """
    Лист по имени БЕЗ учёта регистра/пробелов (в Google имена листов
    регистронезависимы: "поток" == "Поток"). Создаём только если реально нет —
    иначе add_worksheet упадёт конфликтом имён.
    """
    sid = spreadsheet_id(url)
    key = (sid, title)
    if key in _ws_title:
        return _ws_title[key]
    book = open_book(url)
    want = title.strip().lower()
    ws = None
    for w in book.worksheets():
        if w.title.strip().lower() == want:
            ws = w
            break
    if ws is None:
        ws = book.add_worksheet(title=title, rows=1000, cols=max(len(header), 1))
        if header:
            ws.update(f"A1:{col_a1(len(header) - 1)}1", [header])
    _ws_title[key] = ws
    return ws


def reset_cache():
    """Сбросить кэши подключения (на случай проблем с токеном/структурой)."""
    global _client
    _client = None
    _books.clear()
    _ws_title.clear()
    _ws_gid.clear()


def col_a1(col_idx_0: int) -> str:
    """0-индекс столбца -> буква(ы) A1. 0->A, 26->AA."""
    n = col_idx_0
    s = ""
    while True:
        s = chr(ord("A") + n % 26) + s
        n = n // 26 - 1
        if n < 0:
            break
    return s
