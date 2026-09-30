# WhatsApp por la API oficial de Meta: estado al 2026-09-30

Registro de lo que se hizo el 30 de septiembre de 2026 para que el bot de
reservas de la LACS pueda usar la **WhatsApp Cloud API de Meta** además de
Baileys, qué quedó funcionando y qué falta. El paso a paso completo, para
repetirlo desde cero, está en [GUIA_META_WHATSAPP.md](GUIA_META_WHATSAPP.md).

Nada de esto tiene commit todavía (rama `dev`).

## Resumen

| Tema | Estado |
|---|---|
| Baileys actualizado a 7.0.0-rc14 | ✅ Conectado sin QR nuevo |
| Código listo para Meta (envío, webhook, selector) | ✅ Desplegado, pruebas pasan |
| Credenciales de Meta en `.env` | ✅ Completas |
| Envío por la API oficial | ✅ Probado (`hello_world` llegó a Habid) |
| Plantilla `lacs_aviso` para avisos fuera de 24 h | ⏳ Creada, en revisión de Meta |
| Recibir mensajes por Meta (webhook) | ❌ Falta una URL pública con HTTPS |
| Número real de la LACS en la Cloud API | ❌ Hoy se usa el número de prueba de Meta |
| Canal activo | **Baileys** (Meta configurado pero sin activar) |
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
  - Texto libre si la persona escribió en las últimas 24 h (Redis
    `meta:ventana:<numero>` o la auditoría).
  - Si no, la plantilla `META_PLANTILLA_AVISO` con el texto en un solo
    parámetro (saltos de línea cambiados por " · ", máximo 1000 caracteres).
  - A un usuario que solo tiene JID `@lid` (sin número) no se le puede
    escribir por Meta: se registra el error.
- **Recepción** (`flask-app/app/routes/webhook.py`):
  - `GET /webhook/meta`: verificación con `META_WEBHOOK_VERIFY_TOKEN`.
  - `POST /webhook/meta`: valida la firma `X-Hub-Signature-256` con
    `META_APP_SECRET`, descarta repetidos por `wamid` (Meta reintenta hasta
    36 h) y pasa cada texto al mismo flujo del bot que usa Baileys.
    Los estados de entrega se mapean a la auditoría (sent → `server_ack`,
    delivered → `delivery_ack`, read → `leido`, failed → `fallido`).
  - Solo se atienden mensajes del canal activo; el otro canal se ignora.
  - Quien escribe con nombre de usuario (sin número, solo BSUID) recibe un
    mensaje pidiéndole su número. El soporte completo de BSUID necesita una
    columna nueva en `usuarios` y quedó fuera.
- **nginx** (`dashboard-frontend/nginx.conf`): expone solo
  `location = /webhook/meta` hacia Flask, sin usuario/contraseña (lo protege
  la firma). El resto de `/webhook/` sigue sin exponerse.
- **Dashboard**: tarjeta "Canal de WhatsApp" en Servicios → "QR / WhatsApp",
  con botones Baileys | Meta, estado de cada canal (Meta: número, nombre
  verificado y calidad) y la lista de variables faltantes. Endpoint
  `GET/PUT /dashboard/api/whatsapp/proveedor`.
- **Pruebas**: `flask-app/verificar_meta_whatsapp.py` (sin llamar a Meta),
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
2. **Probar texto libre desde el código del bot.** Habid le escribe "hola"
   al +1 555 173 6097 (abre la ventana de 24 h en Meta) y se llama a la rama
   Meta de `whatsapp_client`.
3. **URL pública con HTTPS** para `/webhook/meta`. Propuesta: túnel de
   Cloudflare (`cloudflared`) en un contenedor apuntando a nginx; publica el
   dashboard (con usuario/contraseña) y `/webhook/meta`. La versión gratis
   cambia de URL al reiniciar; para una fija hace falta un dominio en
   Cloudflare. Pendiente de decisión del usuario.
4. **Registrar el webhook en Meta**: URL de devolución de llamada, verify
   token, campo `messages`, y suscribir la app a la cuenta de WhatsApp
   (`POST /<WABA_ID>/subscribed_apps`; hoy solo está suscrita la app interna
   de pruebas de Meta).
5. **Prueba completa por Meta**: cambiar el canal en Servicios y hacer
   registro, reserva y avisos.
6. **Número real de la LACS.** El 573245927008 está en la app Business y
   vinculado a Baileys. Para usarlo en la Cloud API hay que agregarlo con la
   opción de **coexistencia** (si Meta la ofrece) o usar otro número. Para
   producción también hacen falta la verificación del negocio y un método de
   pago en la cuenta.
7. **Soporte completo de BSUID** (usuarios con nombre de usuario): columna
   nueva en `usuarios`.
8. **Commits** de todo lo de hoy.

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
