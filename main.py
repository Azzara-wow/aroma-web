# main.py — aroma_web.v3.3 (морда покупателя, Шаг 3)
# Личность — из куки (auth.current_user), не из URL. Данные заказов — из Потока
# (flow), а не из матрицы. Заказ уходит прямо в Поток (POST /order), без ТГ.

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import traceback

import core
import admin
import auth
import users
import flow
import nalichie
import catalog
import notify

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

        base_tabs = ["Общее", "Духи", "Отдушки", "База", "Разное", "Флаконы"]
        present = {x["category"] for x in all_rows}
        active_tabs = [t for t in base_tabs if t == "Общее" or t in present]

        has_dobor = any(x["is_dobor"] for x in all_rows)
        if has_dobor:
            active_tabs.append("Добор")

        # Наличие (вторая книга). Если недоступна — витрина закупки всё равно грузится.
        nalichie_items, nal_mine_sum = [], 0
        try:
            nalichie_items, nal_mine_sum = nalichie.view(user["phone"] if is_auth else None)
        except Exception:
            traceback.print_exc()
        if nalichie_items:
            active_tabs.append("Наличие")

        # Полный каталог «Ассортимент» (view-only). Не критичен для витрины.
        catalog_items = []
        try:
            catalog_items = catalog.catalog()
        except Exception:
            traceback.print_exc()
        if catalog_items:
            active_tabs.append("Ассортимент")

        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "aromas": visible,
                "nalichie": nalichie_items,
                "nal_mine_sum": nal_mine_sum,
                "catalog_items": catalog_items,
                "user_name": user["name"] if is_auth else "",
                "is_auth": is_auth,
                "tab": tab,
                "tabs": active_tabs,
                "is_admin": users.is_admin(user) if is_auth else False,
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
        lines = [f"• {c['aroma']} — +{c['added']} мл (стало {c['to']})" for c in res_z["changes"]]
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
