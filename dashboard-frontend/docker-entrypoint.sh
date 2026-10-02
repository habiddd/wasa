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

# HTTPS: si no hay certificado en /etc/nginx/certs (montado desde
# dashboard-frontend/certs), se genera uno autofirmado para que el puerto
# 443 arranque igual. El navegador avisará que no es de confianza; para uno
# real, deja fullchain.pem y privkey.pem en esa carpeta y reinicia.
cert_dir=/etc/nginx/certs
if [ ! -s "$cert_dir/fullchain.pem" ] || [ ! -s "$cert_dir/privkey.pem" ]; then
    mkdir -p "$cert_dir"
    openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
        -subj "/CN=lacs-dashboard" -addext "subjectAltName=DNS:localhost" \
        -keyout "$cert_dir/privkey.pem" -out "$cert_dir/fullchain.pem" 2>/dev/null
fi
