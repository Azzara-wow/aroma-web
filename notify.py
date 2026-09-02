# notify.py — aroma_web / уведомления организатору в Telegram.
#
# Зачем: когда покупатель делает заказ (наличие/закупка), организатору падает
# пуш в Telegram. Бесплатно, мгновенно, без внешних зависимостей (стандартный
# urllib). Отправка в фоне — не тормозит и не роняет сам заказ.
#
# Настройка (переменные окружения на Render):
#   TELEGRAM_BOT_TOKEN — токен бота от @BotFather
#   TELEGRAM_CHAT_ID   — id чата организатора (куда слать)
# Если не заданы — уведомления просто выключены (всё остальное работает).

import os
import json
import threading
import urllib.request

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()


def enabled() -> bool:
    return bool(TG_TOKEN and TG_CHAT)


def _post(text: str) -> bool:
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        data = json.dumps({
            "chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True,
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except Exception:
        return False  # уведомление не должно ронять заказ


def send(text: str):
    """Отправить уведомление (в фоне). Тихо ничего не делает, если бот не настроен."""
    if not enabled():
        return
    threading.Thread(target=_post, args=(text,), daemon=True).start()


def test() -> dict:
    """Синхронно отправить тестовое сообщение — для проверки настройки."""
    if not TG_TOKEN:
        return {"ok": False, "reason": "не задан TELEGRAM_BOT_TOKEN"}
    if not TG_CHAT:
        return {"ok": False, "reason": "не задан TELEGRAM_CHAT_ID"}
    return {"ok": _post("✅ Проверка: уведомления LUZI работают.")}


def recent_chats() -> dict:
    """Чаты, писавшие боту (getUpdates) — чтобы узнать свой chat_id для настройки."""
    if not TG_TOKEN:
        return {"error": "не задан TELEGRAM_BOT_TOKEN"}
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates"
        raw = urllib.request.urlopen(url, timeout=10).read()
        data = json.loads(raw)
        chats = {}
        for u in data.get("result", []):
            msg = u.get("message") or u.get("edited_message") or {}
            ch = msg.get("chat") or {}
            cid = ch.get("id")
            if cid is not None:
                name = " ".join(x for x in [ch.get("first_name"), ch.get("last_name"),
                                            ch.get("title")] if x) or ch.get("username", "")
                chats[cid] = name
        return {"chats": [{"id": k, "name": v} for k, v in chats.items()],
                "current": TG_CHAT}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
