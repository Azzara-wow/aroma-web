# info.py — aroma_web / вкладка "Информация" (полезные ссылки СП).
#
# Читает лист "Информация" из книги закупки (по названию, без учёта регистра).
# Столбцы (по именам заголовков, с синонимами): Заголовок | Ссылка | Описание.
# Организатор просто добавляет строки. Если листа нет — вкладка не показывается.

import time

import core
import sheets

H_TITLE = ["заголовок", "название", "имя", "name"]
H_URL = ["ссылка", "url", "адрес", "link"]
H_DESC = ["описание", "комментарий", "примечание", "note"]

_TTL = 120
_cache = {"items": None, "ts": 0.0}


def _ws():
    """Лист 'Информация' в книге закупки (без учёта регистра) или None."""
    for w in sheets.open_book().worksheets():
        if core.norm(w.title).lower() == "информация":
            return w
    return None


def _safe_url(url: str) -> str:
    """Разрешаем только http(s); если схемы нет — добавляем https://. Иначе пусто."""
    u = url.strip()
    if u == "":
        return ""
    low = u.lower()
    if low.startswith("http://") or low.startswith("https://"):
        return u
    if low.startswith("javascript:") or low.startswith("data:"):
        return ""
    return "https://" + u


def items():
    """[{title, url, desc}] из листа Информация. Кэш 2 мин, терпим к ошибкам."""
    now = time.time()
    if _cache["items"] is not None and now - _cache["ts"] < _TTL:
        return _cache["items"]

    out = []
    try:
        ws = _ws()
        if ws is not None:
            values = ws.get_all_values()
            header = values[0] if values else []
            low = [core.norm(h).lower() for h in header]

            def col(aliases):
                for a in aliases:
                    if a in low:
                        return low.index(a)
                return None

            ct, cu, cd = col(H_TITLE), col(H_URL), col(H_DESC)
            for r in range(1, len(values)):
                row = values[r]
                title = core.norm(row[ct]) if ct is not None and ct < len(row) else ""
                url = _safe_url(core.norm(row[cu]) if cu is not None and cu < len(row) else "")
                desc = core.norm(row[cd]) if cd is not None and cd < len(row) else ""
                if title == "" and url == "":
                    continue
                out.append({"title": title or url, "url": url, "desc": desc})
    except Exception:
        out = []

    _cache["items"] = out
    _cache["ts"] = now
    return out
