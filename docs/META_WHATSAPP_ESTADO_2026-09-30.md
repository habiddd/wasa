# WhatsApp por la API oficial de Meta: estado al 2026-09-30

Registro de lo que se hizo el 30 de septiembre de 2026 para que el bot de
reservas de la LACS pueda usar la **WhatsApp Cloud API de Meta** además de
Baileys, qué quedó funcionando y qué falta. El paso a paso completo, para
repetirlo desde cero, está en [GUIA_META_WHATSAPP.md](GUIA_META_WHATSAPP.md).

Todo está en la rama `dev` (commit `fe787b3`).

## Resumen

| Tema | Estado |
|---|---|
| Baileys actualizado a 7.0.0-rc14 | ✅ Conectado sin QR nuevo |
| Código listo para Meta (envío, webhook, selector) | ✅ Desplegado, pruebas pasan |
| Credenciales de Meta en `.env` | ✅ Completas |
| Envío por la API oficial | ✅ Probado (`hello_world` llegó a Habid) |
| Plantilla `lacs_aviso` para avisos fuera de 24 h | ⏳ Creada, en revisión de Meta |
| Recibir mensajes por Meta (webhook) | ✅ Llega por el túnel `lacs_tunel` (URL temporal) |
| Número real de la LACS en la Cloud API | ❌ Hoy se usa el número de prueba de Meta |
| Prueba completa por Meta (registro, reserva, cancelación) | ✅ Hecha por Habid el 2026-10-01: funciona igual que Baileys |
| Canal activo | **Meta** desde el 2026-10-01 (Baileys sigue vinculado) |
| Métricas de Meta en el dashboard | ✅ Gastos, envíos, ventanas de 24 h, cuenta y plantillas, por periodo |
| Catálogo de WhatsApp desde el dashboard | ❌ No se pudo; ver abajo |

## 1. Baileys 7

- `baileys-bot/package.json`: `@whiskeysockets/baileys` fijo en `7.0.0-rc14`
  (era `^6.7.18`, instalado 6.7.24) y `"type": "module"`.
- `baileys-bot/index.js` pasó de `require` a `import` (Baileys 7 es solo ESM).
- El número real de quien escribe por LID ahora llega en `key.remoteJidAlt`;
  se le sigue mandando a Flask con el nombre `senderPn` (con respaldo al
  `senderPn` viejo), así `routes/webhook.py` no cambió.
- Si WhatsApp cierra la sesión (dispositivo desvinculado desde el celular),
  el bot borra las credenciales guardadas y vuelve a conectarse, lo que
  genera un QR nuevo solo. Antes se quedaba desconectado sin pedir QR.
- **Respaldo de la sesión de 6.7** en `baileys-bot/.sesion-respaldo-6.7/`
  (credenciales de WhatsApp: está en `.gitignore` y en
  `baileys-bot/.dockerignore`). Sirve para volver a 6.7.24 sin escanear QR.
- Pendiente de confirmar en vivo: que un "hola" entrante llegue bien con la
  versión 7 y que se vea la confirmación de entrega de los envíos.
- Al arrancar, Baileys registra un "Timed Out" en sus consultas iniciales y
  un "timed out waiting for message" periódico; no han afectado el envío ni la
  recepción.

## 2. Código para el canal de Meta

Plan: [PLAN_baileys7_y_meta_2026-09-30.md](PLAN_baileys7_y_meta_2026-09-30.md).

- **Selector de canal**: se guarda en Redis (`whatsapp:proveedor` =
  `baileys` | `meta`). Si no hay clave se usa `WHATSAPP_PROVEEDOR_DEFAULT`.
  No deja cambiar a Meta si faltan credenciales.
- **Envío** (`flask-app/app/whatsapp_client.py`): todo mensaje del bot pasa
  por `enviar_mensaje_texto`, que despacha según el canal activo. Por Meta:
  - Texto libre si la persona escribió por Meta en las últimas 24 h: la
    ventana vive solo en Redis (`meta:ventana:<numero>`) y se mide desde el
    `timestamp` del mensaje, no desde la auditoría (que mezcla los dos canales).
  - Si no, la plantilla `META_PLANTILLA_AVISO` con el texto en un solo
    parámetro (saltos de línea y espacios repetidos cambiados por " · " o un
    espacio, máximo 900 caracteres).
  - A un usuario que solo tiene JID `@lid` (sin número) no se le puede
    escribir por Meta: se registra el error. Con `@bsuid` sí, por `recipient`.
  - Si Meta rechaza el envío, se registra su código de error (`error.code`).
- **Recepción** (`flask-app/app/routes/webhook.py`):
  - `GET /webhook/meta`: verificación con `META_WEBHOOK_VERIFY_TOKEN`.
  - `POST /webhook/meta`: valida la firma `X-Hub-Signature-256` con
    `META_APP_SECRET`, descarta repetidos por `wamid` (Meta reintenta hasta
    36 h) y pasa cada texto al mismo flujo del bot que usa Baileys.
    Los estados de entrega se mapean a la auditoría (sent → `server_ack`,
    delivered → `delivery_ack`, read → `leido`, failed → `fallido`).
  - Solo se atienden mensajes del canal activo; el otro canal se ignora.
  - Se ignoran los eventos cuyo `metadata.phone_number_id` no es el
    configurado, y los `failed` y `errors` quedan en el log.
  - Meta ya no pisa `usuarios.jid_wa` (no reemplaza el `@lid` de Baileys; sí
    rellena uno vacío).
  - Quien escribe con nombre de usuario (sin número, solo BSUID) se registra
    con `telefono_wa` = BSUID y `jid_wa` = `<BSUID>@bsuid`, como un LID: el
    bot le pide el celular.
- **nginx** (`dashboard-frontend/nginx.conf`): expone solo
  `location = /webhook/meta` hacia Flask, sin usuario/contraseña (lo protege
  la firma). El resto de `/webhook/` sigue sin exponerse.
- **Dashboard**: Servicios → "QR / WhatsApp" (página "WhatsApp"):
  - Tarjeta **Canal de WhatsApp**: botones Baileys | Meta (el activo en
    negro con ✓, no deshabilitado), qué canal se usa, y solo los avisos que
    impiden elegir uno (variables faltantes en `.env`, Meta sin responder).
    Endpoint `GET/PUT /dashboard/api/whatsapp/proveedor`.
  - Tarjeta **API de Meta** (solo con Meta activo), endpoint
    `GET /dashboard/api/whatsapp/meta/metricas?periodo=7|30|365|rango&desde=&hasta=`.
    Periodos: Semana, Mes, Año y Personalizado (fechas civiles de la LACS).
    Muestra, en este orden:
    1. **Gastos**: costo total (de `pricing_analytics`), mensajes gratis y
       con tarifa, barras por tipo de cobro.
    2. **Mensajes enviados**: enviados y entregados (de `analytics`) y
       gráfico por día (por mes si el rango pasa de 62 días).
    3. **Ventanas de 24 h abiertas**: de Redis (`meta:ventana:<número>` y
       su TTL), no de Meta.
    4. **Número y cuenta**: número, nombre verificado, estado, calidad,
       modo, límite de mensajes, velocidad, revisión, verificación del
       negocio y si puede iniciar conversaciones; bloqueos traducidos al
       español con enlace a la configuración del negocio de Meta.
    5. **Plantillas** con estado (oculta las de ejemplo `jaspers_market_*`).
    Lo del periodo son las secciones 1 y 2; el resto es el estado actual.
    Se refresca cada minuto (tres consultas a la Graph API por carga).
  - Tarjeta **Sesión de Baileys**: número vinculado o QR, siempre visible.
- **Límites de las estadísticas de Meta** (probados el 2026-10-01):
  - Solo los **últimos 365 días** (error 2388336 si el inicio cae en el
    borde: se usa 365 días menos una hora). El endpoint responde 400 si el
    rango es más viejo.
  - Con granularidad mensual (`MONTH`/`MONTHLY`) Meta **omite el mes en
    curso**; por eso siempre se pide por día y se agrupa por mes en el
    navegador.
  - Los días de Meta empiezan a medianoche del Pacífico (zona de la cuenta,
    `timezone_id` 1); el dashboard asigna cada punto al día de su mediodía.
  - Los conteos llegan con unas horas de retraso.
  - Sin método de pago la cuenta no tiene moneda: el costo sale sin unidad.
- **Pruebas**: `entregables/OE3/pruebas/verificar_meta_whatsapp.py` (sin llamar a Meta;
  incluye los periodos inválidos de las métricas),
  más las regresiones `verificar_robustez_bot.py` y
  `verificar_celular_registro.py`. Todas pasan.
- Versión de la Graph API: **v25.0** (la que usa hoy el panel de Meta).

## 3. Cuenta de Meta configurada

| Dato | Valor |
|---|---|
| App de Meta | **Lacs**, id `28579707395027686`, sin publicar |
| Portafolio de negocio | Lacs |
| Caso de uso | "Conectar en WhatsApp", agregado el 30/09 |
| Cuenta de WhatsApp Business | "Test WhatsApp Business Account", id `2431567054034939` |
| Número | +1 555-173-6097 (número de prueba de Meta), Phone Number ID `1342307528964727` |
| Usuario del sistema | `lacs_bot`, token sin vencimiento con `whatsapp_business_management` y `whatsapp_business_messaging` |
| Destinatario de prueba verificado | +57 304 524 6117 (Habid) |
| Plantilla | `lacs_aviso`, Utilidad, español (`es`), id `1795743254780931`, **en revisión** |

`business_management` no aparece en los permisos del token porque la app no
tiene el caso de uso que lo trae. No hace falta para enviar ni para leer la
cuenta; solo impide que el token liste los portafolios (por eso los ids se
copiaron a mano del panel).

Variables en `.env` (valores fuera de este documento): `WHATSAPP_PROVEEDOR_DEFAULT`,
`META_WA_TOKEN`, `META_WA_PHONE_NUMBER_ID`, `META_WA_WABA_ID`,
`META_APP_SECRET`, `META_WEBHOOK_VERIFY_TOKEN`, `META_GRAPH_VERSION`,
`META_PLANTILLA_AVISO`, `META_PLANTILLA_IDIOMA`.

## 4. Pruebas contra Meta

1. El primer token era temporal (de usuario, vencía en ~1 h) y solo traía
   `whatsapp_business_messaging`. Se reemplazó por el del usuario del sistema.
2. Primer envío de `hello_world`: error `#133010 Account not registered`.
   El registro del número por la API (`POST /<PHONE_NUMBER_ID>/register`)
   fija un PIN de verificación en dos pasos y lo bloqueó el permiso
   automático de Claude Code. Se destrabó desde el panel: "Generar token" y
   "Enviar mensaje" en **Paso 1. Pruébalo**.
3. Segundo envío con el token del usuario del sistema: **200 aceptado**,
   `hello_world` llegó a Habid.

## 5. Lo que falta

1. **Aprobación de `lacs_aviso`.** Revisar su estado (WhatsApp Manager →
   Plantillas). Sin ella, los avisos a quien no escribió en 24 h no salen.
2. ~~**Probar texto libre desde el código del bot.**~~ Hecho el 2026-10-01. Habid le escribe "hola"
   al +1 555 173 6097 (abre la ventana de 24 h en Meta) y se llama a la rama
   Meta de `whatsapp_client`.
3. **URL pública con HTTPS** para `/webhook/meta`. Hecho el 2026-10-01:
   contenedor `lacs_tunel` (quick tunnel de Cloudflare, fuera del
   `docker-compose.yml` y del repo, sin credenciales) apuntando a
   `dashboard-frontend:80`. Se creó con
   `docker run -d --name lacs_tunel --restart unless-stopped --network lacs-reservas-bot_backend cloudflare/cloudflared:latest tunnel --no-autoupdate --url http://dashboard-frontend:80`.
   La URL (`docker logs lacs_tunel 2>&1 | grep trycloudflare`) **cambia si el
   contenedor se reinicia**: hay que actualizarla en Meta. Para una fija hace
   falta un dominio en Cloudflare.
4. ~~**Registrar el webhook en Meta**~~ Hecho el 2026-10-01: Meta verificó
   la URL; el panel dejó la suscripción sin campos y se agregó `messages`
   por la API; la app Lacs quedó en `subscribed_apps` de la cuenta. Un
   "hola" de Habid llegó a las 14:38 con firma válida, **con la app sin
   publicar**. Publicarla hará falta para el número real.
5. ~~**Prueba completa por Meta**~~ Hecha el 2026-10-01: cambiar el canal en Servicios y hacer
   registro, reserva y avisos.
6. **Número real de la LACS.** El 573245927008 está en la app Business y
   vinculado a Baileys. Para usarlo en la Cloud API hay que agregarlo con la
   opción de **coexistencia** (si Meta la ofrece) o usar otro número. Para
   producción también hacen falta la verificación del negocio y un método de
   pago en la cuenta.
7. ~~**Soporte completo de BSUID**~~ Hecho el 2026-10-01, sin columna nueva
   (`telefono_wa` y `jid_wa` se ensancharon; ver `PLAN_meta_completo_2026-10-01.md`).

## 6. Catálogo de WhatsApp (intentado, no resuelto)

Objetivo: que un equipo creado en el dashboard aparezca solo en el catálogo
del perfil de WhatsApp Business. Plan:
[PLAN_catalogo_whatsapp_2026-09-30.md](PLAN_catalogo_whatsapp_2026-09-30.md).

- Con Baileys 6.7.24, WhatsApp no respondía ninguna consulta de catálogo.
- Con Baileys 7, listar el catálogo funciona (vacío), pero crear un producto
  sin imagen falla en las tres variantes de precio: WhatsApp contesta sin el
  producto. Lo más probable es que la imagen sea obligatoria.
- Por la API oficial (`POST /<CATALOG_ID>/products`) la imagen
  (`image_url`) es **obligatoria**, igual que el precio.
- Los endpoints `/catalog` y `/catalog/product` quedaron en
  `baileys-bot/index.js` sin uso desde Flask. Se retoma cuando se defina lo
  de las imágenes.
