# Build de la SPA (React/shadcn) y luego servida como estáticos por Nginx --
# contenedor propio, independiente de flask-app (que solo expone la API que
# este frontend consume en /dashboard/api).
FROM node:20-slim AS build

WORKDIR /frontend

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:1.27-alpine

# openssl: usado por docker-entrypoint.sh para generar el .htpasswd del
# Basic Auth del dashboard (no viene instalado en la imagen base).
RUN apk add --no-cache openssl

# nginx 1.27 todavía no trae `.mjs` en su tabla de tipos y lo serviría como
# application/octet-stream. El navegador rechaza un módulo ES con ese MIME
# ("Strict MIME type checking is enforced for module scripts"), así que el
# worker de pdf.js -- que Vite emite como .mjs -- no carga, pdf.js cae a su
# "fake worker" y el recorte del cuadro del acta deja de dibujarse (visto en
# el navegador, no supuesto: el único rastro era ese error de consola).
# Se añade a la tabla GLOBAL y no a una `location` con su propio bloque
# `types` porque ese bloque REEMPLAZA la tabla heredada en vez de ampliarla,
# y se perderían todos los demás tipos.
RUN sed -i 's|^}$|    text/javascript                                  mjs;\n}|' /etc/nginx/mime.types

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.sh /docker-entrypoint.d/40-dashboard-htpasswd.sh
RUN chmod +x /docker-entrypoint.d/40-dashboard-htpasswd.sh

COPY --from=build /frontend/dist /usr/share/nginx/html/dashboard

EXPOSE 80
