from flask import Blueprint, current_app, jsonify, request

from app import (
    whatsapp_client,
    guardrails,
    llm_client,
    estado_bot,
    recursos_service,
    usuarios_service,
    registro_service,
    sesiones,
    menu_service,
)

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


@bp.route("/webhook/ack", methods=["POST"])
def recibir_ack():
    """Recibe de Baileys el estado de entrega de un mensaje que enviamos
    (server_ack/delivery_ack/leido/fallido) y lo asocia a su fila de
    auditoría por mensaje_id."""
    payload = request.get_json(silent=True) or {}
    mensaje_id = payload.get("id")
    estado = payload.get("estado")

    if not mensaje_id or not estado:
        return jsonify({"status": "ignorado"}), 200

    encontrado = guardrails.actualizar_estado_envio(mensaje_id, estado)
    return jsonify({"status": "ok" if encontrado else "no_encontrado"}), 200


def _extraer_mensaje(payload: dict) -> tuple[str | None, str | None, str | None]:
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
    Devuelve (numero_sin_sufijo, texto, remote_jid) o (None, None, None) si
    no aplica. `numero` es solo un identificador para rate limiting/auditoría;
    para responder hay que usar `remote_jid` completo -- WhatsApp usa JIDs
    "@lid" para algunas cuentas en vez de "@s.whatsapp.net", y perder el
    sufijo hace que la respuesta se mande a un JID que no existe.
    """
    if payload.get("event") != "messages.upsert":
        return None, None, None

    data = payload.get("data", {})
    key = data.get("key", {})

    if key.get("fromMe"):
        return None, None, None  # ignorar mensajes enviados por el propio bot

    remote_jid = key.get("remoteJid", "")
    numero = remote_jid.split("@")[0] if "@" in remote_jid else remote_jid

    mensaje = data.get("message", {})
    texto = mensaje.get("conversation") or mensaje.get("extendedTextMessage", {}).get("text")

    return (numero or None), (texto or None), (remote_jid or None)


@bp.route("/webhook/whatsapp", methods=["POST"])
def recibir_webhook():
    payload = request.get_json(silent=True) or {}
    numero, texto, remote_jid = _extraer_mensaje(payload)

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
        respuesta = "No puedo procesar ese mensaje. Escribe 'ayuda' para ver qué puedo hacer."
        enviado, mensaje_id = whatsapp_client.enviar_mensaje_texto(remote_jid, respuesta)
        guardrails.registrar_interaccion(
            numero, texto, None, respuesta, permitido=False,
            motivo_bloqueo="mensaje_sospechoso",
            mensaje_id=mensaje_id,
            estado_envio="pendiente" if enviado else "error_http",
        )
        return jsonify({"status": "bloqueado"}), 200

    # --- Identidad: hace falta saber quién es quién antes de dejarlo hacer
    # nada. El primer contacto de un número crea su fila de Usuario vacía;
    # mientras no tenga nombre/código/cédula, cada mensaje se trata como
    # respuesta a la siguiente pregunta pendiente del registro (ver
    # registro_service.py), sin pasar por clasificación de intent -- si no,
    # "Juan Pérez" como respuesta a "¿cuál es tu nombre?" se clasificaría
    # como intent "otro" y quedaría bloqueado por la whitelist. ---
    usuario = usuarios_service.obtener_o_crear_usuario(numero)
    sesion = sesiones.obtener_sesion(numero)

    if not registro_service.usuario_esta_completo(usuario):
        respuesta, sesion = registro_service.procesar_registro(usuario, texto, sesion)
        sesiones.guardar_sesion(numero, sesion)

        enviado, mensaje_id = whatsapp_client.enviar_mensaje_texto(remote_jid, respuesta)
        guardrails.registrar_interaccion(
            numero, texto, "registro", respuesta, permitido=True,
            mensaje_id=mensaje_id,
            estado_envio="pendiente" if enviado else "error_http",
        )
        return jsonify({"status": "registro_en_progreso"}), 200

    # --- Continuación de un wizard de reserva en curso: gestionar_reserva
    # ya le preguntó al usuario un paso (qué tipo/marca/modelo, fecha, hora,
    # o el menú final de confirmar/cambiar/cancelar) y está esperando esa
    # respuesta en este mismo mensaje (ver recursos_service.gestionar_reserva)
    # -- eso tampoco pasa por clasificación de intent: una respuesta corta
    # como "2" no se reconoce de forma confiable como intent "reservar" y
    # quedaría bloqueada por la whitelist. ---
    if sesion.get("reserva_wizard"):
        respuesta, sesion = recursos_service.gestionar_reserva(texto, usuario, sesion)
        sesiones.guardar_sesion(numero, sesion)

        enviado, mensaje_id = whatsapp_client.enviar_mensaje_texto(remote_jid, respuesta)
        guardrails.registrar_interaccion(
            numero, texto, "reservar", respuesta, permitido=True,
            mensaje_id=mensaje_id,
            estado_envio="pendiente" if enviado else "error_http",
        )
        return jsonify({"status": "ok" if enviado else "error_envio"}), 200

    # Mismo motivo que el bloque de arriba: mientras haya un
    # cancelacion_wizard en curso (ver recursos_service.gestionar_cancelacion),
    # la respuesta del usuario (un número, "sí"/"no") va directo ahí, no a
    # clasificación de intent.
    if sesion.get("cancelacion_wizard"):
        respuesta, sesion = recursos_service.gestionar_cancelacion(texto, usuario, sesion)
        sesiones.guardar_sesion(numero, sesion)

        enviado, mensaje_id = whatsapp_client.enviar_mensaje_texto(remote_jid, respuesta)
        guardrails.registrar_interaccion(
            numero, texto, "cancelar", respuesta, permitido=True,
            mensaje_id=mensaje_id,
            estado_envio="pendiente" if enviado else "error_http",
        )
        return jsonify({"status": "ok" if enviado else "error_envio"}), 200

    # --- Menú de fallback en curso: el mensaje anterior no se pudo
    # clasificar y se le ofreció al usuario elegir entre Reservas y
    # Preguntas (ver bloque de "intent no permitido" más abajo); esta
    # respuesta ("1", "2", "reservas"...) se interpreta contra ese menú,
    # no como un mensaje nuevo suelto -- mismo motivo que los dos bloques
    # de wizard de arriba. `menu_service.procesar_respuesta` ya deja
    # `menu_fallback` en None en la sesión que devuelve antes de que se
    # la pasemos a `gestionar_reserva`, para no propagar este estado
    # dentro del wizard de reservas. ---
    if sesion.get(menu_service.CLAVE_SESION):
        accion, payload, sesion, auditoria = menu_service.procesar_respuesta(texto, sesion)

        if accion == "reservar":
            respuesta, sesion = recursos_service.gestionar_reserva(payload, usuario, sesion)
        elif accion == "consultar":
            respuesta = recursos_service.evaluar_consulta_disponibilidad(payload)
        else:  # "responder": menú, reintento, salida o rendición
            respuesta = payload

        sesiones.guardar_sesion(numero, sesion)
        enviado, mensaje_id = whatsapp_client.enviar_mensaje_texto(remote_jid, respuesta)
        guardrails.registrar_interaccion(
            numero, texto, auditoria["intent"], respuesta,
            permitido=auditoria["permitido"],
            motivo_bloqueo=auditoria["motivo_bloqueo"],
            mensaje_id=mensaje_id,
            estado_envio="pendiente" if enviado else "error_http",
        )
        return jsonify({"status": "ok" if enviado else "error_envio"}), 200

    # --- Clasificación de intent + whitelist ---
    intent = llm_client.clasificar_intent(texto)
    if not guardrails.intent_permitido(intent):
        # Antes esto bloqueaba con un mensaje estático fijo. Ahora se le
        # ofrece al usuario un menú (Reservas / Preguntas) en vez de
        # cortar la conversación -- ver menu_service.py. NO agregar "otro"
        # a ALLOWED_INTENTS como atajo: eso enrutaría cualquier mensaje
        # fuera de alcance a llm_client.generar_respuesta (línea de abajo),
        # eliminando este guardrail.
        respuesta, sesion = menu_service.activar_menu(sesion)
        sesiones.guardar_sesion(numero, sesion)
        enviado, mensaje_id = whatsapp_client.enviar_mensaje_texto(remote_jid, respuesta)
        guardrails.registrar_interaccion(
            numero, texto, menu_service.INTENT_AUDITORIA, respuesta, permitido=True,
            mensaje_id=mensaje_id,
            estado_envio="pendiente" if enviado else "error_http",
        )
        return jsonify({"status": "menu_fallback"}), 200

    # Para reservar/consultar disponibilidad/cancelar, la respuesta sale de
    # datos reales de recursos/reservas (ver recursos_service) -- nunca del
    # LLM, que podría inventar disponibilidad o dejar cancelar una reserva
    # ajena. Solo 'ayuda' sigue delegando en el LLM.
    if intent == "consultar_disponibilidad":
        respuesta = recursos_service.evaluar_consulta_disponibilidad(texto)
    elif intent == "reservar":
        respuesta, sesion = recursos_service.gestionar_reserva(texto, usuario, sesion)
        sesiones.guardar_sesion(numero, sesion)
    elif intent == "cancelar":
        respuesta, sesion = recursos_service.gestionar_cancelacion(texto, usuario, sesion)
        sesiones.guardar_sesion(numero, sesion)
    else:
        respuesta = llm_client.generar_respuesta(texto)

    enviado, mensaje_id = whatsapp_client.enviar_mensaje_texto(remote_jid, respuesta)

    guardrails.registrar_interaccion(
        numero, texto, intent, respuesta, permitido=True,
        mensaje_id=mensaje_id,
        estado_envio="pendiente" if enviado else "error_http",
    )

    return jsonify({"status": "ok" if enviado else "error_envio"}), 200
