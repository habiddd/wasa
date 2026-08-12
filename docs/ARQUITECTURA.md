# Arquitectura

## Flujo de un mensaje entrante

1. Usuario escribe por WhatsApp al número de la CPA.
2. Evolution API (Baileys o Meta Cloud API, según config) recibe el
   mensaje y lo reenvía a `POST /webhook/whatsapp` en Flask.
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
   - Envía la respuesta de vuelta por `evolution_client.enviar_mensaje_texto`.
   - Registra la interacción completa en `auditoria_bot`.

## Por qué Evolution API como intermediario

- Abstrae el canal: mismo webhook, mismo formato de payload, sin importar
  si por debajo hay Baileys o Meta Cloud API.
- Te da reconexión automática y gestión de sesión de WhatsApp sin que
  tengas que mantenerlo tú a mano.
- Permite correr en modo no oficial (gratis, rápido de arrancar) mientras
  tramitas la verificación de negocio de Meta, sin bloquear tu desarrollo.

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
