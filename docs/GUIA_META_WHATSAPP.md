# Guía: conectar el bot de la LACS a la API oficial de WhatsApp (Meta)

Paso a paso para dejar el bot enviando y recibiendo mensajes por la
**WhatsApp Cloud API de Meta**, y para elegir entre Meta y Baileys desde el
dashboard. Sirve para configurarlo desde cero o para rehacerlo en otra cuenta.
El estado de lo hecho está en
[META_WHATSAPP_ESTADO_2026-09-30.md](META_WHATSAPP_ESTADO_2026-09-30.md).

Toda credencial va en `.env` (en la raíz del proyecto, ignorado por git).
Nunca en el código ni en documentos.

## 0. Antes de empezar

- Una cuenta de Facebook con acceso de administrador al **portafolio de
  negocio** de la LACS en [business.facebook.com](https://business.facebook.com).
- Acceso a [developers.facebook.com](https://developers.facebook.com).
- El proyecto corriendo con Docker (`docker compose up -d`).
- Un celular con WhatsApp para recibir las pruebas.

Dos reglas de Meta que condicionan todo:
- **Ventana de 24 h.** Se puede mandar texto libre solo si la persona le
  escribió al número en las últimas 24 h. Fuera de eso, solo **plantillas
  aprobadas**. El bot lo resuelve solo (ver paso 7), pero la plantilla tiene
  que existir y estar aprobada.
- **URL pública con HTTPS.** Meta solo manda los mensajes entrantes a una URL
  pública con certificado válido.

## 1. Crear la app y agregar WhatsApp

1. developers.facebook.com → **Mis apps** → **Crear app** → tipo **Negocio**,
   ligada al portafolio de la LACS. (La actual se llama **Lacs**.)
2. En el menú de la app → **Casos de uso** → **Agregar caso de uso** →
   **"Conectar con clientes a través de WhatsApp"**.
3. Elegir el portafolio de la LACS → **Continuar**. Meta crea una cuenta de
   WhatsApp Business de prueba y un **número de prueba** (solo puede
   escribirles a hasta 5 números verificados).

## 2. Anotar los ids

En **Casos de uso → Conectar en WhatsApp → Paso 1. Pruébalo** (o
"Configuración de la API"):

| Dato en el panel | Variable en `.env` |
|---|---|
| Phone Number ID (identificador del número) | `META_WA_PHONE_NUMBER_ID` |
| Identificador de la cuenta de WhatsApp Business | `META_WA_WABA_ID` |

Ojo: el **identificador de la app** (App ID) es otro número distinto y no va
en ninguna de las dos.

En esa misma pantalla, en **Destinatario**, agregar el celular de pruebas y
verificarlo con el código que llega por WhatsApp.

## 3. Token permanente (usuario del sistema)

El botón "Generar token" del panel y el Graph API Explorer dan tokens
**temporales** (vencen en horas). Para el bot hace falta uno permanente:

1. business.facebook.com → **Configuración del negocio** → **Usuarios →
   Usuarios del sistema** → **Agregar**. Nombre `lacs_bot`, rol
   **Administrador**.
2. Con el usuario seleccionado → **Asignar activos**:
   - **Apps** → la app → control total.
   - **Cuentas de WhatsApp** → la cuenta del paso 1 → control total.
     (Si Meta crea otra cuenta más adelante, hay que asignarla también; el
     mismo token empieza a verla sin regenerarlo.)
3. **Generar nuevo token** → app → **Caducidad: Nunca** → permisos
   `whatsapp_business_management` y `whatsapp_business_messaging`
   (`business_management` también, si aparece; no es obligatorio).
4. Copiarlo (Meta lo muestra una sola vez) → `META_WA_TOKEN`.

## 4. App Secret

developers.facebook.com → la app → **Configuración de la app → Básica** →
"Clave secreta de la app" → **Mostrar** → `META_APP_SECRET`.
Con esta clave Flask comprueba que cada webhook viene de verdad de Meta.

## 5. Verify token del webhook

Una cadena larga al azar que inventa uno mismo, por ejemplo:

```
docker exec lacs_flask_app python -c "import secrets;print(secrets.token_urlsafe(32))"
```

→ `META_WEBHOOK_VERIFY_TOKEN`. El mismo valor se pega en el panel de Meta en
el paso 9.

## 6. Activar el número de prueba

La primera vez, el número de prueba puede responder
`#133010 Account not registered`. Para destrabarlo:

1. En **Paso 1. Pruébalo** → **Generar token** (temporal, solo para esa
   página) → **Enviar mensaje** al destinatario verificado. Ese token
   **no** va en `.env`: es de usuario y vence en horas.
2. Si aun así sale `133010`, registrarlo por la API con un PIN de 6 dígitos
   (queda como verificación en dos pasos del número; anotarlo en `.env` como
   `META_WA_PIN`):
   `POST https://graph.facebook.com/v25.0/<PHONE_NUMBER_ID>/register`
   con `{"messaging_product": "whatsapp", "pin": "<PIN>"}`.

## 7. Plantilla para los avisos

Los avisos automáticos del bot (recordatorio, "ya es la hora", no-show,
devolución, aprobación o rechazo de la LACS) suelen salir cuando la persona
no ha escrito en 24 h. Para eso el bot usa **una plantilla genérica** con el
texto del aviso como parámetro.

1. business.facebook.com → **WhatsApp Manager → Administrar plantillas →
   Crear plantilla**.
2. Categoría **Utilidad**, nombre `lacs_aviso`, idioma **Español**.
3. Cuerpo:
   > Hola, tienes una novedad sobre tu reserva en la LACS: {{1}} Si tienes dudas, responde a este mensaje.
4. Ejemplo de `{{1}}`: `tu reserva N.º 12 fue confirmada para el jueves 01/10 a las 9:00 a.m.`
5. Enviar y esperar **Aprobada**.
6. En `.env`: `META_PLANTILLA_AVISO=lacs_aviso` y `META_PLANTILLA_IDIOMA` con
   el código exacto del idioma de la plantilla (`es`, `es_CO`, `es_MX`…).

También se puede crear por la API:
`POST https://graph.facebook.com/v25.0/<WABA_ID>/message_templates` con
`name`, `language`, `category: "UTILITY"` y el componente `BODY` con su
`example`.

## 8. URL pública con HTTPS

nginx (contenedor `dashboard-frontend`, puerto 8080 del host) ya expone solo
`/webhook/meta` hacia Flask. Falta que esa ruta sea accesible desde internet
con HTTPS:

- **Dominio propio** apuntando al servidor, con certificado válido (por
  ejemplo detrás de Cloudflare o de un proxy con Let's Encrypt), o
- **Túnel de Cloudflare** (`cloudflared`) apuntando a
  `http://dashboard-frontend:80`. Sin cuenta ("quick tunnel") la URL cambia
  en cada reinicio; con un dominio en Cloudflare es fija.

Quick tunnel (lo que se usa hoy). Es un contenedor aparte, fuera de
`docker-compose.yml` y del repo, y no necesita credenciales:

```
docker run -d --name lacs_tunel --restart unless-stopped --network lacs-reservas-bot_backend cloudflare/cloudflared:latest tunnel --no-autoupdate --url http://dashboard-frontend:80
docker logs lacs_tunel 2>&1 | grep trycloudflare
```

El segundo comando muestra la URL (`https://<algo>.trycloudflare.com`). Si
el contenedor se reinicia (o Docker), la URL cambia y hay que actualizarla
en Meta (paso 9).

Comprobar antes de ir a Meta (debe responder `12345`; con un token falso,
403):

```
curl "https://<url>/webhook/meta?hub.mode=subscribe&hub.verify_token=<META_WEBHOOK_VERIFY_TOKEN>&hub.challenge=12345"
```

**No** apuntar el túnel directo a `flask-app:5000`: expondría
`/webhook/whatsapp` (Baileys), que no tiene autenticación. A través de nginx
esa ruta da 404.

La URL final del webhook es `https://<dominio>/webhook/meta`.

## 9. Registrar el webhook en Meta

1. developers.facebook.com → la app → **Casos de uso → Conectar en WhatsApp →
   Configuración** (o WhatsApp → Configuración).
2. **URL de devolución de llamada**: `https://<dominio>/webhook/meta`.
   **Token de verificación**: el `META_WEBHOOK_VERIFY_TOKEN`.
   → **Verificar y guardar**. Meta hace un `GET` y Flask responde el
   `hub.challenge` si el token coincide.
3. **Campos del webhook** → suscribirse a **messages**. **Ojo:** el
   "Verificar y guardar" de **Paso 2: Configuración de producción** guarda
   la URL pero puede dejar la suscripción **sin ningún campo**, y entonces
   no llega nada. Comprobarlo con el token de la app
   (`<APP_ID>|<META_APP_SECRET>`):
   `GET https://graph.facebook.com/v25.0/<APP_ID>/subscriptions?access_token=<APP_ID>|<APP_SECRET>`
   → en `fields` debe aparecer `messages`. Si sale vacío, suscribirlo por
   la API (mismos URL y verify token):
   `POST https://graph.facebook.com/v25.0/<APP_ID>/subscriptions` con
   `object=whatsapp_business_account`, `callback_url=<URL>`,
   `verify_token=<META_WEBHOOK_VERIFY_TOKEN>`, `fields=messages` y el mismo
   `access_token`.
4. Suscribir la app a la cuenta de WhatsApp:
   `POST https://graph.facebook.com/v25.0/<WABA_ID>/subscribed_apps` con el
   token del usuario del sistema. Comprobar con un `GET` al mismo endpoint
   que la app (p. ej. **Lacs**) aparece en la lista; la "WA DevX Webhook
   Events 1P App" es la app de pruebas de Meta y no cuenta.
5. Probar: escribirle "hola" al número desde el celular verificado. En
   `docker logs lacs_dashboard_frontend` debe aparecer un
   `POST /webhook/meta ... 200` con agente `facebookexternalua`.

**App sin publicar.** El panel avisa que mientras la app no esté publicada
solo llegan los webhooks de prueba. En la práctica (2026-10-01), con el
número de prueba, los mensajes del destinatario verificado **sí llegaron**
sin publicar la app. Para el número real y usuarios cualquiera hay que
publicarla (pide URL de política de privacidad e ícono).

Los mensajes que llegan mientras no hay campo suscrito o el webhook está
caído se pierden (Meta no los reenvía después).

## 10. Cargar la configuración en el bot

`.env` completo:

```
WHATSAPP_PROVEEDOR_DEFAULT=baileys
META_WA_TOKEN=...
META_WA_PHONE_NUMBER_ID=...
META_WA_WABA_ID=...
META_APP_SECRET=...
META_WEBHOOK_VERIFY_TOKEN=...
META_GRAPH_VERSION=v25.0
META_PLANTILLA_AVISO=lacs_aviso
META_PLANTILLA_IDIOMA=es
```

Aplicar (un `restart` **no** relee el `.env`):

```
docker compose up -d flask-app
docker compose restart dashboard-frontend
```

El reinicio de `dashboard-frontend` hace falta cada vez que se recrea
`flask-app`: nginx guarda la IP vieja de Flask y si no se reinicia el
dashboard da error 502.

## 11. Comprobar y activar

1. Dashboard → **Servicios → QR / WhatsApp → Canal de WhatsApp**: Meta debe
   salir **configurado**, con el número, el nombre verificado y la calidad.
   Si falta algo, la tarjeta lista qué variables faltan.
2. Prueba de envío con la plantilla de ejemplo de Meta:
   ```
   curl -X POST "https://graph.facebook.com/v25.0/<PHONE_NUMBER_ID>/messages" \
     -H "Authorization: Bearer <META_WA_TOKEN>" -H "Content-Type: application/json" \
     -d '{"messaging_product":"whatsapp","to":"<NÚMERO>","type":"template","template":{"name":"hello_world","language":{"code":"en_US"}}}'
   ```
3. Cambiar el canal a **Meta** en la tarjeta (pide confirmación). Desde ese
   momento Flask ignora lo que llegue por Baileys y responde por Meta.
4. Escribirle "hola" al número de Meta y recorrer registro → reserva →
   cancelación. Revisar `auditoria_bot` (dashboard) para ver cada mensaje y
   su estado de entrega.
5. Para volver a Baileys, el mismo switch. Siempre está permitido.

## 12. Pasar al número real

1. developers.facebook.com → la app → **Paso 2: Configuración de producción**
   → agregar el número real de la LACS.
2. Si el número ya se usa en la app WhatsApp Business (hoy el 573245927008),
   hace falta la opción de **coexistencia** para usarlo en la app y en la
   API a la vez; si no, hay que usar otro número (sin WhatsApp activo).
3. Verificación del negocio y **método de pago** en la cuenta (las
   plantillas se cobran).
4. Cambiar `META_WA_PHONE_NUMBER_ID` (y `META_WA_WABA_ID` si cambia),
   asignar la cuenta nueva al usuario del sistema, crear o copiar la
   plantilla en esa cuenta, y repetir los pasos 9 a 11.

## Problemas conocidos

| Síntoma | Causa y solución |
|---|---|
| `#133010 Account not registered` | Número sin registrar en la Cloud API. Ver paso 6. |
| `#100 Missing Permission` al listar negocios | El token no tiene `business_management`. No hace falta: copiar los ids del panel. |
| Token que vence en horas | Es de usuario o del Explorer. Usar el del usuario del sistema (paso 3). |
| El token no ve la cuenta de WhatsApp | La cuenta no está asignada al usuario del sistema como activo. |
| Los avisos no salen por Meta | Plantilla sin aprobar o `META_PLANTILLA_AVISO` vacío; o el usuario solo tiene JID `@lid` sin número. |
| Error `131047` al enviar texto | Fuera de la ventana de 24 h: debe salir como plantilla. |
| Webhook verificado pero no llega ningún mensaje | La suscripción quedó sin el campo `messages`, o la app no está en `subscribed_apps` de la cuenta. Ver paso 9. |
| Dejó de llegar todo de un día para otro | Se reinició `lacs_tunel` y cambió la URL del quick tunnel. Sacar la nueva (paso 8) y actualizarla en Meta. |
| Meta no verifica el webhook | URL sin HTTPS válido, `META_WEBHOOK_VERIFY_TOKEN` distinto o Flask sin recrear tras editar `.env`. |
| Webhook responde 403 | Firma inválida: `META_APP_SECRET` no es el de la app que envía. |
| Personas sin número en el webhook | Usan nombre de usuario de WhatsApp; Meta manda solo su BSUID. El bot les pide el número. |
| Dashboard con error 502 tras recrear Flask | Reiniciar `dashboard-frontend`. |

## Referencias

- [Cloud API: get started](https://developers.facebook.com/docs/whatsapp/cloud-api/get-started)
- [Webhooks: verificación y firma](https://developers.facebook.com/docs/graph-api/webhooks/getting-started)
- [Componentes del webhook de WhatsApp](https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/components)
- [Plantillas](https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/overview)
- [Business-scoped user IDs (BSUID)](https://developers.facebook.com/documentation/business-messaging/whatsapp/business-scoped-user-ids/)
- [Migración a Baileys 7](https://baileys.wiki/migration/v7)
