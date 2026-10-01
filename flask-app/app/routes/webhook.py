import hashlib
import hmac
import time

from flask import Blueprint, Response, current_app, g, jsonify, request

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

from app.extensions import db, redis_client

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
      "instance": "lacs-uninorte",
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
    # Con JID "@lid" lo de antes de la "@" es un identificador anónimo, no
    # el teléfono; el número real llega en `senderPn` (ver baileys-bot).
    # Si WhatsApp no lo compartió, se sigue usando el LID.
    sender_pn = key.get("senderPn") or ""
    if remote_jid.endswith("@lid") and sender_pn:
        numero = sender_pn.split("@")[0]

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

    # Con Meta activo, Baileys sigue conectado pero no debe contestar.
    if whatsapp_client.proveedor_activo() != "baileys":
        return jsonify({"status": "ignorado_proveedor"}), 200

    return _procesar_mensaje(numero, texto, remote_jid)


def _procesar_mensaje(numero: str, texto: str, remote_jid: str, pisar_jid: bool = True):
    """Cuerpo común a los dos canales (Baileys y Meta): guardrails,
    identidad, wizards, intent y respuesta. Devuelve la respuesta de Flask."""
    g.inicio_mensaje = time.monotonic()  # latencia_ms en guardrails.registrar_interaccion
    # --- Guardrail 1: rate limiting por número ---
    if guardrails.excede_limite_mensajes(numero):
        guardrails.registrar_interaccion(
            numero, texto, None, None, permitido=False,
            motivo_bloqueo="rate_limit_excedido",
        )
        return jsonify({"status": "rate_limited"}), 200

    # --- Guardrail 2: filtrado de entrada ---
    # El tope de longitud se afloja (no se quita) cuando el wizard de reserva
    # dejó al usuario escribiendo la nota: ese mensaje largo no es un intento
    # de inyección, es alguien que se pasó de NOTA_MAX_CARACTERES, y tiene
    # que llegar al wizard para que le conteste con el límite concreto y le
    # pida reescribirla (ver recursos_service.espera_texto_libre). La sesión
    # se lee acá solo para eso -- es una lectura de Redis sin efectos, antes
    # de crearle fila de Usuario a nadie; los patrones de inyección se siguen
    # revisando igual en los dos casos.
    texto_libre = recursos_service.espera_texto_libre(sesiones.obtener_sesion(numero))
    if guardrails.mensaje_es_sospechoso(texto, texto_libre=texto_libre):
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
    numero_entrante = numero
    usuario = usuarios_service.obtener_o_crear_usuario(numero, remote_jid, pisar_jid=pisar_jid)
    # Si el mensaje llegó con JID "@lid" y sin `senderPn`, `numero` es el
    # LID; el usuario ya conocido trae el número real. Sin esto su sesión y
    # su auditoría se partirían en dos claves distintas.
    numero = usuario.telefono_wa
    sesion = sesiones.obtener_sesion(numero)

    if not registro_service.usuario_esta_completo(usuario):
        respuesta, sesion = registro_service.procesar_registro(usuario, texto, sesion)
        # Si dio su celular, `telefono_wa` cambió (o se fusionó la fila): la
        # sesión y la auditoría van con el número nuevo, que es el que se
        # resolverá en el próximo mensaje. Se busca con el número tal como
        # llegó en el mensaje, no con el `telefono_wa` de antes: si la persona
        # acaba de corregir su celular, ese número viejo ya no es suyo y
        # obtener_o_crear_usuario lo tomaría por un `senderPn` y le
        # devolvería el número viejo.
        numero = usuarios_service.obtener_o_crear_usuario(numero_entrante, remote_jid, pisar_jid=pisar_jid).telefono_wa
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
            # Misma oferta de reservar que tras una consulta de disponibilidad
            # normal (ver bloque de intent más abajo) -- esta es la otra de
            # las dos rutas que responden disponibilidad.
            respuesta, sesion = menu_service.activar_oferta(sesion, respuesta, payload)
        elif accion == "cancelar":
            respuesta, sesion = recursos_service.gestionar_cancelacion(payload, usuario, sesion)
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

    # --- Oferta de reserva en curso: el mensaje anterior fue una consulta
    # de disponibilidad y se le ofreció al usuario iniciar una reserva (ver
    # los dos puntos más arriba donde se llama a `menu_service.activar_oferta`,
    # y el bloque de intent `consultar_disponibilidad` más abajo). Un solo
    # turno: si acepta o rechaza, se resuelve acá; si escribió otra cosa
    # (cambió de tema), NO se retorna -- se limpia la oferta y el mensaje
    # sigue su curso normal hacia clasificación de intent, para no dejarlo
    # atrapado respondiendo "¿sí o no?" a algo que ya no preguntó. ---
    if sesion.get(menu_service.CLAVE_OFERTA):
        accion, payload, sesion, auditoria = menu_service.procesar_respuesta_oferta(texto, sesion)

        if accion == "seguir_curso":
            sesiones.guardar_sesion(numero, sesion)
            # sin return: cae al bloque de clasificación de intent de abajo
        else:
            if accion == "reservar":
                # El usuario solo dijo "sí" (o equivalente), pero `payload` es
                # el texto de la consulta que originó la oferta (ver
                # `menu_service.activar_oferta`), así que la reserva arranca
                # con el activo y la fecha que ya había dicho ("me prestan las
                # luces led este sábado?"). Si la oferta no guardó texto
                # (sesión vieja), payload es el "sí" y el wizard arranca en
                # su primer paso (el menú de equipos y espacios).
                respuesta, sesion = recursos_service.gestionar_reserva(payload, usuario, sesion)
            else:  # "responder": rechazo cordial
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
    # Los nombres reales del catálogo van como señal de dominio: le dan
    # holgura al clasificador para reconocer un pedido que nombra un equipo
    # sin usar ningún verbo de reserva ("¿y el trípode Manfrotto?"). Ver
    # llm_client._tiene_senal_de_dominio. Solo es una consulta de lectura
    # sobre recursos activos, y solo se ejecuta en la rama que de verdad
    # clasifica (los wizards y el menú ya salieron por arriba).
    intent = llm_client.clasificar_intent(
        texto, terminos_catalogo=recursos_service.listar_terminos_catalogo()
    )
    if not guardrails.intent_permitido(intent):
        # Antes esto bloqueaba con un mensaje estático fijo. Ahora se le
        # ofrece al usuario un menú (Reservar / Consultar / Cancelar) en
        # vez de cortar la conversación -- ver menu_service.py. NO agregar
        # "otro" a ALLOWED_INTENTS como atajo: eso enrutaría cualquier
        # mensaje fuera de alcance a llm_client.generar_respuesta (línea de
        # abajo), eliminando este guardrail.
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
    # ajena. Solo 'ayuda' y 'saludo' delegan en el LLM, y ninguno de los dos
    # toca la base de datos ni afirma nada sobre disponibilidad.
    if intent == "consultar_disponibilidad":
        respuesta = recursos_service.evaluar_consulta_disponibilidad(texto)
        # Tras responder disponibilidad, se ofrece iniciar una reserva para
        # no cortar la conversación (ver menu_service.activar_oferta). La
        # oferta se pega acá, no dentro de `evaluar_consulta_disponibilidad`,
        # para no acoplar ese servicio puro al estado de sesión.
        respuesta, sesion = menu_service.activar_oferta(sesion, respuesta, texto)
        sesiones.guardar_sesion(numero, sesion)
    elif intent == "reservar":
        respuesta, sesion = recursos_service.gestionar_reserva(texto, usuario, sesion)
        sesiones.guardar_sesion(numero, sesion)
    elif intent == "cancelar":
        respuesta, sesion = recursos_service.gestionar_cancelacion(texto, usuario, sesion)
        sesiones.guardar_sesion(numero, sesion)
    elif intent == "saludo":
        # Conversación social sin pedido concreto detrás. Antes esto caía en
        # "otro" y se le respondía con el menú de fallback, que a un "hola"
        # se siente como un portazo; ahora se contesta como una persona y el
        # menú queda reservado para lo que de verdad no se entiende.
        respuesta = llm_client.responder_charla(texto)
    else:
        respuesta = llm_client.generar_respuesta(texto)

    enviado, mensaje_id = whatsapp_client.enviar_mensaje_texto(remote_jid, respuesta)

    guardrails.registrar_interaccion(
        numero, texto, intent, respuesta, permitido=True,
        mensaje_id=mensaje_id,
        estado_envio="pendiente" if enviado else "error_http",
    )

    return jsonify({"status": "ok" if enviado else "error_envio"}), 200


# --- Canal oficial de Meta (WhatsApp Cloud API) -------------------------------

# Estado de Meta -> el que ya usa auditoria_bot con Baileys (ver
# guardrails.actualizar_estado_envio).
_ESTADO_META = {"sent": "server_ack", "delivered": "delivery_ack", "read": "leido", "failed": "fallido"}


@bp.route("/webhook/meta", methods=["GET"])
def verificar_meta():
    """Verificación del webhook: Meta manda hub.verify_token y espera el
    hub.challenge de vuelta en texto plano."""
    esperado = current_app.config["META_WEBHOOK_VERIFY_TOKEN"]
    recibido = request.args.get("hub.verify_token", "")
    if (esperado and request.args.get("hub.mode") == "subscribe"
            and hmac.compare_digest(recibido, esperado)):
        return Response(request.args.get("hub.challenge", ""), 200, mimetype="text/plain")
    return Response("Forbidden", 403)


def _firma_meta_valida() -> bool:
    secreto = current_app.config["META_APP_SECRET"]
    if not secreto:
        return False
    esperada = "sha256=" + hmac.new(secreto.encode(), request.get_data(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(request.headers.get("X-Hub-Signature-256", ""), esperada)


def _procesar_mensaje_meta(msg: dict) -> None:
    if msg.get("type") != "text":
        return
    # Meta reintenta hasta 36 h: el mismo wamid se procesa una sola vez.
    if not redis_client.set(f"meta:wamid:{msg.get('id')}", 1, nx=True, ex=172800):
        return
    if whatsapp_client.proveedor_activo() != "meta":
        return
    # Sin número (la persona usa nombre de usuario) se identifica por su BSUID:
    # se trata como un LID, el registro le pide el celular.
    numero = msg.get("from") or msg.get("from_user_id")
    if not numero:
        return
    # La ventana cuenta desde que la persona escribió, no desde que llegó el webhook (Meta reintenta hasta 36 h).
    ttl = 86400 - 300 - int(time.time() - int(msg.get("timestamp") or time.time()))
    if ttl > 0:
        for clave in {msg.get("from"), msg.get("from_user_id")} - {None}:
            redis_client.set(f"meta:ventana:{clave}", 1, ex=ttl)
    jid = f"{numero}@s.whatsapp.net" if msg.get("from") else f"{numero}@bsuid"
    _procesar_mensaje(numero, msg["text"]["body"], jid, pisar_jid=False)


@bp.route("/webhook/meta", methods=["POST"])
def recibir_webhook_meta():
    if not _firma_meta_valida():
        return Response("Forbidden", 403)

    payload = request.get_json(silent=True) or {}
    for entrada in payload.get("entry", []):
        for cambio in entrada.get("changes", []):
            valor = cambio.get("value", {})
            if valor.get("metadata", {}).get("phone_number_id") != current_app.config["META_WA_PHONE_NUMBER_ID"]:
                continue  # evento de otro número de la cuenta: no se contesta desde el nuestro
            for err in valor.get("errors", []):
                current_app.logger.warning("Error de Meta en webhook: %s", err)
            # Nunca un 500: Meta reintentaría todo el lote. Se loguea y se sigue.
            for msg in valor.get("messages", []):
                try:
                    _procesar_mensaje_meta(msg)
                except Exception:  # noqa: BLE001
                    current_app.logger.exception("Error procesando mensaje de Meta %s", msg.get("id"))
                    db.session.rollback()
            for st in valor.get("statuses", []):
                if st.get("status") == "failed":
                    current_app.logger.warning("Meta no entregó %s: %s", st.get("id"), st.get("errors"))
                estado = _ESTADO_META.get(st.get("status"))
                try:
                    if estado and st.get("id"):
                        guardrails.actualizar_estado_envio(st["id"], estado)
                except Exception:  # noqa: BLE001
                    current_app.logger.exception("Error actualizando estado de Meta %s", st.get("id"))
                    db.session.rollback()
    return jsonify({"status": "ok"}), 200
