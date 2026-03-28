# main.py — aroma_web.v2.10 (финальная рабочая версия)
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import pandas as pd
from urllib.parse import urlparse, parse_qs
import traceback

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# === НАСТРОЙКИ ===
SHEET_URL = "https://docs.google.com/spreadsheets/d/1LgWJ6W_3aWXTI0w9g_w1AjQKBTJeY1mND4j7ondRTE0/edit?gid=0#gid=0"

ORDER_TAGS = "#luzi03"
REORDER_TAGS = "#luzi03 #добор"

@app.get("/health")
@app.head("/health")
async def health(request: Request):
    return {"status": "ok"}

def make_csv_url(sheet_url: str) -> str:
    parsed = urlparse(sheet_url)
    path_parts = parsed.path.split("/")
    spreadsheet_id = path_parts[path_parts.index("d") + 1]
    query = parse_qs(parsed.query)
    gid = query.get("gid", ["0"])[0]
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv&gid={gid}"


def load_data(sheet_url: str) -> pd.DataFrame:
    return pd.read_csv(make_csv_url(sheet_url), engine="python")


def normalize_name(value: str) -> str:
    return str(value).strip().lower().replace("\n", " ").replace("\u00a0", " ").replace("  ", " ")

def prepare_dataframe(df: pd.DataFrame, user_name: str) -> pd.DataFrame:
    # === НОРМАЛИЗАЦИЯ ИМЁН СТОЛБЦОВ ===
    original_cols = list(df.columns)
    df.columns = [normalize_name(c) for c in df.columns]

    # Карта нормализованное → оригинальное
    col_map = dict(zip(df.columns, original_cols))

    # === ОСНОВНЫЕ СТОЛБЦЫ ===
    name_col = next((c for c in df.columns if "наименование" in c), df.columns[2])
    status_col = next((c for c in df.columns if "статус" in c), df.columns[1])
    category_col = next((c for c in df.columns if "категория" in c), df.columns[0])

    view_col = next((c for c in df.columns if "использование" in c or "пол" in c), None)
    collected_col = next((c for c in df.columns if "набрано" in c), None)
    target_col = next((c for c in df.columns if "набрать" in c), None)

    # Столбец пользователя
    user_col = normalize_name(user_name)
    user_col = user_col if user_col in df.columns else None

    # === ФИКСИРОВАННЫЕ ЦЕНОВЫЕ СТОЛБЦЫ ===
    price_cols = ["цена 50 мл", "цена 100 мл", "цена 500 мл"]

    # === ФОРМИРОВАНИЕ РЕЗУЛЬТАТА ===
    result = pd.DataFrame()
    result["aroma_name"] = df[name_col].fillna("").astype(str).str.strip()
    result["status"] = df[status_col].fillna("").astype(str).str.strip().str.lower()
    result["category_raw"] = df[category_col].fillna("").astype(str).str.strip().str.lower()

    # Категории
    cat_map = {
        "духи": "Духи", "отдушки": "Отдушки", "база": "База",
        "флаконы": "Флаконы", "разное": "Разное", "расходники": "Разное"
    }
    result["category"] = result["category_raw"].map(cat_map).fillna(
        result["category_raw"].apply(lambda x: x.capitalize() if x else "Разное")
    )

    # Пол / использование
    result["view"] = df[view_col].fillna("").astype(str).str.strip() if view_col else ""

    # Заказы пользователя
    if user_col:
        result["ordered_ml"] = pd.to_numeric(df[user_col], errors="coerce").fillna(0).astype(int)
    else:
        result["ordered_ml"] = 0

    # === ПАРСИНГ ЦЕН ===
    def get_prices(row):
        prices = []

        # Подписи для категорий
        if row["category"] == "Духи":
            labels = ["Цена 10 мл", "Цена 50 мл", "Цена 100 мл"]
        elif row["category"] == "Отдушки":
            labels = ["Цена 50 мл", "Цена 100 мл", "Цена 500 мл"]
        else:
            labels = ["Цена", "Цена", "Цена"]

        for i, col in enumerate(price_cols):
            if col not in df.columns:
                continue

            # ВАЖНО: читаем цену из df, а не из result
            raw = str(df.at[row.name, col]).replace(",", ".").strip()

            if raw == "" or raw.lower() in ["nan", "none"]:
                continue

            val = pd.to_numeric(raw, errors="coerce")
            if pd.isna(val) or val <= 0:
                continue

            prices.append({"label": labels[i], "value": float(val)})

        return prices

    result["prices"] = result.apply(get_prices, axis=1)
    result["price"] = result["prices"].apply(lambda p: p[0]["value"] if p else 0)

    # Набрано / осталось
    result["collected"] = pd.to_numeric(df[collected_col], errors="coerce").fillna(0).astype(int) if collected_col else 0
    result["remaining"] = pd.to_numeric(df[target_col], errors="coerce").fillna(0).astype(int) if target_col else 0

    # Очистка
    result = result[result["aroma_name"] != ""]
    header_names = ["духи", "отдушки", "база", "флаконы", "разное", "расходники", "добор"]
    result = result[~(
        result["aroma_name"].str.lower().isin(header_names) &
        (result["category_raw"] == "") &
        (result["status"] == "")
    )]

    return result

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user_name_raw = request.query_params.get("user", "").strip()
    mode = request.query_params.get("mode", "category")
    tab = request.query_params.get("tab", "Общее")

    try:
        df_raw = load_data(SHEET_URL)
        df_full = prepare_dataframe(df_raw, user_name_raw)
        df = df_full.copy()

        df = df[~df["status"].isin(["hide", "сервис"])]

        has_dobor = df_full["status"].str.contains("добор", na=False).any()
        active_tabs = ["Общее", "Духи", "Отдушки", "Флаконы", "База", "Разное"]
        active_tabs = [t for t in active_tabs if t == "Общее" or (df_full["category"] == t).any()]
        if has_dobor:
            active_tabs.append("Добор")

        if mode == "mine":
            df = df[df["ordered_ml"] > 0]
            current_tab = "Моё"
        else:
            if tab == "Общее":
                df = df[~df["status"].str.contains("добор", na=False)]
            elif tab == "Добор":
                df = df[df["status"].str.contains("добор", na=False)]
            else:
                df = df[(df["category"] == tab) & (~df["status"].str.contains("добор", na=False))]

            if tab not in active_tabs:
                tab = "Общее"
            current_tab = tab

        # Список пользователей для datalist
        exclude_words = ["наименование", "статус", "категория", "пол", "набрано", "набрать",
                         "осталось", "цена", "hide", "добор", "сервис", "итого", "всего", "сумма"]
        all_users = []
        for col in df_raw.columns:
            col_str = str(col).strip()
            if not col_str:
                continue
            col_norm = normalize_name(col_str)
            if any(word in col_norm for word in exclude_words):
                continue
            if len(col_str) > 2 and not col_str.replace(".", "").replace(",", "").isdigit():
                all_users.append(col_str)

        unique_users = sorted(set(all_users), key=str.lower)

        is_reorder = (df_full["ordered_ml"] > 0).any()
        order_tag = REORDER_TAGS if is_reorder else ORDER_TAGS

        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "aromas": df.to_dict(orient="records"),
                "user_name": user_name_raw,
                "mode": mode,
                "tab": current_tab,
                "order_tag": order_tag,
                "tabs": active_tabs,
                "all_users": unique_users,
            }
        )

    except Exception as e:
        traceback.print_exc()
        return HTMLResponse(content=f"<h2>Ошибка загрузки данных</h2><pre>{traceback.format_exc()}</pre>", status_code=500)

