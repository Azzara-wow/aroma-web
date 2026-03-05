# aroma_web.v2.3 — динамические табы + без Плана заказа
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import pandas as pd
from urllib.parse import urlparse, parse_qs

app = FastAPI()
templates = Jinja2Templates(directory="templates")

from fastapi import Request

@app.get("/health")
@app.head("/health")
async def health(request: Request):
    return {"status": "ok"}

# === НАСТРОЙКИ ===
SHEET_URL = "https://docs.google.com/spreadsheets/d/1QHfj-JTCVs7xvnUj0bQZu6HNHuuDflWgSt6mC9ngMmU/edit?gid=0#gid=0"

ORDER_TAGS = "#парфюм3"
REORDER_TAGS = "#парфюм3 #добор"


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
    return (
        str(value).strip()
        .lower()
        .replace("\n", " ")
        .replace("\u00a0", " ")
        .replace("  ", " ")
    )


def prepare_dataframe(df: pd.DataFrame, user_name: str) -> pd.DataFrame:
    norm_cols = {normalize_name(col): col for col in df.columns}

    name_col = next((col for k, col in norm_cols.items() if "наименование" in k or "название" in k), df.columns[2])
    status_col = next((col for k, col in norm_cols.items() if "статус" in k), df.columns[1])
    category_col = next((col for k, col in norm_cols.items() if "категория" in k), df.columns[0])

    view_col = next((col for k, col in norm_cols.items() if "пол" in k.lower()), None)
    collected_col = next((col for k, col in norm_cols.items() if "набрано" in k), None)
    target_col = next((col for k, col in norm_cols.items() if "набрать" in k or "осталось" in k), None)

    user_col = norm_cols.get(normalize_name(user_name)) if user_name else None

    price_pairs = [(k, col) for k, col in norm_cols.items() if "цена" in k]

    result = pd.DataFrame()
    result["aroma_name"] = df[name_col].fillna("").astype(str).str.strip()
    result["status"] = df[status_col].fillna("").astype(str).str.strip().str.lower()
    result["category_raw"] = df[category_col].fillna("").astype(str).str.strip().str.lower()

    cat_map = {"духи": "Духи", "отдушки": "Отдушки", "база": "База", "флаконы": "Флаконы", "разное": "Разное", "расходники": "Разное"}
    result["category"] = result["category_raw"].map(cat_map).fillna(
        result["category_raw"].apply(lambda x: x.capitalize() if x else "Разное")
    )

    if view_col:
        result["view"] = df[view_col].fillna("").astype(str).str.strip()
    else:
        result["view"] = ""

    if user_col:
        result["ordered_ml"] = pd.to_numeric(df[user_col], errors="coerce").fillna(0).astype(int)
    else:
        result["ordered_ml"] = 0

    def get_prices(row):
        prices = []
        for _, col in price_pairs:
            val_str = str(row[col]).replace(",", ".").strip()
            val = pd.to_numeric(val_str, errors="coerce")
            if pd.notna(val) and val > 0:
                label = col.replace("\n", " ").strip().replace("цена", "").strip() or "Цена"
                prices.append({"label": label, "value": float(val)})
        return prices

    result["prices"] = df.apply(get_prices, axis=1)
    result["price"] = result["prices"].apply(lambda x: x[0]["value"] if x else 0)

    result["collected"] = pd.to_numeric(df[collected_col], errors="coerce").fillna(0).astype(int) if collected_col else 0
    result["remaining"] = pd.to_numeric(df[target_col], errors="coerce").fillna(0).astype(int) if target_col else 0

    # очистка
    result = result[result["aroma_name"] != ""]
    header_names = ["духи", "отдушки", "база", "флаконы", "разное", "расходники", "добор"]
    result = result[~(
        result["aroma_name"].str.lower().isin(header_names) &
        (result["category_raw"] == "") &
        (result["status"] == "")
    )]

    print(f"DEBUG: Загружено строк всего: {len(result)} | Категории: {result['category'].value_counts().to_dict()}")
    return result


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    user_name_raw = request.query_params.get("user", "").strip()
    user_name = normalize_name(user_name_raw)
    mode = request.query_params.get("mode", "category")
    tab = request.query_params.get("tab", "Духи")

    df_raw = load_data(SHEET_URL)
    df_full = prepare_dataframe(df_raw, user_name)
    df = df_full.copy()

    # скрываем hide
    df = df[~df["status"].str.contains("hide", na=False)]

    # режим "Моё"
    if mode == "mine":
        df = df[df["ordered_ml"] > 0]
        active_tab = None  # для моего списка таба нет
    else:
        if tab == "Добор":
            df = df[df["status"].str.contains("добор", na=False)]
        else:
            df = df[(df["category"] == tab) & (~df["status"].str.contains("добор", na=False))]

        # считаем реальные категории (только те, где после фильтров есть строки)
        visible_categories = df_full[
            (~df_full["status"].str.contains("hide", na=False)) &
            (~df_full["status"].str.contains("добор", na=False) | (df_full["category"] != tab))
        ]["category"].unique()

        # оставляем только те, что есть в списке + Добор если есть доборные строки
        possible_tabs = ["Духи", "Отдушки", "Флаконы", "База", "Разное"]
        active_tabs = [t for t in possible_tabs if t in visible_categories]

        # Добор отдельно — проверяем наличие строк с добор
        has_dobor = (df_full["status"].str.contains("добор", na=False)).any()
        if has_dobor:
            active_tabs.append("Добор")

        # если текущий tab не в активных — переключаем на первый
        if tab not in active_tabs and active_tabs:
            tab = active_tabs[0]

    print(f"DEBUG: Показываем в режиме {mode} / таб {tab} → {len(df)} ароматов")

    is_reorder = (df_full["ordered_ml"] > 0).any()
    order_tag = REORDER_TAGS if is_reorder else ORDER_TAGS

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "aromas": df.to_dict(orient="records"),
            "user_name": user_name_raw,
            "mode": mode,
            "tab": tab,
            "order_tag": order_tag,
            "tabs": active_tabs if 'active_tabs' in locals() else ["Духи"],  # fallback
        }
    )