#!/usr/bin/env bash
# Вписать Telegram-секреты в /etc/aroma-web.env и перезапустить службу.
# Запуск:  bash deploy/telegram.sh <ТОКЕН> <CHAT_ID>
set -e
TOKEN="$1"
CHAT="$2"
if [ -z "$TOKEN" ] || [ -z "$CHAT" ]; then
    echo "Использование: bash deploy/telegram.sh ТОКЕН CHAT_ID"
    exit 1
fi
ENV=/etc/aroma-web.env
touch "$ENV"
# убрать прежние TELEGRAM-строки, сохранив SESSION_SECRET и прочее
grep -v -e '^TELEGRAM_BOT_TOKEN=' -e '^TELEGRAM_CHAT_ID=' "$ENV" > "$ENV.tmp" || true
mv "$ENV.tmp" "$ENV"
printf 'TELEGRAM_BOT_TOKEN=%s\nTELEGRAM_CHAT_ID=%s\n' "$TOKEN" "$CHAT" >> "$ENV"
chmod 600 "$ENV"
systemctl restart aroma
echo "Telegram записан, служба перезапущена."
echo "Проверь: Организаторская → 🔔 Уведомления → Отправить тест"
