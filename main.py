# main.py — aroma_web.v3.3 (морда покупателя, Шаг 3)
# Личность — из куки (auth.current_user), не из URL. Данные заказов — из Потока
# (flow), а не из матрицы. Заказ уходит прямо в Поток (POST /order), без ТГ.

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import traceback

from yandex_delivery import YandexDeliveryClient
from yandex_delivery.errors import YandexDeliveryError

import core
import admin
import auth
import users
import flow
import nalichie
import catalog
import notify
import info

app = FastAPI()
app.include_router(admin.router)
app.include_router(auth.router)
app.mount("/static", StaticFiles(directory="static"), name="static")  # логотип и пр.
templates = Jinja2Templates(directory="templates")


# Обработчики намеренно СИНХРОННЫЕ (def, не async): внутри — блокирующие вызовы
# gspread. FastAPI гоняет sync-обработчики в пуле потоков, поэтому медленный момент
# Google у одного покупателя не подвешивает остальных (важно на одном воркере Render).


@app.get("/health")
@app.head("/health")
def health(request: Request):
    return {"status": "ok"}


# Service worker отдаём из КОРНЯ (scope "/"), чтобы он контролировал весь сайт —
# иначе установка PWA не проходит. Ничего не кэшируем (данные динамические):
# обработчик fetch есть только для критерия «устанавливаемости».
_SW_JS = (
    "self.addEventListener('install', e => self.skipWaiting());\n"
    "self.addEventListener('activate', e => self.clients.claim());\n"
    "self.addEventListener('fetch', e => {});\n"
)


@app.get("/sw.js")
def service_worker():
    return Response(content=_SW_JS, media_type="application/javascript")


@app.head("/")
def index_head():
    # Лёгкий ответ на HEAD-пробу (иначе GET-only корень отдаёт 405).
    return HTMLResponse("")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    # Авторизация НЕ обязательна: ассортимент и цены видит любой (пусть конкуренты
    # смотрят). Вход нужен только для заказа и вкладки «Моё».
    user = auth.current_user(request)
    is_auth = bool(user)

    tab = request.query_params.get("tab", "Общее")

    try:
        df_raw = core.load_data()
        all_rows, _ = core.prepare_dataframe(df_raw)  # покупателей матрицы больше нет

        # Данные заказов из Потока — за ОДНО чтение (набрано всеми + моё).
        collected, mine = flow.board(user["phone"] if is_auth else None)
        for x in all_rows:
            x["collected"] = collected.get(x["aroma_name"], 0)
            x["ordered_ml"] = mine.get(x["aroma_name"], 0)
            x["is_dobor"] = "добор" in x["status"]

        # Отдаём ВЕСЬ видимый список; вкладки и «Моё» фильтрует браузер (быстро).
        visible = [x for x in all_rows if x["status"] not in ("hide", "сервис")]

        # Вкладки ЗАКУПКИ (под строкой поиска).
        base_tabs = ["Общее", "Духи", "Отдушки", "База", "Разное", "Флаконы"]
        present = {x["category"] for x in all_rows}
        cat_tabs = [t for t in base_tabs if t == "Общее" or t in present]
        if any(x["is_new"] for x in all_rows):
            cat_tabs.append("Новинки")
        if any(x["is_dobor"] for x in all_rows):
            cat_tabs.append("Добор")

        # Наличие (вторая книга). Если недоступна — витрина закупки всё равно грузится.
        nalichie_items, nal_mine_sum = [], 0
        try:
            nalichie_items, nal_mine_sum = nalichie.view(user["phone"] if is_auth else None)
        except Exception:
            traceback.print_exc()

        # Полный каталог «Ассортимент» (view-only). Не критичен.
        catalog_items = []
        try:
            catalog_items = catalog.catalog()
        except Exception:
            traceback.print_exc()

        # Информация (лист «Информация» в книге закупки). Нет листа — нет вкладки.
        info_items = []
        try:
            info_items = info.items()
        except Exception:
            traceback.print_exc()

        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "aromas": visible,
                "nalichie": nalichie_items,
                "nal_mine_sum": nal_mine_sum,
                "catalog_items": catalog_items,
                "info_items": info_items,
                "user_name": user["name"] if is_auth else "",
                "is_auth": is_auth,
                "tab": tab,
                "cat_tabs": cat_tabs,
                "has_nalichie": bool(nalichie_items),
                "has_catalog": bool(catalog_items),
                "has_info": bool(info_items),
                "is_admin": users.is_admin(user) if is_auth else False,
                # данные доставки: плашка горит, пока не заполнено (гостю не показываем)
                "delivery_complete": (user.get("delivery_complete", False) if is_auth else True),
                "deliv": {
                    "last_name": user.get("last_name", ""),
                    "first_name": user.get("first_name", ""),
                    "patronymic": user.get("patronymic", ""),
                    "city": user.get("city", ""),
                    "pvz_address": user.get("pvz_address", ""),
                    "pvz_id": user.get("pvz_id", ""),
                } if is_auth else {},
            },
        )

    except Exception:
        traceback.print_exc()  # подробности — в логи сервера, не пользователю
        return HTMLResponse(
            content=(
                "<div style='font-family:system-ui;background:#0e1117;color:#fff;"
                "padding:24px;text-align:center;'>"
                "<h2>Не удалось загрузить данные</h2>"
                "<p style='opacity:.8'>Связь с таблицей на секунду прервалась. "
                "Обновите страницу — обычно со второго раза открывается.</p>"
                "<p><a href='/' style='color:#8ab4f7;'>Обновить</a></p></div>"
            ),
            status_code=503,
        )


@app.get("/cities")
def cities_search(request: Request, q: str = ""):
    """JSON-автоподбор города Яндекса (location/detect) — [{geo_id, address}]."""
    q = (q or "").strip()
    if len(q) < 2:
        return JSONResponse({"ok": False, "error": "Введите город"})
    try:
        c = YandexDeliveryClient()
        variants = c.detect_location(q)
        data = [{"geo_id": v.get("geo_id"), "address": v.get("address", "")}
                for v in variants if v.get("geo_id")]
        return JSONResponse({"ok": True, "env": c.env, "cities": data})
    except YandexDeliveryError as e:
        return JSONResponse({"ok": False, "error": str(e)})
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": str(e)})


@app.get("/pvz")
def pvz_search(request: Request, city: str = "", geo_id: int = 0):
    """JSON-поиск ПВЗ Яндекса по городу или geo_id — для формы доставки."""
    try:
        c = YandexDeliveryClient()  # окружение/токен из env (по умолчанию тест)
        gid = geo_id or (c.geo_id(city.strip()) if city.strip() else 0)
        if not gid:
            return JSONResponse({"ok": False, "error": "Укажите город"})
        points = c.list_pickup_points(geo_id=gid)
        data = [{"id": p.id, "name": p.name, "address": p.full_address} for p in points[:300]]
        return JSONResponse({"ok": True, "env": c.env, "count": len(points), "points": data})
    except YandexDeliveryError as e:
        return JSONResponse({"ok": False, "error": str(e)})
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/delivery")
def save_delivery(
    request: Request,
    last_name: str = Form(""),
    first_name: str = Form(""),
    patronymic: str = Form(""),
    city: str = Form(""),
    pvz_address: str = Form(""),
    pvz_id: str = Form(""),
):
    """Сохранить данные доставки покупателя в лист «Покупатели» (личность из куки)."""
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    try:
        users.set_delivery(user["phone"], last_name, first_name, patronymic,
                           city, pvz_address, pvz_id)
    except Exception:
        traceback.print_exc()
    return RedirectResponse("/?deliv=1", status_code=303)


class OrderIn(BaseModel):
    zakupka: dict = {}    # {аромат: добавить_мл} -> поток закупки
    nalichie: dict = {}   # {товар: добавить}     -> поток наличия


@app.post("/order")
def order(request: Request, payload: OrderIn):
    """
    Принять ДОБАВЛЕНИЯ от витрины (только прибавление) и разложить в разные листы:
    закупку — в поток закупки, наличие — в поток наличия. Личность — из куки.
    """
    user = auth.current_user(request)
    if not user:
        return JSONResponse({"ok": False, "reason": "not_authenticated"}, status_code=401)
    try:
        res_z = flow.add_batch(user["phone"], user["name"], payload.zakupka) \
            if payload.zakupka else {"ok": True, "changes": []}
        res_n = nalichie.add_batch(user["phone"], user["name"], payload.nalichie) \
            if payload.nalichie else {"ok": True, "changes": [], "rejected": []}
    except Exception:
        traceback.print_exc()
        return JSONResponse(
            {"ok": False, "reason": "Связь прервалась, попробуйте ещё раз"},
            status_code=503,
        )
    ok = res_z.get("ok") and res_n.get("ok")

    # Уведомление организатору о заказе ЗАКУПКИ (в Telegram, в фоне).
    # У закупки нет простой суммы (цена по ступеням), поэтому объём и новый итог.
    if res_z.get("changes"):
        lines = [f"• {c['aroma']} — +{c['added']} (стало {c['to']})" for c in res_z["changes"]]
        notify.send("🛍 Закупка — новый заказ\n"
                    f"{user['name']} ({user['phone']})\n" + "\n".join(lines))

    # Уведомление организатору о заказе НАЛИЧИЯ (в Telegram, в фоне).
    if res_n.get("changes"):
        lines = [f"• {c['item']} — {c['added']} ({c['sum']} ₽)" for c in res_n["changes"]]
        total = sum(c.get("sum", 0) for c in res_n["changes"])
        notify.send("🛒 Наличие — новый заказ\n"
                    f"{user['name']} ({user['phone']})\n"
                    + "\n".join(lines)
                    + f"\nИтого: {total} ₽")

    return JSONResponse({"ok": ok, "zakupka": res_z, "nalichie": res_n},
                        status_code=200 if ok else 400)
