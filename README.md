# LACS Reservas Bot — Punto de partida

Scaffold inicial para el bot de reservas de equipos/espacios de la LACS
(Uninorte) por WhatsApp, con IA local y back-office de analítica.

**Diseño clave:** el canal de WhatsApp tiene dos proveedores y se elige cuál
está activo desde el dashboard (Servicios → QR / WhatsApp), sin reiniciar:

- **Baileys** (WhatsApp Web no oficial, gratuito): se vincula escaneando un QR.
- **API oficial de Meta** (WhatsApp Cloud API): Meta llama al webhook
  `/webhook/meta`, firmado con el App Secret. Fuera de la ventana de 24 h
  solo se puede escribir con una plantilla aprobada.

Flask recibe los mensajes por webhook, aplica guardrails, consulta al LLM
local y responde por el canal activo. El webhook del otro proveedor ignora
los mensajes.

```
WhatsApp → Baileys Bot ──┐
                         ├→ webhook → Flask → Postgres
WhatsApp → Meta Cloud ───┘              ↓
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

En `http://localhost:8080/dashboard` tienes un panel con un sidebar
colapsable. Cada sección tiene su propio ícono; en modo colapsado el sidebar
muestra solo los íconos, con el nombre como tooltip al pasar el mouse. La
sección activa se recuerda entre recargas (`localStorage`), y la primera vez
abre en **Hoy**. El menú lleva, en orden, Hoy, Métricas, Calendario,
Reservas, Excepciones, Usuarios, Activos y Lugares. **Servicios** va aparte,
al pie del sidebar. Todo el menú se puede recorrer con el teclado, incluidos
los submenús.

- **Hoy**: la pantalla de inicio del mostrador. Reparte las reservas en
  cuatro bloques: **Por entregar hoy**, **Por recibir hoy**, **Por
  confirmar** y, al final, **Retrasados y no devueltos**. Cada fila trae la
  hora, el tiempo que falta y el botón para firmar el acta que toca, sea de
  entrega o de devolución. Un préstamo `no_devuelta` sigue en la lista
  mientras su activo esté en "No devuelto". Cuando la LACS lo pasa a otro
  estado desde Activos (porque apareció o se dio de baja), sale de Hoy. Se
  refresca solo cada 30s. Ver `docs/ARQUITECTURA.md`, sección "Vista Hoy".
- **Servicios** (al pie del sidebar, con un punto verde o rojo según la
  salud de los servicios): submenú desplegable de dos entradas. "Estado"
  muestra el estado en vivo de Postgres, Redis, Baileys/WhatsApp, el LLM
  local y el propio Flask (se refresca solo cada 10s). "QR / WhatsApp"
  tiene el selector del canal (Baileys o la API oficial de Meta); con Meta
  activo, las métricas de Meta por semana, mes, año o rango (gastos,
  mensajes enviados, ventanas de 24 h, estado de la cuenta y plantillas);
  y la sesión de Baileys: el código QR de vinculación mientras no esté
  conectado, o el número vinculado una vez lo esté (se refresca solo cada
  5s). Ver `docs/GUIA_META_WHATSAPP.md`.
- **Métricas**: números sacados de los datos que ya guarda la base, para
  un periodo de 30 días, 90 días, todo, o un rango de fechas a elección
  ("Personalizado"). El periodo es uno solo para General y para las
  métricas de un activo, y se conserva al cambiar de pestaña. Tiene tres
  subpestañas:
  - **General**: cuántas reservas, préstamos, denegadas, canceladas y
    no-shows hubo; el top 10 de equipos y de espacios más prestados; el top 10
    de usuarios; la puntualidad al recoger y al devolver; las reservas por
    día, por día de la semana y por hora; y el uso del bot. También muestra
    el inventario (activos agregados, dados de baja y en mantenimiento) y
    cuántas reservas salieron de Barranquilla, es decir, las que llevan
    seguro.
  - **Por activo**: primero se escoge un equipo o un espacio. Después se
    elige entre **Métricas**, que son los mismos números pero solo de ese
    activo y en el mismo periodo, y **Timeline**, que es su historia día
    por día: reservas, cambios de estado, actas y cambios de estado del
    propio activo. El timeline es siempre de **todo el tiempo**, no sigue
    el periodo, así que en esa vista el selector de periodo se oculta.
  - **Por uso**: los equipos pedidos, en dos bloques separados. **Para
    asignaturas (con NRC)**: qué NRC piden más equipos y qué equipos pide
    cada uno. **Particulares (sin NRC)**: qué estudiantes piden más equipos y
    qué equipos pide cada uno. Cada bloque trae además sus equipos más
    pedidos.

  El botón **Descargar Excel** abre un popup con dos opciones. El Excel de
  **métricas** trae General y Por uso con el mismo periodo que esté elegido en
  la página (30 días, 90 días, todo o personalizado); el popup dice cuál es.
  Tiene siete hojas con tablas en color y gráficos: Resumen, Estados, Cuándo
  se reserva, Top activos y usuarios, Por uso (NRC y particulares),
  Inventario y seguro, y Bot y hábitos. La **base de datos en raw** trae
  todas las tablas completas, una por hoja, la vista `reserva_puntualidad` y
  una hoja "Índice" con cuántas filas trae cada una. Si una tabla pasa del
  límite de Excel (1.048.576 filas por hoja), sigue en otra hoja
  ("auditoria_bot (2)"…), y el Índice lo dice.
  Ver `docs/ARQUITECTURA.md`, sección "Métricas".
- **Calendario**: las reservas de activos y lugares en vistas de **mes,
  semana y día**, vía `GET /dashboard/api/reservas?desde&hasta`. Las
  reservas del mismo usuario y horario se muestran como **un solo evento**
  (una sola reserva puede cubrir varios activos: una cámara y sus lentes),
  etiquetado como "Cámara Canon EOS R +2". El color indica el estado
  (por confirmar / confirmada / entregada / completada / devuelta tarde /
  no devuelta / no se presentó / cancelada / denegada) y el ícono si es un
  activo o un lugar. La leyenda plegable "Qué significa cada color" explica
  cada estado. Se puede filtrar por número de reserva, tipo, recurso,
  usuario y estado. Cada filtro (salvo el número) tiene buscador y deja
  marcar varias opciones; por defecto se ocultan las canceladas y las
  denegadas. Al hacer clic en un evento se abre
  un modal con su **número de reserva**, el usuario, el horario y la lista
  de activos, desde donde se puede **editar** la reserva (horario, estado y,
  si es de un solo activo, el recurso) vía
  `PATCH /dashboard/api/reservas/<id>` o
  `PATCH /dashboard/api/reservas/bloque`, que mueve todas las líneas de la
  reserva en una sola transacción. Ese modal también tiene el botón "Ver
  acta" (ver "Reservas"). Todos los horarios se muestran en hora
  local de la LACS (`America/Bogota`), sin importar la zona del equipo desde
  el que se abra el dashboard. Se refresca solo cada 60s.
- **Reservas**: la misma lista de sesiones en tabla (o tarjetas en pantallas
  angostas), ordenada por número de reserva de mayor a menor y filtrable
  por periodo, por si necesita seguro y con los mismos filtros
  multiselección del Calendario: tipo, recurso, usuario y estado (con el
  conteo de cada uno). Un buscador
  encuentra por estudiante, teléfono, código, número de reserva, activo o
  placa, sin importar tildes ni mayúsculas. Cada fila tiene un botón que
  abre el **acta de entrega y devolución** en un modal con la vista previa
  del PDF (todos los datos de la reserva más los 4 cuadros de firma de
  entrega/devolución), el botón de descarga y el historial de versiones
  firmadas.

  Desde ese modal se registra el acta, que es lo que mueve la reserva por su
  ciclo de estados:

  ```
  confirmada ──(acta de ENTREGA)──────────────> entregada ──(acta de DEVOLUCIÓN)──> completada
      │                                              ▲                          └─> devuelta_tarde
      └─(sin recoger, automático 30 min después)──> no_show ──(acta de ENTREGA, si su horario sigue vigente)┘
  ```

  Hay dos formas de registrar el acta. La primera es **firmar en pantalla**,
  con un asistente de cuatro pasos:

  1. marcar los equipos que se entregan o se devuelven, con una nota
     opcional;
  2. la firma del usuario;
  3. los datos y la firma del administrador;
  4. revisar el PDF compuesto antes de guardarlo.

  La segunda es **subir un PDF ya firmado** desde otro dispositivo.

  A mano solo va la firma: la cédula del usuario la imprime el servidor, la
  del administrador se teclea, y la fecha y la hora se ponen solas. El
  navegador recuerda el nombre y la cédula del administrador para la próxima
  acta. Si ya hay una firma, cerrar el asistente pide confirmación antes de
  descartarla. Una reserva entregada cuyo horario ya venció se marca como
  "Retrasada" mientras el equipo siga fuera. Un equipo que no se marca en
  la devolución deja su línea en `no_devuelta` y el activo en "No devuelto".

  **No-show automático**: una `confirmada` que nadie recoge pasa sola a
  `no_show` 30 minutos después de su hora de inicio (configurable,
  `NO_SHOW_MARGEN_MIN`), con avisos de WhatsApp al estudiante 30 min antes, a
  la hora, y al marcar el no-show (además, 30 min antes de `fin` se le pide
  devolver a quien ya recogió, `AVISO_DEVOLUCION_MIN`). Si el estudiante llega tarde pero su
  horario todavía no terminó, se le puede entregar igual — queda marcada
  como **"Entrega tardía"** (bandera `entrega_tardia`, no un estado nuevo:
  sobrevive a la devolución) y el horario no se extiende por la demora. Ver
  `docs/ARQUITECTURA.md`, sección "No-show automático y avisos de recogida".

  Esos tres estados **no se pueden fijar a mano** desde el selector: la única
  puerta es el acta, que es el comprobante de que el equipo cambió de manos.
  `no_show` sí se puede poner o quitar a mano, además de llegar solo.

  Cada firma o subida guarda un archivo nuevo en el volumen `actas_pdf`; no se
  sobrescribe nunca ninguna versión. Ver `docs/ARQUITECTURA.md` para el detalle.
- **Excepciones**: los días en que el bot no acepta reservas (días de la
  semana bloqueados, festivos de Colombia y fechas sueltas bloqueadas o
  desbloqueadas), editables en vista calendario o en vista lista. Ver
  `docs/ARQUITECTURA.md`, sección "Excepciones: lista negra de fechas".
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
  abrir el formulario completo, y reasignar su padre. "No devuelto" solo lo
  pone el acta de devolución, y para sacar un activo de ahí se pide un
  motivo. Al reasignar el padre, la UI no deja elegir un descendiente
  propio, para no crear un ciclo. Solo los activos
  "Disponible" entran al catálogo que ofrece el bot por WhatsApp. El botón
  "+ Agregar activo" crea uno nuevo, decidiendo ahí mismo si es un activo
  independiente (padre) o un hijo de otro equipo ya existente. El botón
  "Importar" agrega activos y lugares en lote desde una plantilla que se
  descarga en el mismo diálogo: un Excel con macros (`.xlsm`) que se llena
  con el formulario de su primera hoja o directo en la tabla, y desde el que
  también se agregan categorías y subcategorías; o un CSV. El padre va por
  placa y el lugar por nombre. Solo agrega, y si una fila tiene un error no se importa
  ninguna (ver `docs/ARQUITECTURA.md`, "Importar activos"). No se
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

## 6. Canal oficial de Meta (WhatsApp Cloud API)

El canal de Meta ya está integrado junto a Baileys. Para activarlo:

1. Sigue `docs/GUIA_META_WHATSAPP.md`: app de Meta, token permanente de
   usuario del sistema, webhook y plantilla. Lo que falta para producción
   (número real, verificación del negocio, método de pago, publicar la app)
   está en `docs/META_WHATSAPP_ESTADO_2026-09-30.md`.
2. Llena las variables `META_*` en `.env` (ver `.env.example`). Todas las
   credenciales van solo ahí, nunca en el código.
3. Meta necesita una URL pública con HTTPS para `/webhook/meta`. En
   desarrollo se usa un túnel de Cloudflare (paso 8 de la guía). Su URL
   cambia cada vez que el túnel se reinicia, y entonces hay que actualizarla
   en Meta.
4. En el dashboard, Servicios → QR / WhatsApp, elige **Meta**. Ahí mismo
   aparecen sus métricas: gastos, mensajes enviados, ventanas de 24 h
   abiertas, estado del número y de la cuenta, y plantillas.
5. Prueba: `docker exec -i lacs_flask_app python - < entregables/OE3/pruebas/verificar_meta_whatsapp.py`
   (no llama a Meta ni manda mensajes).

La política de Meta vigente desde enero de 2026 permite bots de reservas y
soporte con resultados predecibles, pero prohíbe los chatbots de IA
conversacional abierta en la Cloud API: mantén las respuestas como un
**flujo guiado**.

## 7. Qué ya incluye este scaffold

- Guardrails: rate limiting por número (`app/guardrails.py`), filtrado
  contra inyección de prompt (compara sobre una forma normalizada y
  compactada del mensaje, para que el espaciado o la puntuación no lo
  evadan), whitelist de intents, y auditoría de cada interacción en la
  tabla `auditoria_bot`.
- Esquema de datos base: `usuarios`, `recursos` (equipos/espacios),
  `reservas`, `reserva_actas` (histórico de actas firmadas),
  `secuencias_confirmacion` (para tu flujo de confirmación de equipos),
  `auditoria_bot` — ver `postgres/init/02-schema.sql`.
- Actas de entrega y devolución en PDF, firmables en pantalla, que son las
  que mueven la reserva por su ciclo de estados (ver sección 5).
- Dos endpoints de analítica de ejemplo (`/api/analytics/...`) como base
  de tu back-office.
- Dashboard interno en `/dashboard` con estado de servicios, QR y
  métricas (ver sección 5).
- Separación clara: canal (Baileys o Meta) / lógica de negocio (Flask) /
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
│                            # (volumen actas_pdf: histórico de actas firmadas)
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
│   │   ├── acta_pdf.py       # genera el acta (reportlab) y sus anclas de firma
│   │   ├── actas_service.py  # actas en disco + transiciones de estado
│   │   ├── whatsapp_client.py
│   │   ├── llm_client.py
│   │   └── routes/
│   │       ├── webhook.py       # recibe mensajes de WhatsApp (Baileys y /webhook/meta)
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
