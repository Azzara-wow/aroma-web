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
