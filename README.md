# LACS Reservas Bot — Punto de partida

Scaffold inicial para el bot de reservas de equipos/espacios de la LACS
(Uninorte) por WhatsApp, con IA local y back-office de analítica.

**Diseño clave:** el canal de WhatsApp corre sobre **Baileys** (WhatsApp Web
no oficial, gratuito), que conecta directamente a WhatsApp mediante WebSocket
sin necesidad de la API oficial de Meta. Tu backend Flask recibe los mensajes
por webhook desde el servicio de Baileys, aplica guardrails, consulta al LLM
local y responde por WhatsApp.

```
WhatsApp (tu número) → Baileys Bot → webhook → Flask → Postgres
                                                     ↓
                                              LLM local (Ollama/LM Studio)
```

## 1. Requisitos previos

- Docker y Docker Compose instalados en tu servidor Ubuntu
- Tu número de WhatsApp libre, con WhatsApp ya instalado en un celular
  (lo necesitas un momento para escanear el QR)
- Ollama o LM Studio corriendo en tu red local (ajústalo en `.env`)

## 2. Puesta en marcha

```bash
cp .env.example .env
# Edita .env: reemplaza TODOS los valores "changeme_*" por claves reales.
# IMPORTANTE: la contraseña de APP_DB_PASSWORD en .env debe coincidir
# con la que usas en postgres/init/01-init.sql (ese script solo corre
# una vez, la primera vez que se crea el volumen de Postgres).

docker compose up -d --build
docker compose logs -f baileys-bot   # espera a que aparezca el QR
```

## 3. Conectar tu número de WhatsApp (modo Baileys, gratis)

El servicio de Baileys genera automáticamente un código QR en la terminal
cuando se inicia. Solo necesitas escanearlo:

```bash
# Ver el QR en la terminal
docker compose logs -f baileys-bot
```

Verás un código QR en la terminal. Ábrelo en WhatsApp → Dispositivos vinculados
→ Escanear código QR, usando el celular con tu número libre.

Alternativamente, puedes obtener el QR vía API:

```bash
curl http://localhost:5000/webhook/qr
```

## 4. Probar el flujo completo

1. Envía un WhatsApp al número recién vinculado, algo como *"quiero
   reservar una cámara"*.
2. Baileys Bot recibe el mensaje y lo reenvía por webhook a
   `flask-app:5000/webhook/whatsapp`.
3. Flask aplica guardrails (rate limit, filtrado, whitelist de intent),
   consulta al LLM local, y responde por Baileys Bot.
4. Revisa `docker compose logs -f flask-app` si algo no llega.

## 5. Dashboard interno de monitoreo

En `http://localhost:8080/dashboard` tienes un panel con seis secciones en
un sidebar colapsable (cada una con su propio ícono; en modo colapsado el
sidebar muestra solo los íconos, con el label como tooltip al pasar el
mouse; la sección activa se recuerda entre recargas, vía `localStorage`):

- **Servicios**: submenú desplegable de dos entradas (clic en "Servicios"
  para contraerlo/expandirlo). "Estado" muestra el estado en vivo de
  Postgres, Redis, Baileys/WhatsApp, el LLM local y el propio Flask (se
  refresca solo cada 10s); "QR / WhatsApp" muestra el código QR de
  vinculación mientras no esté conectado, o el número vinculado una vez lo
  esté (se refresca sola cada 5s).
- **Métricas**: reservas y mensajes bloqueados por guardrails en los
  últimos 30 días, un gráfico de reservas por día del mismo periodo, y las
  últimas interacciones registradas en `auditoria_bot`.
- **Calendario**: las reservas de activos y lugares en vistas de **mes,
  semana y día**, vía `GET /dashboard/api/reservas?desde&hasta`. Las
  reservas del mismo usuario y horario se muestran como **un solo evento**
  (una sola reserva puede cubrir varios activos: una cámara y sus lentes),
  etiquetado como "Cámara Canon EOS R +2". El color indica el estado
  (confirmada / completada / cancelada / no_show) y el ícono si es un activo
  o un lugar. Se puede filtrar por tipo, por recurso concreto y por estado
  (por defecto oculta las canceladas), y al hacer clic en un evento se abre
  un modal con su **número de reserva**, el usuario, el horario y la lista
  de activos, desde donde se puede **editar** la reserva (horario, estado y,
  si es de un solo activo, el recurso) vía
  `PATCH /dashboard/api/reservas/<id>` o
  `PATCH /dashboard/api/reservas/bloque`, que mueve todas las líneas de la
  reserva en una sola transacción. Todos los horarios se muestran en hora
  local de la LACS (`America/Bogota`), sin importar la zona del equipo desde
  el que se abra el dashboard. Se refresca solo cada 60s.
- **Usuarios**: submenú desplegable de dos entradas, mismo patrón que
  "Servicios". "Usuarios" lista los usuarios que completaron el registro
  por WhatsApp (`registro_completo=True` -- deja afuera contactos a medio
  registrar y el usuario dummy de `seed.py`), con botón "Editar" por fila
  para cambiar nombre, teléfono, código Uninorte, cédula, rol
  (estudiante/staff/admin) y si está activo, vía
  `GET/PATCH /dashboard/api/usuarios[/<id>]` (se refresca sola cada 30s, más
  el botón "Refrescar" manual). "Sesiones" lista las reservas de cada
  usuario vía `GET /dashboard/api/reservas`, ordenables por "Más reciente" o
  agrupadas "Por usuario" (una tarjeta por persona, con su nombre y todas
  sus sesiones), y filtrables por categoría (el mismo campo `rol` de
  Usuarios).
- **Activos**: equipos (`tipo='equipo'`) agrupados en subpestañas por
  categoría, con su árbol de relaciones padre/hijo (ej. una cámara y sus
  lentes -- los hijos se pueden desplegar/colapsar por tarjeta). Permite
  editar cualquier dato del activo, cambiar su estado (Disponible / En
  mantenimiento / No disponible) desde un selector en la propia tarjeta sin
  abrir el formulario completo, y reasignar su padre (la UI no deja elegir
  un descendiente propio, para no crear un ciclo). Solo los activos
  "Disponible" entran al catálogo que ofrece el bot por WhatsApp. El botón
  "+ Agregar activo" crea uno nuevo, decidiendo ahí mismo si es un activo
  independiente (padre) o un hijo de otro equipo ya existente. No se
  auto-refresca -- botón "Refrescar" manual, para no perder una edición a
  medio hacer.
- **Lugares**: espacios reservables (`tipo='espacio'`, ej. salas/estudios),
  misma tarjeta editable que Activos (más su propio botón "+ Agregar
  lugar") pero sin agrupación por categoría ni relación padre/hijo.

Estas dos últimas comparten los endpoints `GET/POST /dashboard/api/activos`
y `PATCH /dashboard/api/activos/<id>` (ver
`flask-app/app/routes/dashboard.py`), protegidos por la misma autenticación
básica del resto del dashboard.

Está protegido con autenticación básica (`DASHBOARD_USER` /
`DASHBOARD_PASSWORD` en `.env` — genera una contraseña real, no dejes el
placeholder). Nginx pide ese login para servir el shell estático, y
`flask-app` vuelve a validarlo de forma independiente en cada llamada a
`/dashboard/api/*` (si `DASHBOARD_PASSWORD` no está configurada, esas
llamadas responden `503` en vez de quedar abiertas).

La UI es una app React + [shadcn/ui](https://ui.shadcn.com/) (Tailwind v4,
selector de tema claro/oscuro/sistema) en `dashboard-frontend/`,
con su propio contenedor (`dashboard-frontend` en `docker-compose.yml`):
un build multi-stage compila la SPA con Node y la sirve un Nginx que hace
proxy de `/dashboard/api/*` hacia `flask-app:5000`. El backend ya no sirve
ni compila HTML/JS -- solo expone esa API. Para desarrollar la UI en
caliente:

```bash
cd dashboard-frontend
npm install
npm run dev   # sirve en http://localhost:5173/dashboard/, con proxy hacia Flask :5000
```

## 6. Migrar a la API oficial de Meta (cuando estés listo para producción)

Si en el futuro quieres migrar a la API oficial de Meta:

1. Completa la verificación de negocio de Meta (Meta Business Manager +
   WhatsApp Business Platform) — este trámite puede tardar días, empiézalo
   con tiempo.
2. Reemplaza el servicio `baileys-bot` en `docker-compose.yml` con
   Evolution API (configuración con `WA_BUSINESS_TOKEN` y
   `WA_BUSINESS_URL`).
3. Actualiza `flask-app/app/whatsapp_client.py` para usar la API de
   Evolution en lugar de Baileys.
4. Diseña tus respuestas como **flujo guiado** (botones/listas, no chat
   libre) — así calzas con la política de Meta vigente desde enero 2026,
   que permite bots de reservas/soporte con resultados predecibles, pero
   prohíbe chatbots de IA conversacional abierta en la Cloud API.

## 7. Qué ya incluye este scaffold

- Guardrails: rate limiting por número (`app/guardrails.py`), filtrado
  básico contra inyección de prompt, whitelist de intents, auditoría de
  cada interacción en la tabla `auditoria_bot`.
- Esquema de datos base: `usuarios`, `recursos` (equipos/espacios),
  `reservas`, `secuencias_confirmacion` (para tu flujo de confirmación de equipos),
  `auditoria_bot` — ver `postgres/init/02-schema.sql`.
- Dos endpoints de analítica de ejemplo (`/api/analytics/...`) como base
  de tu back-office.
- Dashboard interno en `/dashboard` con estado de servicios, QR y
  métricas (ver sección 5).
- Separación clara: canal (Baileys) / lógica de negocio (Flask) /
  IA (LLM local) — para que cada pieza se pueda reemplazar sin romper
  las demás.

## 8. Qué falta por construir (siguiente iteración)

- Lógica real de reservas por intent (hoy hay un `TODO` en
  `app/routes/webhook.py`): validar disponibilidad, crear/cancelar
  reserva, activar secuencia de confirmación.
- Migración de tu data actual de la LACS al esquema de `recursos`/`usuarios`.
- Autenticación para los endpoints de analítica (hoy están abiertos;
  el dashboard en `/dashboard` sí tiene autenticación básica, pero
  `/api/analytics/*` sigue sin protección).
- Mover el rate limiter de memoria a Redis si vas a correr más de un
  worker de Flask.
- Ver `docs/SEGURIDAD.md` para el checklist completo de guardrails
  pendientes antes de producción con datos reales.

## Estructura del proyecto

```
lacs-reservas-bot/
├── docker-compose.yml       # Postgres + Redis + Baileys + Flask + dashboard-frontend
├── .env.example
├── postgres/init/           # esquema inicial (se ejecuta 1 sola vez)
├── baileys-bot/
│   ├── index.js              # canal de WhatsApp (QR, mensajes, /status, /send)
│   └── Dockerfile
├── flask-app/               # backend: 100% Python, solo expone la API
│   ├── app/
│   │   ├── config.py
│   │   ├── extensions.py     # db, limiter, cliente de Redis
│   │   ├── models.py
│   │   ├── guardrails.py
│   │   ├── estado_bot.py     # último QR recibido (compartido webhook/dashboard)
│   │   ├── whatsapp_client.py
│   │   ├── llm_client.py
│   │   └── routes/
│   │       ├── webhook.py       # recibe mensajes de WhatsApp
│   │       ├── analytics.py     # endpoints de analítica
│   │       └── dashboard.py     # API del panel de monitoreo (/dashboard/api/*)
│   └── wsgi.py
├── dashboard-frontend/     # UI del dashboard: React + shadcn/ui, contenedor propio
│   ├── Dockerfile            # build Node -> estáticos servidos por Nginx
│   ├── nginx.conf            # sirve el shell y hace proxy de /dashboard/api/ a flask-app
│   └── src/                  # SPA (Vite + React 19 + TS + Tailwind v4)
└── docs/
    ├── ARQUITECTURA.md
    └── SEGURIDAD.md
```
