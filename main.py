# main.py — aroma_web.v3.1 (морда покупателя)
# Вся логика чтения файла вынесена в core.py. Здесь — только веб-слой витрины.

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import traceback

import os
import core
import admin

app = FastAPI()
app.include_router(admin.router)
templates = Jinja2Templates(directory="templates")

# Секретное слово для входа в организаторскую (из окружения, не в коде).
# Если не задано — дверь не показывается никогда.
ADMIN_NAME = os.environ.get("ADMIN_NAME", "").strip()

# Теги сообщения покупателя — специфика морды, админке не нужны.
ORDER_TAGS = "#luzi07"
REORDER_TAGS = "#luzi07 #добор"


@app.get("/health")
@app.head("/health")
async def health(request: Request):
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user_name_raw = request.query_params.get("user", "").strip()
    mode = request.query_params.get("mode", "category")
    tab = request.query_params.get("tab", "Общее")

    try:
        df_raw = core.load_data()
        all_rows, buyer_names = core.prepare_dataframe(df_raw, user_name_raw)

        visible = [x for x in all_rows if x["status"] not in ("hide", "сервис")]

        base_tabs = ["Общее", "Духи", "Отдушки", "База", "Разное", "Флаконы"]
        present = {x["category"] for x in all_rows}
        active_tabs = [t for t in base_tabs if t == "Общее" or t in present]

        has_dobor = any("добор" in x["status"] for x in all_rows)
        if has_dobor:
            active_tabs.append("Добор")

        if mode == "mine":
            shown = [x for x in visible if x["ordered_ml"] > 0]
            current_tab = "Моё"
        else:
            if tab == "Общее":
                shown = [x for x in visible if "добор" not in x["status"]]
            elif tab == "Добор":
                shown = [x for x in visible if "добор" in x["status"]]
            else:
                shown = [x for x in visible if x["category"] == tab and "добор" not in x["status"]]
            current_tab = tab if tab in active_tabs else "Общее"

        is_reorder = any(x["ordered_ml"] > 0 for x in all_rows)
        order_tag = REORDER_TAGS if is_reorder else ORDER_TAGS

        # Дверь в организаторскую: видна, только если введено секретное слово.
        show_admin_door = bool(ADMIN_NAME) and user_name_raw.strip().lower() == ADMIN_NAME.lower()

        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "aromas": shown,
                "user_name": user_name_raw,
                "mode": mode,
                "tab": current_tab,
                "order_tag": order_tag,
                "tabs": active_tabs,
                "all_users": sorted(set(buyer_names), key=str.lower),
                "show_admin_door": show_admin_door,
            },
        )

    except Exception:
        traceback.print_exc()
        return HTMLResponse(
            content=f"<h2>Ошибка загрузки данных</h2><pre>{traceback.format_exc()}</pre>",
            status_code=500,
        )