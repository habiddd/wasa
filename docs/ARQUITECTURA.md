# Arquitectura

## Flujo de un mensaje entrante

1. Usuario escribe por WhatsApp al número de la CPA.
2. `baileys-bot` (servicio Node.js sobre `@whiskeysockets/baileys`, WhatsApp
   Web no oficial) recibe el mensaje vía `messages.upsert` y lo reenvía a
   `POST /webhook/whatsapp` en Flask, con el `remoteJid` completo tal cual
   lo entrega Baileys (ver nota sobre JIDs `@lid` más abajo).
3. Flask, en orden:
   - Aplica **rate limiting** por número (`guardrails.excede_limite_mensajes`).
   - Aplica **filtrado de entrada** contra mensajes sospechosos/inyección
     de prompt (`guardrails.mensaje_es_sospechoso`).
   - Clasifica el **intent** (`llm_client.clasificar_intent`) y lo valida
     contra la whitelist (`guardrails.intent_permitido`).
   - Ejecuta la lógica de negocio correspondiente (reservar, consultar,
     cancelar) — pendiente de implementar en detalle.
   - Genera la respuesta (hoy delega en el LLM local; en producción,
     buena parte de las respuestas deberían ser plantillas fijas +
     datos reales, y el LLM se reserva para interpretar lenguaje natural
     libre, no para "inventar" el contenido de la respuesta).
   - Envía la respuesta de vuelta por `whatsapp_client.enviar_mensaje_texto`,
     que hace `POST /send` al servicio `baileys-bot`.
   - Registra la interacción completa en `auditoria_bot`.
4. `baileys-bot` reporta de forma asíncrona el estado de entrega
   (server-ack/delivery-ack/leído) vía `POST /webhook/ack`, correlacionado
   por `mensaje_id` (ver `guardrails.actualizar_estado_envio`).

`flask-app/app/evolution_client.py` es un cliente para Evolution API que
quedó del scaffold original y ya no se usa — el canal activo hoy es
Baileys directo (`whatsapp_client.py`), documentado en
`docs/SESION_2026-08-10.md`.

## Por qué Baileys directo (sin Evolution API de por medio)

- Es gratuito y no requiere levantar/mantener una instancia aparte de
  Evolution API.
- Te da reconexión automática y gestión de sesión de WhatsApp (la sesión
  persiste en el volumen `baileys_sessions`) sin que tengas que
  mantenerlo tú a mano.
- Permite correr en modo no oficial (gratis, rápido de arrancar) mientras
  tramitas la verificación de negocio de Meta, sin bloquear tu desarrollo.
- El costo es que Flask queda acoplado directamente al formato de eventos
  de Baileys en vez de a un formato intermediario estable — si más
  adelante se migra a Meta Cloud API, `webhook.py`/`whatsapp_client.py`
  necesitan un adaptador.

## JIDs `@lid` — por qué no se puede reconstruir el destino desde el número

WhatsApp no siempre identifica una conversación con el JID clásico
`<numero>@s.whatsapp.net`; algunas cuentas usan direccionamiento
`<id>@lid` ("linked ID"). Baileys entrega el `remoteJid` tal cual sea uno
u otro.

Si el pipeline recorta el `remoteJid` a solo los dígitos (por ejemplo,
para usarlo como identificador de rate limiting o como `telefono_wa` en
`auditoria_bot`) y luego reconstruye el JID de salida asumiendo siempre
`@s.whatsapp.net`, el mensaje se manda a un JID que no es el de la
conversación real. Baileys no valida que el JID exista: `sendMessage`
devuelve éxito igual, pero WhatsApp nunca entrega nada. Esto pasó en
producción (ver `docs/SESION_2026-08-24.md`) y quedó silencioso durante
semanas porque no genera ningún error en los logs.

Por eso `webhook.py` guarda dos cosas por separado:
- `numero` (dígitos, solo para rate limiting/auditoría — no sirve para
  responder).
- `remote_jid` (el JID completo tal cual llegó — el único que hay que
  usar para enviar la respuesta, vía `whatsapp_client.enviar_mensaje_texto`
  → `baileys-bot` `POST /send` con `{"jid": ...}`).

## Por qué separar el LLM de la lógica de reservas

El LLM (`llm_client.py`) debería usarse para:
- Interpretar lenguaje natural ambiguo ("necesito una cámara para el
  viernes en la tarde" → intent + entidades).
- Redactar respuestas naturales a partir de datos ya verificados en la
  base de datos.

El LLM **no** debería:
- Decidir por sí solo si hay disponibilidad (eso lo valida SQL contra
  `reservas`, no una alucinación del modelo).
- Ejecutar acciones directamente (crear/cancelar reservas) sin pasar por
  la whitelist de intents y la lógica de negocio explícita.

## Analítica en tiempo real

Los endpoints en `app/routes/analytics.py` consultan directamente la
tabla `reservas`. Para volumen bajo (el caso típico de una CPA
universitaria) esto es suficiente sin necesidad de un pipeline de datos
aparte. Si más adelante el volumen crece, considera:
- Vistas materializadas en Postgres para los agregados más pedidos.
- Un job periódico que precalcule métricas en vez de calcularlas on-demand.
