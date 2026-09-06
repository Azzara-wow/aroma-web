#!/usr/bin/env bash
# HTTPS через Let's Encrypt. Запускать ПОСЛЕ того, как домен указывает на сервер (DNS).
# Запуск:  bash deploy/https.sh aroma-web.ru
set -e
DOMAIN="${1:-aroma-web.ru}"
apt -y install certbot python3-certbot-nginx
certbot --nginx -d "$DOMAIN" -d "www.$DOMAIN" --non-interactive --agree-tos \
    -m eaburdenko@gmail.com --redirect
systemctl reload nginx
echo "Готово: https://$DOMAIN"
