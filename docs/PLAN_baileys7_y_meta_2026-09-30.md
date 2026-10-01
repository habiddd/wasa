# Plan: Baileys 7 + canal oficial de Meta (Cloud API) con selector en Servicios (2026-09-30)

Pedido del usuario, en este orden:
1. Actualizar Baileys (y de paso reintentar el catálogo, que en 6.7.24 no respondía).
2. Dejar el código listo para la **API oficial de Meta** (WhatsApp Cloud API,
   no terceros). Credenciales siempre en `.env`.
3. En el dashboard, **Servicios**: un switch para elegir el canal de WhatsApp:
   Baileys o Meta.
4. Después Opus le dice al usuario qué credenciales poner.

## Investigación

### Baileys 7 (fuente: https://baileys.wiki/migration/v7)
- Versión actual en npm: `7.0.0-rc14` (tag `latest`); `6.7.24` quedó como `legacy`.
- **Solo ESM**: `require()` ya no sirve. `baileys-bot/index.js` usa
  `require` → pasar a `import` y `"type": "module"` en `package.json` (o
  `await import()` dinámico; preferir ESM entero, es un solo archivo).
- LID: las sesiones nuevas son LID. `MessageKey` trae `remoteJidAlt`
  (el JID alterno: si `remoteJid` es `@lid`, `remoteJidAlt` es el
  `@s.whatsapp.net` con el número). Hoy el bot manda `key.senderPn` a Flask;
  en 7 hay que mandar `remoteJidAlt` (con fallback a `senderPn` por si acaso)
  **con el mismo nombre de campo `senderPn` hacia Flask**, para no tocar
  `routes/webhook.py::_extraer_mensaje`.
- `isJidUser` → `isPnUser`; `onWhatsApp` ya no da LIDs (no los usamos).
- El auth state se migra solo (nuevas claves `lid-mapping`, `device-list`,
  `tctoken` que `useMultiFileAuthState` ya guarda). No debería pedir QR.
- 7.x deja de mandar ACKs de entrega de mensajes recibidos (anti-ban). No
  afecta los `messages.update` de nuestros envíos, pero verificarlo.
- El paquete sigue siendo `@whiskeysockets/baileys` (también existe `baileys`
  a secas en npm; usar el mismo nombre que hoy si `7.0.0-rc14` está ahí).

### Meta WhatsApp Cloud API (fuente: developers.facebook.com)
- Enviar: `POST https://graph.facebook.com/<VERSION>/<PHONE_NUMBER_ID>/messages`
  con `Authorization: Bearer <TOKEN>` y
  `{"messaging_product":"whatsapp","recipient_type":"individual","to":"<número>","type":"text","text":{"body":"..."}}`.
  Respuesta: `messages[0].id` (el `wamid`). Ejemplos de la doc usan `v23.0`.
- Token: de **usuario del sistema** (permanente), con permisos
  `whatsapp_business_messaging` y `whatsapp_business_management`.
- Webhook (mismo endpoint para mensajes y estados):
  - Verificación: `GET ?hub.mode=subscribe&hub.verify_token=<nuestro>&hub.challenge=<n>`
    → responder `hub.challenge` en texto plano con 200 si el token coincide; 403 si no.
  - Eventos: `POST`, firmado con `X-Hub-Signature-256: sha256=<HMAC-SHA256(body crudo, APP_SECRET)>`.
    Responder 200 rápido. Meta reintenta hasta 36 h → **deduplicar por `wamid`**.
  - Forma: `entry[].changes[].value` con `metadata.phone_number_id`,
    `contacts[{profile.name, wa_id, user_id}]`, `messages[{from, from_user_id, id, timestamp, type, text.body}]`,
    `statuses[{id, status: sent|delivered|read|failed, recipient_id, recipient_user_id, errors}]`.
  - Requiere URL **pública con HTTPS válido** (no autofirmado).
- **BSUID** (vigente 2026): `user_id` / `from_user_id` siempre vienen;
  `wa_id`/`from` (el número) **pueden faltar** si la persona usa nombre de
  usuario. Formato `CC.alfanumérico` (hasta ~131 chars). Desde julio 2026 se
  puede enviar con `"recipient": "<BSUID>"` en vez de `to`.
- **Ventana de 24 h**: texto libre solo dentro de las 24 h desde el último
  mensaje de la persona. Fuera de eso, **solo plantillas aprobadas**
  (categorías utility/marketing/authentication), con
  `{"type":"template","template":{"name":..,"language":{"code":"es"},"components":[{"type":"body","parameters":[{"type":"text","text":".."}]}]}}`.
  Los parámetros no pueden llevar saltos de línea/tabs.
- **Catálogo por Meta**: `POST /<CATALOG_ID>/products` exige `image_url`,
  `price`, `currency`, `name`, `retailer_id` y permiso `catalog_management`.
  → **Sin imagen no se puede por la vía oficial.** Queda fuera de este plan.

## Parte A — Baileys 7 (ejecutar primero)

1. **Respaldo de la sesión** antes de tocar nada (para volver atrás sin QR):
   copiar el contenido del volumen `cpa-reservas-bot_baileys_sessions` a
   `baileys-bot/.sesion-respaldo-6.7/` con un contenedor alpine temporal, y
   agregar esa ruta a `.gitignore` y `.dockerignore` (son credenciales).
2. `package.json`: `"@whiskeysockets/baileys": "7.0.0-rc14"` (fijo, no `^`,
   es una RC) y `"type": "module"`. `index.js` a ESM (`import`), incluyendo
   `http`, `fs`, `path`, `crypto`. Revisar que ningún otro archivo del
   servicio use `require`.
3. `senderPn` → `messageInfo.key.remoteJidAlt || messageInfo.key.senderPn || null`
   (solo si termina en `@s.whatsapp.net`), mismo campo `senderPn` hacia Flask.
4. Rebuild solo `baileys-bot`. Debe conectar **sin QR**. Si pide QR o falla,
   restaurar el respaldo + volver a 6.7.24 y reportar.
5. Pruebas:
   - `GET /status` → `open`.
   - Enviar un mensaje de prueba a Habid **por `/send`** con el JID `@lid` de
     Habid (144186850431118@lid) y un texto "Prueba de Baileys 7, ignora este
     mensaje": 200 con `id`, y que en `docker logs` llegue su `messages.update`
     de entrega (y que `/webhook/ack` lo registre).
   - Entrante: no se puede simular desde aquí → dejar el log de `📨 Mensaje`
     listo y pedir en el reporte que Opus/usuario escriba un "hola".
   - **Catálogo**: repetir la prueba de la fase 0 de
     `docs/PLAN_catalogo_whatsapp_2026-09-30.md` (`GET /catalog`, luego crear
     "PRUEBA LACS - borrar" sin imagen/sin precio → precio 0 → precio 1000,
     y borrarlo). Anotar la respuesta exacta. **No** seguir con la fase 2 del
     catálogo en este plan: solo reportar si ahora funciona.

## Parte B — Canal Meta + selector

### B1. Configuración (`config.py`, `.env.example`, `docker-compose.yml`)
Variables nuevas (vacías por defecto; `.env.example` con comentarios, nunca
valores reales):
- `WHATSAPP_PROVEEDOR_DEFAULT=baileys`
- `META_WA_TOKEN` (token del usuario del sistema)
- `META_WA_PHONE_NUMBER_ID`
- `META_WA_WABA_ID` (para el estado/diagnóstico)
- `META_APP_SECRET` (firma del webhook)
- `META_WEBHOOK_VERIFY_TOKEN` (lo inventa el usuario; mismo valor en el panel de Meta)
- `META_GRAPH_VERSION=v23.0`
- `META_PLANTILLA_AVISO` (nombre de la plantilla para avisos fuera de 24 h, ej. `lacs_aviso`)
- `META_PLANTILLA_IDIOMA=es`
Pasarlas a `flask-app` en `docker-compose.yml` como las demás.

### B2. Proveedor activo (sin esquema nuevo)
- Guardado en Redis, clave `whatsapp:proveedor` (`baileys` | `meta`); Redis
  ya corre con `--appendonly yes`, persiste. Si no hay clave →
  `WHATSAPP_PROVEEDOR_DEFAULT`. Funciones `proveedor_activo()` y
  `cambiar_proveedor(p)` en `whatsapp_client.py`.
- `cambiar_proveedor("meta")` se niega si falta alguna de `META_WA_TOKEN`,
  `META_WA_PHONE_NUMBER_ID`, `META_APP_SECRET`, `META_WEBHOOK_VERIFY_TOKEN`
  (devuelve la lista de las que faltan). A Baileys siempre se puede volver.

### B3. Envío (`whatsapp_client.py`, punto único)
Todo envío ya pasa por `enviar_mensaje_texto(jid_destino, texto)` (webhook,
avisos, decisiones de la LACS). Mantener la firma y despachar por proveedor:
- Baileys: igual que hoy.
- Meta: destino = número (`jid.split("@")[0]` si termina en
  `@s.whatsapp.net`; si es `@lid` → no hay número: `(False, None)` y log
  claro). Texto libre si la persona escribió en las últimas 24 h (último
  `auditoria_bot` con `mensaje_entrante` no nulo para ese `telefono_wa`);
  si no, plantilla `META_PLANTILLA_AVISO` con **un** parámetro = el texto
  aplanado (saltos de línea → " · ", recortado a 1000). Si no hay plantilla
  configurada → `(False, None)` + log. Devuelve `(True, wamid)`.
- `jid_de(usuario)`: con Meta, `f"{usuario.telefono_wa}@s.whatsapp.net"`
  (el LID no sirve en Meta); con Baileys igual que hoy.
- `ponytail:` una sola plantilla genérica; si Meta la rechaza o se quieren
  textos por tipo de aviso, pasar a una por tipo.

### B4. Recepción (`routes/webhook.py`)
- Factorizar el cuerpo de `recibir_webhook` en
  `_procesar_mensaje(numero, texto, remote_jid)` sin cambiar comportamiento;
  `recibir_webhook` (Baileys) queda: extraer → **si el proveedor activo no es
  baileys, ignorar** (200 `{"status":"ignorado_proveedor"}`) → procesar.
- `GET /webhook/meta`: verificación (`hub.*`).
- `POST /webhook/meta`: validar `X-Hub-Signature-256` con `META_APP_SECRET`
  sobre `request.get_data()` (comparación en tiempo constante; 403 si no
  coincide o falta el secreto). Por cada `messages[]` de tipo `text`:
  deduplicar por `id` en Redis (`SET NX EX 172800`), si el proveedor activo
  no es meta ignorar, y llamar `_procesar_mensaje(from, text.body,
  f"{from}@s.whatsapp.net")`. Si falta `from` (usuario con nombre de
  usuario, solo BSUID): loguear y responder con `recipient` =
  `from_user_id` un texto fijo pidiendo que comparta su número — `ponytail:`
  soporte completo de BSUID requiere columna nueva en `usuarios`, fuera de
  este plan. Por cada `statuses[]`: `guardrails.actualizar_estado_envio(id,
  estado)` mapeando sent→el mismo estado que hoy da `server_ack`,
  delivered→`delivery_ack`, read→`leido`, failed→`fallido` (ver el mapeo
  actual en `guardrails.actualizar_estado_envio`). Responder siempre 200
  tras validar la firma (aunque algo falle adentro; loguear).
- `nginx.conf` del frontend: `location /webhook/meta` → `flask-app:5000`, sin
  auth básica (la protege la firma). Nada más de `/webhook/` se expone.

### B5. Dashboard — Servicios
- Backend: `GET /dashboard/api/whatsapp/proveedor` →
  `{activo, disponibles: {baileys: {estado}, meta: {configurado, faltan:[...], estado}}}`.
  El estado de Meta: si está configurado, `GET https://graph.facebook.com/<v>/<PHONE_NUMBER_ID>?fields=display_phone_number,verified_name,quality_rating`
  (timeout 5 s) → número, nombre verificado y calidad, o el error de Meta.
  `PUT` con `{"proveedor": "baileys"|"meta"}` → `cambiar_proveedor`, 400 con
  las variables faltantes.
- Frontend `ServiciosPanel.tsx`: una tarjeta "Canal de WhatsApp" con un
  switch/segmento Baileys | Meta (componentes ya usados en el proyecto), el
  estado de cada uno y, si Meta no está configurado, la lista de variables
  de `.env` que faltan (deshabilitado el cambio). Confirmación antes de
  cambiar ("Los mensajes empezarán a salir por …"). Mantener el QR de
  Baileys como está.

### B6. Pruebas (`entregables/OE3/pruebas/verificar_meta_whatsapp.py`)
Sin credenciales reales ni llamadas a Meta (parchear `requests.post/get`
dentro del script):
- Verificación GET: token correcto → challenge; incorrecto → 403.
- POST con firma válida (calculada con un `META_APP_SECRET` de prueba
  puesto en `app.config` dentro del script) → procesa; firma inválida → 403.
- Payload de texto real de la doc (con `from` = número ficticio que no
  exista) con proveedor=meta → se crea la interacción (auditoria) y el envío
  sale por la rama Meta (payload capturado: `to`, `type: text`).
  Mismo `wamid` dos veces → se procesa una sola vez.
- Fuera de 24 h → sale como plantilla con el texto aplanado.
- Status `delivered` → `auditoria_bot.estado_envio` actualizado.
- Con proveedor=meta, el webhook de Baileys responde `ignorado_proveedor`.
- `cambiar_proveedor("meta")` sin variables → error con la lista.
- Al final: proveedor vuelve a `baileys`, se borran las filas/claves de
  prueba (usuario ficticio, auditoria, claves Redis). Todo lo que se pueda en
  transacción revertida.
- Regresión: `verificar_robustez_bot.py`, `verificar_celular_registro.py`.

Despliegue: `docker compose up -d --build baileys-bot flask-app dashboard-frontend`
y `docker compose restart dashboard-frontend`. El proveedor queda en
**baileys** al terminar.

## Lo que el usuario tendrá que dar después (lo explica Opus)
Token de usuario del sistema, Phone Number ID, WABA ID, App Secret, un verify
token inventado, nombre de la plantilla aprobada, y una URL pública HTTPS
para `/webhook/meta`.
