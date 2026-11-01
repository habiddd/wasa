# OE2 — Implementar el asistente conversacional sobre WhatsApp con clasificación de intenciones

**Repositorio:** [github.com/habiddd/wasa](https://github.com/habiddd/wasa)

## Entregable 1: servicio de mensajería integrado con la pasarela de WhatsApp, en operación y con sesión persistente

| Ruta | Qué hace |
|---|---|
| `baileys-bot/index.js` | Pasarela de WhatsApp (Baileys). Reenvía cada mensaje entrante a Flask por webhook y los acuses de entrega a `/webhook/ack`. La sesión vinculada persiste en un volumen de Docker. |
| `flask-app/app/routes/webhook.py` | Webhooks `/webhook/whatsapp` (Baileys) y `/webhook/meta` (API oficial de Meta, con firma verificada). Ambos llegan al mismo flujo `_procesar_mensaje`. |
| `flask-app/app/whatsapp_client.py` | Envío de respuestas por el canal activo. El canal se cambia desde el dashboard sin reiniciar. |
| `flask-app/app/sesiones.py` | Memoria de la conversación en Redis. |

La API oficial de Meta, que el anteproyecto dejaba como migración futura, ya
está integrada como segundo proveedor (ver `docs/GUIA_META_WHATSAPP.md`).

## Entregable 2: módulo de clasificación de intenciones con lista blanca y prompt versionado

| Ruta | Qué hace |
|---|---|
| `flask-app/app/llm_client.py` | Clasificador en tres capas: saludos y cortesías sin consultar al modelo, modelo de lenguaje por una API compatible con OpenAI (`PROMPT_CLASIFICACION`, el prompt del sistema, versionado aquí) y heurístico de respaldo si el modelo no responde. Funciona con un modelo local pequeño (`qwen3-4b-2507` en LM Studio, 109/112) o con uno grande por internet (en uso: `deepseek/deepseek-v4-flash-0731` por OpenRouter, 111/112); la dirección, el modelo, la clave y la especialización `local` u `online` se cambian desde el back office (`config_llm`), sin tocar código. Intenciones válidas: `INTENTS_VALIDOS`. |
| `flask-app/app/guardrails.py` | `intent_permitido`: lista blanca (`ALLOWED_INTENTS` en `config.py`). Lo que no está en la lista va al menú de opciones, nunca a una acción. |
| `flask-app/app/menu_service.py` | Menú de respaldo cuando el mensaje no se entiende. |

Intenciones: `reservar`, `consultar_disponibilidad`, `cancelar`, `ayuda`,
`saludo` y `otro`. Las tres primeras **no** usan el modelo para responder: la
respuesta sale de consultas a la base de datos. El diseño se explica en
`docs/ARQUITECTURA.md` (secciones "Por qué separar el LLM de la lógica de
reservas" y "Clasificación de intent en tres capas", con la comparación de
modelos que llevó a deepseek).

## Entregable 3: registro de pruebas de interpretación y correcciones aplicadas

[REGISTRO_PRUEBAS_INTERPRETACION.md](REGISTRO_PRUEBAS_INTERPRETACION.md)

Nueve rondas. Las ocho primeras las hizo el practicante. La novena, del 28 de
octubre de 2026, fue la de cuatro conocidos del practicante, que con él, que
participó desde las rondas 2 y 3, completan los cinco usuarios piloto. Los cuatro
se registraron por WhatsApp, dos de
ellos hicieron una reserva, y los 44 mensajes que enviaron recibieron la
intención correcta.
