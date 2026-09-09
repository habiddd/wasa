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

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.sh /docker-entrypoint.d/40-dashboard-htpasswd.sh
RUN chmod +x /docker-entrypoint.d/40-dashboard-htpasswd.sh

COPY --from=build /frontend/dist /usr/share/nginx/html/dashboard

EXPOSE 80
