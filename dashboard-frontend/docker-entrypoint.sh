#!/bin/sh
# Genera /etc/nginx/.htpasswd a partir de DASHBOARD_USER/DASHBOARD_PASSWORD
# antes de que arranque Nginx (ver /docker-entrypoint.d/ en la imagen oficial).
# Usamos openssl en vez de apache2-utils/htpasswd para no meter ese paquete
# extra en una imagen que solo sirve estáticos.
set -eu

: "${DASHBOARD_USER:?DASHBOARD_USER es obligatorio}"
: "${DASHBOARD_PASSWORD:?DASHBOARD_PASSWORD es obligatorio}"

hash=$(openssl passwd -apr1 "$DASHBOARD_PASSWORD")
printf '%s:%s\n' "$DASHBOARD_USER" "$hash" > /etc/nginx/.htpasswd
