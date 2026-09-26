# sync_api.py — aroma_web / закрытый канал для дашборда (aroma-manager).
#
#   GET /api/sync/zakupka  — состав закупки (свёртка Потока × цены Ассортимента,
#                            то же, что «Файл для дашборда» на странице счетов)
#
# Ключ доступа: заголовок X-Sync-Token. Если в окружении задан SYNC_TOKEN — берём его,
# иначе выводим из закрытого ключа общего сервисного аккаунта Google (он лежит у обоих
# приложений), поэтому отдельной настройки на сервере не требуется.

import hashlib
import hmac
import json
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import admin
import sheets

router = APIRouter()


def sync_token():
    env = os.environ.get("SYNC_TOKEN", "").strip()
    if env:
        return env
    with open(sheets._find_key_path(), encoding="utf-8") as f:
        sa = json.load(f)
    return hashlib.sha256(("aroma-sync:" + sa["private_key"]).encode("utf-8")).hexdigest()


def _authorized(request: Request):
    got = request.headers.get("X-Sync-Token", "")
    try:
        return bool(got) and hmac.compare_digest(got, sync_token())
    except Exception:
        return False


@router.get("/api/sync/zakupka")
def sync_zakupka(request: Request):
    if not _authorized(request):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    try:
        data = admin.build_invoices()
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=500)
    rows = []
    for d in data["details"]:
        for p in d["positions"]:
            rows.append({
                "phone": d["phone"],
                "name": d["buyer"],
                "aroma": p["aroma"],
                "volume": p["volume"],
                "per_ml": p["per_ml"],
                "amount": p["amount"],
                "piece": bool(p.get("piece")),
            })
    return {"ok": True, "rows": rows, "problems": data["problems"]}


# ======================================================================
#  Покупатели и счета (вместо листа «Покупатели»)
#
#   GET  /api/sync/buyers             — все покупатели с текущим счётом (без хешей кодов)
#   POST /api/sync/buyers/save        — {phone, create?, fields{...}} завести/поправить
#   POST /api/sync/buyers/reset-code  — {phone} сбросить код входа
#   POST /api/sync/buyers/delete      — {phone}
#   POST /api/sync/bills              — {updates{phone:{link,amount,delivery,paid,payto,tracking}},
#                                        clear_others?} счета закупки
# ======================================================================

import buyers_db
import users

_BILL_ALIASES = {"link": "pay_link", "amount": "pay_amount", "delivery": "pay_delivery",
                 "paid": "paid", "payto": "pay_to", "tracking": "tracking_url"}
# что дашборд может править в профиле (код входа — только сбросом, роль — отдельно)
_EDITABLE = ("name", "address", "note", "role", "last_name", "first_name", "patronymic",
             "city", "pvz_address", "pvz_id", "carrier", "email")


def _forbidden():
    return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)


def _public(rec):
    out = {k: v for k, v in rec.items() if k != "code_hash"}
    out["has_code"] = bool(rec.get("code_hash"))
    out["paid"] = bool(rec.get("paid"))
    return out


@router.get("/api/sync/buyers")
def sync_buyers(request: Request):
    if not _authorized(request):
        return _forbidden()
    return {"ok": True, "buyers": [_public(r) for r in buyers_db.all_buyers()]}


@router.post("/api/sync/buyers/save")
async def sync_buyer_save(request: Request):
    if not _authorized(request):
        return _forbidden()
    body = await request.json()
    phone = users.normalize_phone(body.get("phone", ""))
    if not users.valid_phone(phone):
        return {"ok": False, "error": "Телефон в формате 7XXXXXXXXXX"}
    fields = {k: v for k, v in (body.get("fields") or {}).items() if k in _EDITABLE}
    if body.get("create"):
        if not (fields.get("name") or "").strip():
            return {"ok": False, "error": "Укажите имя"}
        fields.setdefault("role", users.ROLE_BUYER)
        if not buyers_db.insert(phone, **fields):
            return {"ok": False, "error": "Этот телефон уже есть"}
    elif not buyers_db.update(phone, **fields):
        return {"ok": False, "error": "not_found"}
    return {"ok": True, "buyer": _public(buyers_db.get(phone))}


@router.post("/api/sync/buyers/reset-code")
async def sync_buyer_reset(request: Request):
    if not _authorized(request):
        return _forbidden()
    res = users.reset_code((await request.json()).get("phone", ""))
    return res if res["ok"] else {"ok": False, "error": res["reason"]}


@router.post("/api/sync/buyers/delete")
async def sync_buyer_delete(request: Request):
    if not _authorized(request):
        return _forbidden()
    phone = users.normalize_phone((await request.json()).get("phone", ""))
    return {"ok": buyers_db.delete(phone)}


@router.post("/api/sync/bills")
async def sync_bills(request: Request):
    if not _authorized(request):
        return _forbidden()
    body = await request.json()
    ups = {}
    for ph, f in (body.get("updates") or {}).items():
        ups[users.normalize_phone(ph)] = {_BILL_ALIASES[k]: v for k, v in (f or {}).items()
                                          if k in _BILL_ALIASES}
    res = buyers_db.set_bills(ups, clear_others=bool(body.get("clear_others")))
    return {"ok": True, **res}
