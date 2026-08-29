# sheets.py — aroma_web / единая точка подключения к гуглтаблице.
#
# Зачем этот файл:
#   На Шаге 3 книга приватная — Ассортимент, Поток и Пользователи читаются/пишутся
#   только через сервисный аккаунт (gspread). Чтобы способ открыть книгу был ОДИН,
#   всё подключение живёт здесь.
#
# ПРОИЗВОДИТЕЛЬНОСТЬ: подключение (ключ, OAuth-токен, открытая книга и листы)
# КЭШИРУЕТСЯ на процесс. Иначе каждый запрос заново авторизовывался и заново
# открывал книгу — это давало десятки секунд задержки. Токен google-auth
# обновляет себя сам, так что держать клиент открытым безопасно.

import os
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
_book = None
_ws_by_title = {}
_ws_by_gid = {}


def spreadsheet_id() -> str:
    """ID книги из core.SHEET_URL (.../d/<ID>/edit...)."""
    parts = urlparse(core.SHEET_URL).path.split("/")
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
    Автоповтор на обрывах/временных ошибках Google (частая беда канала из РФ:
    'Remote end closed connection without response' на переиспользованном
    keep-alive соединении). Повторяем ТОЛЬКО идемпотентные методы:
      GET  — чтение листов,
      PUT  — правка ячейки (update_acell: ставит значение, повтор безопасен).
    POST (append_row/append_rows — добавление заказа) НЕ повторяем: при обрыве
    неизвестно, применился ли он, и повтор мог бы задвоить строку.
    Плюс общий таймаут, чтобы зависший запрос не висел бесконечно.
    """
    try:
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        retry = Retry(
            total=4, connect=4, read=4, backoff_factor=0.6,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=frozenset(["GET", "PUT", "HEAD", "OPTIONS", "DELETE"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session = client.http_client.session
        session.mount("https://", adapter)
        session.mount("http://", adapter)
    except Exception:
        pass  # ретраи — улучшение, не критично
    try:
        client.set_timeout(20)  # секунд на запрос
    except Exception:
        pass


def _get_client():
    global _client
    if _client is None:
        creds = Credentials.from_service_account_file(_find_key_path(), scopes=SCOPES)
        _client = gspread.authorize(creds)
        _install_retries(_client)
    return _client


def open_book():
    """Открытая книга (кэшируется на процесс)."""
    global _book
    if _book is None:
        _book = _get_client().open_by_key(spreadsheet_id())
    return _book


def worksheet_by_gid(gid: int):
    """Лист по номеру gid (кэшируется)."""
    if gid not in _ws_by_gid:
        _ws_by_gid[gid] = open_book().get_worksheet_by_id(gid)
    return _ws_by_gid[gid]


def get_or_create_ws(title: str, header: list):
    """Лист по имени; если его нет — создаём с шапкой. Результат кэшируется."""
    if title in _ws_by_title:
        return _ws_by_title[title]
    book = open_book()
    try:
        ws = book.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = book.add_worksheet(title=title, rows=1000, cols=max(len(header), 1))
        if header:
            ws.update(f"A1:{col_a1(len(header) - 1)}1", [header])
    _ws_by_title[title] = ws
    return ws


def reset_cache():
    """Сбросить кэши подключения (на случай проблем с токеном/структурой)."""
    global _client, _book
    _client = None
    _book = None
    _ws_by_title.clear()
    _ws_by_gid.clear()


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
