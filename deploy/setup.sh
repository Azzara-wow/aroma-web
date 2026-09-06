#!/usr/bin/env bash
# Разовая настройка сервера: env + автозапуск (systemd) + nginx (http).
# Запуск:  bash deploy/setup.sh aroma-web.ru
set -e

DOMAIN="${1:-aroma-web.ru}"
APP=/opt/aroma-web

echo "== 1. Файл секретов =="
if [ ! -f /etc/aroma-web.env ]; then
    SECRET=$(openssl rand -hex 32)
    cat > /etc/aroma-web.env <<EOF
SESSION_SECRET=$SECRET
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
EOF
    chmod 600 /etc/aroma-web.env
    echo "   создан, SESSION_SECRET сгенерирован (Telegram впишем позже)"
else
    echo "   уже есть — не трогаю"
fi

echo "== 2. Автозапуск (systemd) =="
cp "$APP/deploy/aroma.service" /etc/systemd/system/aroma.service
systemctl daemon-reload
systemctl enable aroma >/dev/null 2>&1 || true
systemctl restart aroma
echo "   служба перезапущена"

echo "== 3. nginx =="
sed "s/DOMAIN_PLACEHOLDER/$DOMAIN/g" "$APP/deploy/nginx.conf" > /etc/nginx/sites-available/aroma
ln -sf /etc/nginx/sites-available/aroma /etc/nginx/sites-enabled/aroma
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
echo "   nginx настроен на домен $DOMAIN"

echo "== Проверка =="
if [ -f "$APP/service_account.json" ]; then
    echo "   service_account.json: есть"
else
    echo "   service_account.json: НЕТ — приложение не сможет читать таблицы!"
fi
sleep 2
echo -n "   /health -> "
curl -s http://127.0.0.1:8001/health || echo "(приложение не ответило: journalctl -u aroma -n 30)"
echo
echo "Готово. Открой http://$DOMAIN (после того как DNS укажет на сервер)."
echo "HTTPS включим отдельно: bash deploy/https.sh $DOMAIN"
