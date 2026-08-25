# Arquitectura

## Flujo de un mensaje entrante

1. Usuario escribe por WhatsApp al número de la CPA.
2. `baileys-bot` (servicio Node.js sobre `@whiskeysockets/baileys`, WhatsApp
   Web no oficial) recibe el mensaje vía `messages.upsert` y lo reenvía a
   `POST /webhook/whatsapp` en Flask, con el `remoteJid` completo tal cual
   lo entrega Baileys (ver nota sobre JIDs `@lid` más abajo).
3. Flask, en orden (ver `routes/webhook.py:recibir_webhook`):
   - Aplica **rate limiting** por número (`guardrails.excede_limite_mensajes`).
   - Aplica **filtrado de entrada** contra mensajes sospechosos/inyección
     de prompt (`guardrails.mensaje_es_sospechoso`).
   - Resuelve/crea el **Usuario** por `telefono_wa` (`usuarios_service`) y,
     si su registro no está completo, el mensaje se trata como respuesta a
     la siguiente pregunta pendiente del alta (`registro_service`) --
     ver "Identidad y registro" más abajo. Esto pasa *antes* de clasificar
     intent a propósito.
   - Si hay una **reserva pendiente de confirmación** en la sesión del
     usuario (ver "Memoria de conversación"), el mensaje se trata como la
     respuesta sí/no a esa confirmación (`recursos_service.confirmar_reserva_pendiente`),
     también antes de clasificar intent.
   - Clasifica el **intent** (`llm_client.clasificar_intent`) y lo valida
     contra la whitelist (`guardrails.intent_permitido`).
   - Ejecuta la lógica de negocio correspondiente:
     - `consultar_disponibilidad` y `reservar` van a `recursos_service.py`,
       que consulta `recursos`/`reservas` de verdad -- nunca al LLM en
       modo libre (ver "Por qué separar el LLM de la lógica de reservas").
     - `cancelar` y `ayuda` siguen delegando en el LLM conversacional
       (`llm_client.generar_respuesta`) -- `cancelar` con lógica real
       (identificar y anular una reserva existente) queda pendiente.
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

Esto ya está implementado así: `llm_client.extraer_datos_reserva` le pide
al LLM que devuelva `{recurso, inicio, fin}` como JSON a partir del texto
libre del usuario, pero `recursos_service.py` es quien valida ese
`recurso` contra el catálogo real (si el LLM alucina un nombre que no
existe, se descarta y cae al matcher por palabras clave), quien consulta
disponibilidad, y quien decide si se puede escribir en `reservas`. El LLM
nunca toca la base de datos directamente.

## Disponibilidad de activos y reservas (`recursos_service.py`)

- `buscar_recursos_en_texto` matchea el texto del usuario contra
  `recursos.nombre` por score de palabras (no boolean simple), para que
  una palabra genérica compartida entre varios activos (ej. "cámara" en
  dos modelos) no gane sobre una palabra distintiva (ej. "sony") que solo
  aparece en uno. Cuando el único match viene de una palabra genérica sin
  nombre completo ni segunda palabra distintiva, se marca como
  `es_match_debil` y la respuesta incluye una salvedad ("si no te
  refieres a X, dime el nombre completo") -- es una mitigación, no una
  solución perfecta: sigue siendo posible un falso positivo tipo "grúa de
  cámara" (que no existe) matcheando con una cámara real solo por la
  palabra compartida.
- `evaluar_consulta_disponibilidad` (intent `consultar_disponibilidad`)
  lista el **estado de cada coincidencia** (o de todo el catálogo si no
  identifica un activo específico) -- el usuario quiere saber qué hay y
  qué no, así que nunca pregunta "¿cuál de estos?".
- `gestionar_reserva` (intent `reservar`) sí necesita que el usuario elija
  exactamente un activo, así que ante varias coincidencias pregunta cuál.
  Una vez resuelto el activo, si `llm_client.extraer_datos_reserva` no
  devolvió fecha/hora, responde con el estado actual y pide que la
  incluya en el mismo mensaje -- el bot no tiene memoria de conversación
  para completar eso en un turno posterior (salvo la reserva pendiente de
  confirmación, ver más abajo).
- Toda hora se interpreta en `America/Bogota` (zona horaria fija de la
  CPA) y se guarda en UTC (columnas `TIMESTAMPTZ`).
- **La garantía real contra choques de horario es de Postgres, no de la
  app**: `postgres/init/02-schema.sql` agrega la extensión `btree_gist` y
  una restricción `EXCLUDE USING gist (recurso_id WITH =, tstzrange(inicio,
  fin) WITH &&) WHERE (estado = 'confirmada')` sobre `reservas`. Postgres
  rechaza cualquier INSERT/UPDATE que se solape con una reserva confirmada
  existente del mismo recurso, sin importar si la vía es la app, una
  migración, un script manual, o dos requests concurrentes que pasaron el
  chequeo de la aplicación al mismo tiempo. `crear_reserva` en
  `recursos_service.py` solo atrapa el `IntegrityError` que Postgres lanza
  si eso pasa; el chequeo previo con `_reserva_solapada` es solo para dar
  un mensaje más claro antes de intentar el INSERT, no la garantía de
  consistencia.

## Confirmación antes de reservar

`gestionar_reserva` nunca escribe en `reservas` directamente. Si activo +
fecha/hora quedan resueltos y sin conflicto, en vez de reservar manda un
**resumen** (activo, nombre completo de quien reserva, fecha/hora de
inicio y fin) y dice "¿confirmas?", guardando los datos en la sesión de
Redis del usuario bajo `reserva_pendiente`. El siguiente mensaje se
intercepta en `webhook.py` *antes* de clasificar intent (un simple "sí" se
clasificaría como intent "otro" y quedaría bloqueado por la whitelist) y
va a `confirmar_reserva_pendiente`, que:
- Si el usuario confirma: re-valida que la fecha no haya pasado y que no
  haya un choque nuevo (por si alguien más reservó mientras esperaba la
  respuesta), y recién ahí llama a `crear_reserva`.
- Si cancela: limpia la sesión sin tocar la base de datos.
- Si la respuesta no es reconocible como sí/no: vuelve a preguntar sin
  perder el estado pendiente.

## Identidad y registro (`registro_service.py`, `usuarios_service.py`)

El primer mensaje de un número nuevo crea su fila de `Usuario` vacía
(`registro_completo=False`, sin nombre/código/cédula). Mientras no esté
completo, **cualquier mensaje** se trata como respuesta a la siguiente
pregunta pendiente del alta -- nombre completo, código/id de estudiante,
cédula, en ese orden -- sin pasar por clasificación de intent, por la
misma razón que la confirmación de reserva: "Juan Pérez" como respuesta a
"¿cuál es tu nombre?" no es un intent de reserva/consulta/ayuda.

El número de celular no se pregunta -- ya se conoce, es `telefono_wa`, el
canal por el que está escribiendo.

Cada respuesta válida se guarda en Postgres al instante (no solo en la
sesión), así que el flujo es resistente a que la sesión de Redis expire o
se pierda a mitad de registro: el siguiente mensaje recalcula el campo que
realmente falta contra la fila real de `Usuario` en vez de asumir ciegamente
que responde a lo último que se preguntó.

## Memoria de conversación (`sesiones.py`)

Cada webhook HTTP es una request sin estado, pero el registro paso a paso
y la confirmación de reserva necesitan recordar en qué punto de la
conversación se quedaron entre un mensaje y el siguiente. `sesiones.py`
guarda un blob JSON pequeño por número de WhatsApp en Redis
(`sesion:<telefono_wa>`), con TTL de 7 días que se renueva en cada
lectura/escritura -- una conversación activa no se corta a media
pregunta, y una inactiva por más de una semana se limpia sola sin
necesidad de un job de limpieza aparte.

Es un punto de extensión genérico: cualquier flujo futuro que necesite
recordar contexto entre mensajes (ej. una aclaración de "¿cuál de estos
recursos?" respondida en un mensaje separado) puede guardar su propio
estado ahí bajo su propia clave, igual que `registro_campo_pendiente` y
`reserva_pendiente`.

## Analítica en tiempo real

Los endpoints en `app/routes/analytics.py` consultan directamente la
tabla `reservas`. Para volumen bajo (el caso típico de una CPA
universitaria) esto es suficiente sin necesidad de un pipeline de datos
aparte. Si más adelante el volumen crece, considera:
- Vistas materializadas en Postgres para los agregados más pedidos.
- Un job periódico que precalcule métricas en vez de calcularlas on-demand.
