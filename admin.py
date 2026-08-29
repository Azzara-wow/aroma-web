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
        }

    orders = flow.net_orders()  # phone -> {"name", "aromas": {аромат: мл}}
    details = []
    export_rows = []
    all_problems = []

    for phone, u in orders.items():
        buyer = u["name"] or phone
        d = {"buyer": buyer, "phone": phone, "positions": [], "total": 0, "problems": []}
        for aroma, vol in u["aromas"].items():
            meta = info.get(aroma.lower())
            if meta is None:
                prob = {"buyer": buyer, "aroma": aroma, "volume": int(vol),
                        "reason": "нет в Ассортименте или скрыт"}
                d["problems"].append(prob)
                all_problems.append(prob)
                continue
            calc = core.price_of_position(meta["category"], vol, meta["per_ml"])
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
            export_rows.append([buyer, meta["name"], int(vol), calc["per_ml"], calc["amount"]])

        if d["positions"] or d["problems"]:
            details.append(d)

    details.sort(key=lambda d: d["buyer"].lower())
    summary = [{"buyer": d["buyer"], "total": d["total"]} for d in details]
    grand_total = sum(d["total"] for d in details)

    return {
        "summary": summary,
        "grand_total": grand_total,
        "details": details,
        "export": export_rows,
        "problems": all_problems,
    }


# ---------- маршруты ----------

@router.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    if not _require_admin(request):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "aromas": _live_aromas(),
            "users_list": users.list_users(),
        },
    )


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

    # имя-снимок берём из листа Пользователи (если телефон зарегистрирован)
    known = users.get_user(phone)
    name = known["name"] if known else ""

    try:
        result = flow.add_order(phone, name, aroma, volume, direction)
        if result.get("ok"):
            result["buyer"] = name or result.get("phone", phone)
        else:
            result["msg"] = result.get("reason", "ошибка")
    except Exception as e:
        result = {"ok": False, "msg": f"{type(e).__name__}: {e}"}

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "aromas": _live_aromas(),
            "users_list": users.list_users(),
            "result": result,
            "last_phone": phone,
        },
    )


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
