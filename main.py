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

        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "aromas": visible,
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
    items: dict = {}


@app.post("/order")
def order(request: Request, payload: OrderIn):
    """
    Принять ДОБАВЛЕНИЯ от витрины и записать их в Поток (только прибавление).
    Тело: {"items": {"<аромат>": <добавить_мл>, ...}}. Личность — из куки.
    """
    user = auth.current_user(request)
    if not user:
        return JSONResponse({"ok": False, "reason": "not_authenticated"}, status_code=401)
    try:
        res = flow.add_batch(user["phone"], user["name"], payload.items or {})
    except Exception:
        traceback.print_exc()
        # Запись (POST) не повторяем автоматически — просим повторить вручную.
        return JSONResponse(
            {"ok": False, "reason": "Связь прервалась, попробуйте ещё раз"},
            status_code=503,
        )
    status = 200 if res.get("ok") else 400
    return JSONResponse(res, status_code=status)
