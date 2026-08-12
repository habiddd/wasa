from flask import Blueprint, current_app, jsonify, request

from app import whatsapp_client, guardrails, llm_client, estado_bot

bp = Blueprint("webhook", __name__)


@bp.route("/webhook/qr", methods=["POST"])
def recibir_qr():
    """Recibe el código QR de Baileys para mostrarlo en el dashboard."""
    payload = request.get_json(silent=True) or {}
    estado_bot.guardar_qr(payload.get("qr"))
    return jsonify({"status": "ok"}), 200


@bp.route("/webhook/qr", methods=["GET"])
def obtener_qr():
    """Devuelve el último código QR recibido de Baileys."""
    return jsonify({"qr": estado_bot.obtener_qr()}), 200


def _extraer_mensaje(payload: dict) -> tuple[str | None, str | None]:
    """
    Evolution API envía distintos formatos de evento; nos interesa
    'messages.upsert'. Estructura típica:
    {
      "event": "messages.upsert",
      "instance": "cpa-uninorte",
      "data": {
        "key": {"remoteJid": "573001234567@s.whatsapp.net", "fromMe": false},
        "message": {"conversation": "hola"}
      }
    }
    Devuelve (numero_sin_sufijo, texto) o (None, None) si no aplica.
    """
    if payload.get("event") != "messages.upsert":
        return None, None

    data = payload.get("data", {})
    key = data.get("key", {})

    if key.get("fromMe"):
        return None, None  # ignorar mensajes enviados por el propio bot

    remote_jid = key.get("remoteJid", "")
    numero = remote_jid.split("@")[0] if "@" in remote_jid else remote_jid

    mensaje = data.get("message", {})
    texto = mensaje.get("conversation") or mensaje.get("extendedTextMessage", {}).get("text")

    return (numero or None), (texto or None)


@bp.route("/webhook/whatsapp", methods=["POST"])
def recibir_webhook():
    payload = request.get_json(silent=True) or {}
    numero, texto = _extraer_mensaje(payload)

    if not numero or not texto:
        # Eventos que no son mensajes de texto entrantes (status, conexión, etc.)
        return jsonify({"status": "ignorado"}), 200

    # --- Guardrail 1: rate limiting por número ---
    if guardrails.excede_limite_mensajes(numero):
        guardrails.registrar_interaccion(
            numero, texto, None, None, permitido=False,
            motivo_bloqueo="rate_limit_excedido",
        )
        return jsonify({"status": "rate_limited"}), 200

    # --- Guardrail 2: filtrado de entrada ---
    if guardrails.mensaje_es_sospechoso(texto):
        guardrails.registrar_interaccion(
            numero, texto, None, None, permitido=False,
            motivo_bloqueo="mensaje_sospechoso",
        )
        whatsapp_client.enviar_mensaje_texto(
            numero, "No puedo procesar ese mensaje. Escribe 'ayuda' para ver qué puedo hacer."
        )
        return jsonify({"status": "bloqueado"}), 200

    # --- Clasificación de intent + whitelist ---
    intent = llm_client.clasificar_intent(texto)
    if not guardrails.intent_permitido(intent):
        respuesta = (
            "Solo puedo ayudarte a reservar equipos/espacios, consultar "
            "disponibilidad o cancelar una reserva. Escribe 'ayuda' para más info."
        )
        whatsapp_client.enviar_mensaje_texto(numero, respuesta)
        guardrails.registrar_interaccion(
            numero, texto, intent, respuesta, permitido=False,
            motivo_bloqueo="intent_no_permitido",
        )
        return jsonify({"status": "intent_bloqueado"}), 200

    # --- TODO: aquí conectas la lógica real de reservas por intent ---
    # if intent == "reservar": ...
    # if intent == "consultar_disponibilidad": ...
    # Por ahora, delega la respuesta conversacional al LLM local.
    respuesta = llm_client.generar_respuesta(texto)

    enviado = whatsapp_client.enviar_mensaje_texto(numero, respuesta)

    guardrails.registrar_interaccion(
        numero, texto, intent, respuesta, permitido=True,
    )

    return jsonify({"status": "ok" if enviado else "error_envio"}), 200
