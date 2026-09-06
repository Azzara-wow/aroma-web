# auth.py — aroma_web / авторизация покупателей (Шаг 3).
#
# Что здесь:
#   - сессия на подписанной куке (HMAC из стандартной библиотеки, без зависимостей);
#   - лимит попыток входа (счётчик в памяти процесса — на Render один инстанс);
#   - резолв текущего пользователя из куки;
#   - маршруты: страница входа, вход, регистрация, задать код заново, выход.
#
# Личность = телефон (см. users.py). Телефон/код НИКОГДА не уходят в URL —
# только POST-формой; после входа помним по куке.
#
# ВАЖНО (Render): задай переменную окружения SESSION_SECRET (длинная случайная
# строка) — ею подписываются куки. Если не задать, на старте берётся случайный
# секрет процесса (локально ок; на Render это разлогинит всех при каждом рестарте).

import os
import time
import hmac
import base64
import hashlib
import secrets

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import users

router = APIRouter()
templates = Jinja2Templates(directory="templates")

COOKIE_NAME = "aroma_session"
SESSION_TTL = 30 * 24 * 3600           # 30 дней

# Секрет подписи куки. Из окружения; иначе — случайный на процесс (с предупреждением).
_env_secret = os.environ.get("SESSION_SECRET", "").strip()
if not _env_secret:
    print("ВНИМАНИЕ: SESSION_SECRET не задан — беру случайный секрет процесса "
          "(на Render это разлогинит всех при рестарте). Задай SESSION_SECRET.")
SECRET = (_env_secret or secrets.token_hex(32)).encode("utf-8")

# --- лимит попыток входа ---
MAX_FAILS = 5
BLOCK_SECONDS = 15 * 60
_fails = {}  # телефон -> список меток времени неудачных попыток


# ======================================================================
#  Токен сессии: <phone>.<ts>.<sig>  (phone — только цифры, точек не содержит)
# ======================================================================

def _sign(msg: bytes) -> str:
    digest = hmac.new(SECRET, msg, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def make_token(phone: str) -> str:
    body = f"{phone}.{int(time.time())}"
    return f"{body}.{_sign(body.encode('utf-8'))}"


def read_token(token: str):
    """Вернуть телефон, если подпись и срок валидны; иначе None."""
    try:
        phone, ts, sig = str(token).split(".")
    except (ValueError, AttributeError):
        return None
    body = f"{phone}.{ts}"
    if not hmac.compare_digest(sig, _sign(body.encode("utf-8"))):
        return None
    try:
        if int(time.time()) - int(ts) > SESSION_TTL:
            return None
    except ValueError:
        return None
    return phone


# ======================================================================
#  Кука
# ======================================================================

def _set_cookie(response, request: Request, phone: str):
    # secure ставим по схеме запроса: на Render (https) — да, на localhost (http) — нет.
    secure = request.url.scheme == "https"
    response.set_cookie(
        COOKIE_NAME, make_token(phone),
        max_age=SESSION_TTL, httponly=True, samesite="lax", secure=secure,
    )


def _clear_cookie(response):
    response.delete_cookie(COOKIE_NAME)


def current_user(request: Request):
    """Текущий пользователь (dict из users) или None. Читает лист Пользователи."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    phone = read_token(token)
    if not phone:
        return None
    return users.get_user(phone)


# ======================================================================
#  Лимит попыток
# ======================================================================

def _prune(phone):
    now = time.time()
    kept = [t for t in _fails.get(phone, []) if now - t < BLOCK_SECONDS]
    if kept:
        _fails[phone] = kept
    else:
        _fails.pop(phone, None)
    return kept


def is_blocked(phone) -> bool:
    return len(_prune(phone)) >= MAX_FAILS


def _record_fail(phone):
    _prune(phone)
    _fails.setdefault(phone, []).append(time.time())


def _reset_fails(phone):
    _fails.pop(phone, None)


# ======================================================================
#  Маршруты
# ======================================================================

def _login_page(request, tab="login", phone="", msg="", status=200):
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "tab": tab, "phone": phone, "msg": msg},
        status_code=status,
    )


@router.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    # уже вошли — на витрину
    if current_user(request):
        return RedirectResponse("/", status_code=303)
    return _login_page(
        request,
        tab=request.query_params.get("tab", "login"),
        phone=request.query_params.get("phone", ""),
        msg=request.query_params.get("msg", ""),
    )


@router.post("/login")
def login_post(request: Request, phone: str = Form(...), code: str = Form(...)):
    canon = users.normalize_phone(phone)

    if is_blocked(canon):
        return _login_page(request, "login", phone,
                           "Слишком много попыток. Подождите 15 минут.")

    res = users.verify_login(phone, code)
    if res["ok"]:
        _reset_fails(canon)
        resp = RedirectResponse("/", status_code=303)
        _set_cookie(resp, request, res["user"]["phone"])
        return resp

    reason = res["reason"]
    if reason == "not_found":
        return _login_page(request, "register", phone,
                           "Телефон не найден — зарегистрируйтесь.")
    if reason == "no_code":
        return _login_page(request, "setcode", phone,
                           "Код ещё не задан — придумайте его.")
    # bad_code
    _record_fail(canon)
    return _login_page(request, "login", phone, "Неверный код.")


@router.post("/register")
def register_post(
    request: Request,
    phone: str = Form(...),
    name: str = Form(...),
    code: str = Form(...),
    address: str = Form(""),
):
    res = users.register(phone, name, code, address)
    if res["ok"]:
        resp = RedirectResponse("/", status_code=303)
        _set_cookie(resp, request, res["user"]["phone"])
        return resp
    return _login_page(request, "register", phone, res["reason"])


@router.post("/set-code")
def set_code_post(request: Request, phone: str = Form(...), code: str = Form(...)):
    # Разрешаем задать код только если телефон есть, а код пуст (был сброшен).
    # Если код уже стоит — не даём перезаписать без входа (защита от угона).
    user = users.get_user(phone)
    if user is None:
        return _login_page(request, "register", phone,
                           "Телефон не найден — зарегистрируйтесь.")
    if user["code_hash"]:
        return _login_page(request, "login", phone,
                           "Код уже задан — войдите с ним.")
    res = users.set_code(phone, code)
    if not res["ok"]:
        return _login_page(request, "setcode", phone, res["reason"])
    resp = RedirectResponse("/", status_code=303)
    _set_cookie(resp, request, users.normalize_phone(phone))
    return resp


@router.get("/logout")
def logout(request: Request):
    resp = RedirectResponse("/login", status_code=303)
    _clear_cookie(resp)
    return resp
