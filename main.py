#aroma_web.v1.2 release
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import pandas as pd
from urllib.parse import urlparse, parse_qs

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# === НАСТРОЙКИ ===
SHEET_URL = "https://docs.google.com/spreadsheets/d/1_f7IZpy7AfjO2gw_1DTwjBGq5DO51-sqhlQmgk9fon8/edit?gid=0#gid=0"

# Тексты для сообщений в Telegram
ORDER_TAGS = "#парфюм2"
REORDER_TAGS = "#парфюм2 #добор"


def make_csv_url(sheet_url: str) -> str:
    parsed = urlparse(sheet_url)
    path_parts = parsed.path.split("/")
    spreadsheet_id = path_parts[path_parts.index("d") + 1]
    query = parse_qs(parsed.query)
    gid = query.get("gid", ["0"])[0]

    return (
        f"https://docs.google.com/spreadsheets/d/"
        f"{spreadsheet_id}/export?format=csv&gid={gid}"
    )


def load_data(sheet_url: str) -> pd.DataFrame:
    csv_url = make_csv_url(sheet_url)
    return pd.read_csv(csv_url, engine="python")


def extract_first_valid_number(row: pd.Series) -> float | None:
    for value in row:
        try:
            num = float(str(value).replace(",", "."))
            if num > 0:
                return num
        except (ValueError, TypeError):
            continue
    return None
def normalize_name(value: str) -> str:
    return (
        value.strip()
        .lower()
        .replace("\u00a0", " ")
        .replace("  ", " ")
    )


def prepare_dataframe(df: pd.DataFrame, user_name: str) -> pd.DataFrame:
    # колонка с названием
    name_column = None
    for col in df.columns:
        if "название" in col.lower():
            name_column = col
            break

    if name_column is None:
        raise ValueError("Не удалось найти колонку с названием аромата")

    normalized_columns = {
        normalize_name(col): col for col in df.columns
    }

    result = pd.DataFrame()
    result["aroma_name"] = df[name_column]

    # ordered_ml по имени
    if user_name and user_name in normalized_columns:
        user_col = normalized_columns[user_name]
        result["ordered_ml"] = (
            pd.to_numeric(df[user_col], errors="coerce")
            .fillna(0)
            .astype(int)
        )
    else:
        result["ordered_ml"] = 0

    # цена
    # цена
    price_series = df.apply(extract_first_valid_number, axis=1)

    price_series = pd.to_numeric(price_series, errors="coerce")

    result["price"] = price_series.fillna(0)

    result["price"] = (
        result["price"]
        .astype(str)
        .str.replace(r"[^\d.,]", "", regex=True)
        .str.replace(",", ".", regex=False)
    )
    result["price"] = pd.to_numeric(result["price"], errors="coerce").fillna(0)

    # данные строго из файла организатора
    # данные строго из файла организатора

    total_col = df["Набрано"] if "Набрано" in df.columns else pd.Series([0] * len(df))
    result["total_collected"] = (
        pd.to_numeric(total_col, errors="coerce")
        .fillna(0)
        .astype(int)
    )

    remaining_col = (
        df["Осталось набрать"]
        if "Осталось набрать" in df.columns
        else pd.Series([0] * len(df))
    )
    result["remaining_ml"] = (
        pd.to_numeric(remaining_col, errors="coerce")
        .fillna(0)
        .astype(int)
    )

    return result




@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    mode = request.query_params.get("mode", "all")
    user_name_raw = request.query_params.get("user", "")
    user_name = normalize_name(user_name_raw)

    # загружаем данные
    df_raw = load_data(SHEET_URL)
    df_full = prepare_dataframe(df_raw, user_name)

    # df — это то, что показываем
    df = df_full.copy()

    #режим "План заказа"
    if mode == "plan":
        df = df.iloc[0:0]  # пустой DataFrame

    # режим "Моё"
    elif mode == "mine":
        df = df[df["ordered_ml"] > 0]

    # 🔴 ВАЖНО: хештег считаем по ПОЛНОМУ списку
    is_reorder = (df_full["ordered_ml"] > 0).any()
    order_tag = REORDER_TAGS if is_reorder else ORDER_TAGS
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "aromas": df.to_dict(orient="records"),
            "user_name": user_name_raw,
            "mode": mode,
            "order_tag": order_tag
        }
    )

