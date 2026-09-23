# admin.py — aroma_web / режим организатора (Шаг 3, на Потоке)
#
# Что делает:
#   GET  /admin           — форма ввода/правки заказов (плюс/минус) в Поток
#   GET  /admin/current   — сколько уже есть у покупателя + всего набрано (из Потока)
#   POST /admin/add       — внести заказ строкой в Поток (flow.add_order)
#   GET  /admin/invoices  — счёт по каждому покупателю (свёртка Потока × цены Ассортимента)
#
# Матрицы и листа "Журнал" больше нет — единственный источник заказов это Поток.
# Доступ ко всем маршрутам — только для роли 'организатор' (users.is_admin),
# личность берётся из куки (auth.current_user). Секретное слово ADMIN_NAME не нужно.

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

import core
import users
import flow
import auth
import nalichie
import catalog
import notify
import orders_state
import source_state


# ---------- покупатель по имени (не по телефону) ----------

def _buyer_options():
    """Покупатели для автоподсказки: [{name, phone}] из Пользователей + потока закупки."""
    opts = {}
    try:
        for u in users.list_users():
            if u["phone"]:
                opts[u["phone"]] = u["name"] or opts.get(u["phone"], "")
    except Exception:
        pass
    try:
        for phone, u in flow.net_orders().items():
            if phone and not opts.get(phone):
                opts[phone] = u.get("name", "")
    except Exception:
        pass
    lst = [{"name": n or p, "phone": p} for p, n in opts.items()]
    lst.sort(key=lambda x: x["name"].lower())
    return lst


def _resolve_buyer(raw, options):
    """Из ввода 'Имя — телефон' / телефона / имени -> (phone|None, name)."""
    raw = (raw or "").strip()
    canon = users.normalize_phone(raw)
    if users.valid_phone(canon):
        for o in options:
            if o["phone"] == canon:
                return canon, o["name"]
        return canon, ""
    if "—" in raw:
        head, tail = raw.rsplit("—", 1)
        t = users.normalize_phone(tail)
        if users.valid_phone(t):
            return t, head.strip()
    low = raw.lower()
    for o in options:
        if o["name"].lower() == low:
            return o["phone"], o["name"]
    return None, raw

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _require_admin(request: Request):
    """Текущий пользователь-организатор или None."""
    user = auth.current_user(request)
    return user if users.is_admin(user) else None


def _live_aromas():
    """Список показываемых ароматов Ассортимента (не hide/сервис) — для подсказок."""
    rows, _ = core.prepare_dataframe(core.load_data())
    aromas = [x["aroma_name"] for x in rows if x["status"] not in ("hide", "сервис")]
    return sorted(set(aromas), key=str.lower)


# ---------- сборка счетов из Потока ----------

def build_invoices():
    """
    Счёт по каждому покупателю: остатки из Потока × цены из Ассортимента.
    Контракт вывода не меняется (invoices.html): summary / grand_total /
    details / export / problems.
      - позиция без валидной цены в грейде -> проблемная (в сумму не идёт);
      - аромат, которого нет в Ассортименте (или скрыт) -> тоже проблемная.
    """
    rows, _ = core.prepare_dataframe(core.load_data())
    info = {}
    for x in rows:
        if x["status"] in ("hide", "сервис"):
            continue
        info[x["aroma_name"].lower()] = {
            "name": x["aroma_name"],
            "category": x["category"],
            "per_ml": x["per_ml"],
            "price": x["price"],   # цена за штуку — для штучных категорий (База)
        }

    orders = flow.net_orders()  # phone -> {"name", "aromas": {аромат: мл}}

    # Актуальные имена из Пользователей (динамически): имя в Потоке — «снимок» на
    # момент заказа, а тут берём текущее по телефону. Fallback — снимок, потом телефон.
    try:
        current_names = {u["phone"]: u["name"] for u in users.list_users() if u["name"]}
    except Exception:
        current_names = {}

    details = []
    export_rows = []
    all_problems = []

    for phone, u in orders.items():
        buyer = current_names.get(phone) or u["name"] or phone
        d = {"buyer": buyer, "phone": phone, "positions": [], "total": 0, "problems": []}
        for aroma, vol in u["aromas"].items():
            meta = info.get(aroma.lower())
            if meta is None:
                prob = {"buyer": buyer, "aroma": aroma, "volume": int(vol),
                        "reason": "нет в Ассортименте или скрыт"}
                d["problems"].append(prob)
                all_problems.append(prob)
                continue
            calc = core.price_of_position(meta["category"], vol, meta["per_ml"],
                                          piece_price=meta.get("price"))
            if not calc["ok"]:
                prob = {"buyer": buyer, "aroma": meta["name"], "volume": int(vol),
                        "reason": calc["reason"]}
                d["problems"].append(prob)
                all_problems.append(prob)
                continue
            d["positions"].append({
                "aroma": meta["name"], "volume": int(vol),
                "per_ml": calc["per_ml"], "amount": calc["amount"],
            })
            d["total"] += calc["amount"]
            export_rows.append([f"{phone} - {buyer}", meta["name"], int(vol), calc["per_ml"], calc["amount"]])

        if d["positions"] or d["problems"]:
            details.append(d)

    details.sort(key=lambda d: d["buyer"].lower())
    summary = [{"buyer": d["buyer"], "phone": d["phone"], "total": d["total"]} for d in details]
    grand_total = sum(d["total"] for d in details)

    return {
        "summary": summary,
        "grand_total": grand_total,
        "details": details,
        "export": export_rows,
        "problems": all_problems,
    }


# ---------- маршруты ----------

def _admin_ctx(request, **extra):
    """Общий контекст страницы закупки (чтобы блоки статуса были и после /admin/add)."""
    ctx = {
        "request": request,
        "aromas": _live_aromas(),
        "users_list": _buyer_options(),
        "orders_open": orders_state.is_open(),
        "source_url": core.current_source_url(),
        "source_custom": source_state.is_custom(),
        "source_result": request.query_params.get("src"),
    }
    ctx.update(extra)
    return ctx


@router.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    if not _require_admin(request):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("admin.html", _admin_ctx(request))


@router.post("/admin/orders")
def admin_orders_toggle(request: Request, action: str = Form(...)):
    """Открыть/закрыть приём заказов покупателями (action=open|close)."""
    if not _require_admin(request):
        return RedirectResponse("/login", status_code=303)
    orders_state.set_open(action == "open")
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/source")
def admin_source(request: Request, url: str = Form(""), action: str = Form("set")):
    """Сменить активную книгу закупки (action=set) или вернуть ссылку из кода (reset).
    Меняется без правки сервера и без рестарта; кэш Ассортимента сбрасываем сразу."""
    if not _require_admin(request):
        return RedirectResponse("/login", status_code=303)
    if action == "reset":
        source_state.reset()
        core.reset_data_cache()
        return RedirectResponse("/admin?src=reset", status_code=303)
    ok = source_state.set_url(url)
    if ok:
        core.reset_data_cache()
    return RedirectResponse(f"/admin?src={'ok' if ok else 'bad'}", status_code=303)


def _current_names():
    """Телефон -> текущее имя из «Покупателей» (для читаемых списков организатора)."""
    try:
        return {u["phone"]: u["name"] for u in users.list_users() if u["name"]}
    except Exception:
        return {}


@router.get("/admin/by_aroma")
def admin_by_aroma(request: Request, aroma: str):
    """Кто набрал аромат — очередь с временем (для режима «Убрать» и раскрытия витрины)."""
    if not _require_admin(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        names = _current_names()
        buyers = []
        for e in flow.buyers_of_aroma(aroma):
            buyers.append({
                "phone": e["phone"],
                "name": names.get(e["phone"]) or e["name"] or e["phone"],
                "ml": e["ml"],
                "ts": e.get("first_ts", ""),
                "last_ts": e.get("last_ts", ""),
            })
        total = sum(b["ml"] for b in buyers)
        return {"ok": True, "aroma": aroma, "buyers": buyers, "total": total}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@router.get("/admin/by_buyer")
def admin_by_buyer(request: Request, phone: str):
    """Что набрала девочка — её ароматы с объёмами (для режима «Убрать» по имени)."""
    if not _require_admin(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        phone_val, name = _resolve_buyer(phone, _buyer_options())
        if not phone_val:
            return {"ok": False, "error": "Покупатель не найден"}
        name = _current_names().get(phone_val) or name or phone_val
        positions = flow.buyer_positions(phone_val)
        aromas = [{"aroma": a, "ml": int(v)} for a, v in positions.items()]
        aromas.sort(key=lambda x: x["aroma"].lower())
        return {"ok": True, "phone": phone_val, "name": name, "aromas": aromas}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@router.post("/admin/cut")
def admin_cut(request: Request,
              phone: str = Form(...), aroma: str = Form(...),
              volume: str = Form(...), name: str = Form("")):
    """Отрезать объём у конкретной пары (телефон, аромат) — строка 'минус' в Поток.
    Живой ответ JSON, чтобы список в «Убрать» обновлялся без перезагрузки."""
    if not _require_admin(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        res = flow.add_order(phone, name, aroma, volume, direction=flow.DIR_MINUS)
        if not res.get("ok"):
            res["error"] = res.get("reason", "ошибка")
        return res
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@router.get("/admin/buyers", response_class=HTMLResponse)
def admin_buyers(request: Request):
    if not _require_admin(request):
        return RedirectResponse("/login", status_code=303)
    return _render_buyers(request)


def _render_buyers(request, **extra):
    try:
        buyers = users.list_full()
    except Exception as e:
        buyers = []
        extra.setdefault("load_error", f"{type(e).__name__}: {e}")
    ctx = {"request": request, "buyers": buyers}
    ctx.update(extra)
    return templates.TemplateResponse("admin_buyers.html", ctx)


@router.post("/admin/buyers/add", response_class=HTMLResponse)
def admin_buyers_add(
    request: Request,
    phone: str = Form(...),
    name: str = Form(...),
    address: str = Form(""),
):
    if not _require_admin(request):
        return RedirectResponse("/login", status_code=303)
    try:
        res = users.add_buyer(phone, name, address)
        result = ({"ok": True, "name": res["name"], "phone": res["phone"]}
                  if res.get("ok") else {"ok": False, "msg": res.get("reason", "ошибка")})
    except Exception as e:
        result = {"ok": False, "msg": f"{type(e).__name__}: {e}"}
    return _render_buyers(request, result=result)


@router.get("/admin/telegram", response_class=HTMLResponse)
def admin_telegram(request: Request):
    if not _require_admin(request):
        return RedirectResponse("/login", status_code=303)
    test_res = notify.test() if request.query_params.get("test") else None
    info = notify.recent_chats()
    return templates.TemplateResponse("admin_telegram.html", {
        "request": request,
        "token_set": bool(notify.TG_TOKEN),
        "chat_set": bool(notify.TG_CHAT),
        "current": notify.TG_CHAT,
        "chats": info.get("chats", []),
        "chats_error": info.get("error"),
        "test_res": test_res,
    })


@router.get("/admin/current")
def admin_current(request: Request, phone: str, aroma: str):
    """Остаток пары (телефон, аромат) + всего набрано — из Потока."""
    if not _require_admin(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        return flow.pair_and_collected(phone, aroma)
    except Exception as e:
        return {"buyer_qty": "?", "collected": "?", "error": f"{type(e).__name__}: {e}"}


@router.post("/admin/add", response_class=HTMLResponse)
def admin_add(
    request: Request,
    phone: str = Form(...),
    aroma: str = Form(...),
    volume: str = Form(...),
    direction: str = Form("плюс"),
):
    if not _require_admin(request):
        return RedirectResponse("/login", status_code=303)

    options = _buyer_options()
    phone_val, name = _resolve_buyer(phone, options)

    if not phone_val:
        result = {"ok": False, "msg": "Покупатель не найден — выберите из списка или введите телефон 7XXXXXXXXXX"}
    else:
        try:
            result = flow.add_order(phone_val, name, aroma, volume, direction)
            if result.get("ok"):
                result["buyer"] = name or result.get("phone", phone_val)
            else:
                result["msg"] = result.get("reason", "ошибка")
        except Exception as e:
            result = {"ok": False, "msg": f"{type(e).__name__}: {e}"}

    return templates.TemplateResponse(
        "admin.html",
        _admin_ctx(request, users_list=options, result=result, last_phone=phone),
    )


# ---------- организаторский режим: Наличие ----------

def _nalichie_names():
    try:
        return sorted({i["name"] for i in nalichie.assortment()}, key=str.lower)
    except Exception:
        return []


def _catalog_names():
    try:
        return catalog.names()
    except Exception:
        return []


def _render_nalichie(request, **extra):
    ctx = {"request": request, "items": _nalichie_names(),
           "users_list": _buyer_options(), "catalog_names": _catalog_names()}
    ctx.update(extra)
    return templates.TemplateResponse("admin_nalichie.html", ctx)


@router.get("/admin/nalichie", response_class=HTMLResponse)
def admin_nalichie_page(request: Request):
    if not _require_admin(request):
        return RedirectResponse("/login", status_code=303)
    return _render_nalichie(request)


@router.get("/admin/nalichie/current")
def admin_nalichie_current(request: Request, item: str):
    """Остаток/доступно по товару склада — для подсказки в форме."""
    if not _require_admin(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        target = item.strip().lower()
        for i in nalichie.assortment():
            if i["name"].lower() == target:
                return {"available": i["available"], "stock": i["stock"],
                        "consumed": i["consumed"], "per_ml": i["per_ml"]}
        return {"available": "—", "note": "нет такого товара"}
    except Exception as e:
        return {"available": "?", "error": f"{type(e).__name__}: {e}"}


@router.post("/admin/nalichie/item", response_class=HTMLResponse)
def admin_nalichie_item(
    request: Request,
    name: str = Form(...),
    stock: str = Form("0"),
    per_ml: str = Form("0"),
    show: str = Form(""),
):
    if not _require_admin(request):
        return RedirectResponse("/login", status_code=303)
    try:
        res = nalichie.add_item(name, stock, per_ml, show)
        item_result = {"ok": res.get("ok"),
                       "msg": ("Добавлено: " + res["name"]) if res.get("ok") else res.get("reason", "ошибка")}
    except Exception as e:
        item_result = {"ok": False, "msg": f"{type(e).__name__}: {e}"}
    return _render_nalichie(request, item_result=item_result)


@router.post("/admin/nalichie/order", response_class=HTMLResponse)
def admin_nalichie_order(
    request: Request,
    phone: str = Form(...),
    item: str = Form(...),
    qty: str = Form(...),
    direction: str = Form("плюс"),
    done: str = Form(""),
):
    if not _require_admin(request):
        return RedirectResponse("/login", status_code=303)
    phone_val, name = _resolve_buyer(phone, _buyer_options())
    if not phone_val:
        return _render_nalichie(request, order_result={
            "ok": False, "msg": "Покупатель не найден — выберите из списка или введите телефон"})
    try:
        res = nalichie.add_order(phone_val, name, item, qty, done=bool(done), direction=direction)
        if res.get("ok"):
            ch = res.get("change") or {}
            order_result = {"ok": True, "buyer": name or phone, "item": ch.get("item", item),
                            "added": ch.get("added"), "sum": ch.get("sum"),
                            "done": bool(done), "direction": direction}
        else:
            order_result = {"ok": False, "msg": res.get("reason", "ошибка")}
    except Exception as e:
        order_result = {"ok": False, "msg": f"{type(e).__name__}: {e}"}
    return _render_nalichie(request, order_result=order_result, last_phone=phone)


@router.get("/admin/invoices", response_class=HTMLResponse)
def admin_invoices(request: Request):
    if not _require_admin(request):
        return RedirectResponse("/login", status_code=303)
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
