"""
Menú de fallback: qué pasa cuando `llm_client.clasificar_intent` no logra
ubicar el mensaje en ninguno de los intents permitidos (en la práctica,
"otro"). Antes se cortaba la conversación con un mensaje estático de
rechazo (ver `webhook.py`); ahora se ofrece un menú de tres opciones
("Reservar" / "Consultar" / "Cancelar") que enruta hacia los flujos que ya
existen (`recursos_service.gestionar_reserva`,
`evaluar_consulta_disponibilidad` y `gestionar_cancelacion`).

Ojo con qué llega hasta aquí: desde que `llm_client` reconoce el intent
"saludo", los saludos y las cortesías ya NO caen en este menú -- se
responden como conversación (ver `llm_client.responder_charla`). A este
módulo solo llega lo que de verdad no se entendió.

Módulo puro a propósito: sin DB, sin LLM, sin importar `recursos_service`.
`webhook.py` es quien decide qué servicio llamar según la `accion` que
devuelve `procesar_respuesta` -- este módulo solo interpreta texto y
decide transiciones de estado, igual que hace `llm_client` con los
intents normales.

Estado en sesión (Redis, vía `sesiones.py`), bajo la clave `CLAVE_SESION`:
    {"paso": "elegir_opcion" | "esperando_consulta", "intentos": 0}
Se limpia con `menu_fallback = None`, mismo patrón que `reserva_wizard` /
`cancelacion_wizard`. Importante para quien toque `webhook.py`: esta clave
SIEMPRE debe quedar en `None` antes de que la sesión se le pase a
`recursos_service.gestionar_reserva` -- si no, como ese wizard hace
`{**sesion, "reserva_wizard": wizard}` en cada paso, `menu_fallback`
quedaría propagándose para siempre y el bloque de `webhook.py` que lee
`sesion.get(CLAVE_SESION)` interceptaría cada mensaje futuro del wizard.

Además del menú de fallback, este módulo también implementa la "oferta de
reserva": el empujoncito de un solo turno que se pega al final de una
respuesta de disponibilidad para invitar al usuario a reservar sin tener
que escribir un mensaje nuevo desde cero. Misma forma de máquina de
estados, misma invariante de limpieza de sesión (ver `CLAVE_OFERTA` y
`procesar_respuesta_oferta` más abajo).
"""

import re

from app.textutils import normalizar

CLAVE_SESION = "menu_fallback"
INTENT_AUDITORIA = "menu_fallback"
MAX_REINTENTOS = 2

# "no" queda fuera a propósito: en un menú numerado no es una salida, es
# una respuesta no reconocida (debe generar reintento, no cortar el flujo).
# "cancelar" tampoco está aquí desde que el menú tiene opción 3: dentro del
# menú, "cancelar" ya no significa "salgamos de aquí" sino "quiero cancelar
# una reserva", que es justo lo que hace esa opción.
PALABRAS_SALIR = {"salir", "cerrar", "nada", "ninguna", "olvidalo"}

OPCIONES_POR_NUMERO = {"1": "reservar", "2": "preguntas", "3": "cancelar"}
# El menú dice "o escríbelo con tus palabras": el nombre de la opción a
# secas se resuelve aquí; cualquier frase más larga, el clasificador.
OPCIONES_POR_NOMBRE = {
    "reservar": "reservar", "reserva": "reservar",
    "consultar": "preguntas", "consulta": "preguntas", "preguntar": "preguntas",
    "cancelar": "cancelar",
}
# Solo para el paso "esperando_consulta": ahí "cancela la del viernes" no
# es el nombre de un equipo, es un cambio de idea.
PALABRAS_CANCELAR = {"cancel", "anul"}

TEXTO_MENU = (
    "Perdona, no te entendí bien 😅 ¿Con cuál de estas te ayudo?\n\n"
    "1. Reservar — apartar un equipo o un espacio\n"
    "2. Consultar — saber si un equipo o espacio está disponible\n"
    "3. Cancelar — anular una reserva que ya tienes\n\n"
    "Responde con el número, o escríbelo con tus palabras."
)

PREGUNTA_CONSULTA = '¿Sobre qué equipo o espacio quieres saber? (ej. "cámara Sony", "sala de edición")'

MENSAJE_REINTENTO = "No reconocí esa opción.\n\n" + TEXTO_MENU

MENSAJE_SALIDA = (
    "Listo, no te molesto más 🙂 Escríbeme cuando quieras reservar algo, "
    "preguntar por disponibilidad o cancelar una reserva."
)

# Mismo texto que antes se usaba para bloquear directo (ver webhook.py) --
# se conserva como red final si el usuario agota los reintentos del menú.
MENSAJE_FUERA_DE_ALCANCE = (
    "Solo puedo ayudarte a reservar equipos/espacios, consultar "
    "disponibilidad o cancelar una reserva. Escribe 'ayuda' para más info."
)


def activar_menu(sesion: dict) -> tuple[str, dict]:
    """Punto de entrada: se llama cuando `clasificar_intent` no devuelve
    un intent permitido. Reemplaza el bloqueo estático de antes por este
    menú."""
    nueva_sesion = {**sesion, CLAVE_SESION: {"paso": "elegir_opcion", "intentos": 0}}
    return TEXTO_MENU, nueva_sesion


def _interpretar_opcion_menu(texto_usuario: str) -> str | None:
    """Devuelve 'reservar' | 'preguntas' | 'cancelar' | 'salir' |
    'texto_libre' | None (un número que no es opción -> reintento).

    Solo resuelve lo inequívoco: el número de una opción, su nombre a secas,
    o una palabra de salida. El texto libre ya no se adivina con subcadenas:
    "mi reserva quedó confirmada?" contenía "reserv" y abría el wizard de
    reserva, y "gracias, chao" repetía el menú (medido en vivo 2026-10-15).
    Ese texto vuelve al clasificador: ver "seguir_curso" en
    `procesar_respuesta`."""
    texto_norm = normalizar(texto_usuario)

    if set(texto_norm.split()) & PALABRAS_SALIR:
        return "salir"

    m = re.match(r"^\s*(\d+)", texto_norm)
    if m:
        return OPCIONES_POR_NUMERO.get(m.group(1))

    return OPCIONES_POR_NOMBRE.get(texto_norm.strip(" .!¡?¿"), "texto_libre")


def procesar_respuesta(texto_usuario: str, sesion: dict) -> tuple[str, str, dict, dict]:
    """Interpreta la respuesta del usuario contra el paso actual del menú.

    Devuelve (accion, payload, nueva_sesion, auditoria):
      - accion == "responder":  payload es el texto a enviar tal cual.
      - accion == "reservar":   payload es el texto a pasarle a
                                 `recursos_service.gestionar_reserva`.
      - accion == "consultar":  payload es el texto a pasarle a
                                 `recursos_service.evaluar_consulta_disponibilidad`.
      - accion == "cancelar":   payload es el texto a pasarle a
                                 `recursos_service.gestionar_cancelacion`.
      - accion == "seguir_curso": texto libre, no una opción del menú; el
                                 webhook lo clasifica como mensaje nuevo
                                 (la sesión ya viene sin el menú).
      - auditoria: {"intent": str, "permitido": bool, "motivo_bloqueo": str | None}
    """
    estado = sesion.get(CLAVE_SESION)
    if not isinstance(estado, dict):
        estado = {"paso": "elegir_opcion", "intentos": 0}
    paso = estado.get("paso")
    if paso not in ("elegir_opcion", "esperando_consulta"):
        paso = "elegir_opcion"
    intentos = estado.get("intentos", 0)

    sesion_limpia = {**sesion, CLAVE_SESION: None}

    if paso == "esperando_consulta":
        opcion = _interpretar_opcion_menu(texto_usuario)
        if opcion == "salir":
            return (
                "responder",
                MENSAJE_SALIDA,
                sesion_limpia,
                {"intent": INTENT_AUDITORIA, "permitido": True, "motivo_bloqueo": None},
            )
        # Alguien que responde "cancelar" a "¿sobre qué equipo quieres
        # saber?" no está nombrando un equipo: cambió de idea y quiere
        # cancelar una reserva. Antes ese texto se le pasaba tal cual a la
        # consulta de disponibilidad, que no encontraba nada.
        if opcion == "cancelar" or any(p in normalizar(texto_usuario) for p in PALABRAS_CANCELAR):
            return (
                "cancelar",
                texto_usuario,
                sesion_limpia,
                {"intent": "cancelar", "permitido": True, "motivo_bloqueo": None},
            )
        return (
            "consultar",
            texto_usuario,
            sesion_limpia,
            {"intent": "consultar_disponibilidad", "permitido": True, "motivo_bloqueo": None},
        )

    # paso == "elegir_opcion"
    opcion = _interpretar_opcion_menu(texto_usuario)

    if opcion == "reservar":
        return (
            "reservar",
            texto_usuario,
            sesion_limpia,
            {"intent": "reservar", "permitido": True, "motivo_bloqueo": None},
        )

    if opcion == "cancelar":
        return (
            "cancelar",
            texto_usuario,
            sesion_limpia,
            {"intent": "cancelar", "permitido": True, "motivo_bloqueo": None},
        )

    if opcion == "preguntas":
        nueva_sesion = {**sesion, CLAVE_SESION: {"paso": "esperando_consulta", "intentos": 0}}
        return (
            "responder",
            PREGUNTA_CONSULTA,
            nueva_sesion,
            {"intent": "consultar_disponibilidad", "permitido": True, "motivo_bloqueo": None},
        )

    if opcion == "salir":
        return (
            "responder",
            MENSAJE_SALIDA,
            sesion_limpia,
            {"intent": INTENT_AUDITORIA, "permitido": True, "motivo_bloqueo": None},
        )

    # Una frase en vez de una opción: que la clasifique el LLM como
    # cualquier mensaje nuevo. Si tampoco la entiende, el webhook vuelve a
    # mostrar el menú.
    if opcion == "texto_libre":
        return (
            "seguir_curso",
            texto_usuario,
            sesion_limpia,
            {"intent": INTENT_AUDITORIA, "permitido": True, "motivo_bloqueo": None},
        )

    # No reconocida (un número que no es opción)
    if intentos < MAX_REINTENTOS:
        nueva_sesion = {
            **sesion,
            CLAVE_SESION: {"paso": "elegir_opcion", "intentos": intentos + 1},
        }
        return (
            "responder",
            MENSAJE_REINTENTO,
            nueva_sesion,
            {"intent": INTENT_AUDITORIA, "permitido": True, "motivo_bloqueo": None},
        )

    return (
        "responder",
        MENSAJE_FUERA_DE_ALCANCE,
        sesion_limpia,
        {"intent": INTENT_AUDITORIA, "permitido": False, "motivo_bloqueo": "intent_no_permitido"},
    )


# --- Oferta de reserva tras una consulta de disponibilidad -----------------
#
# Se activa desde `webhook.py` justo después de responder una consulta de
# disponibilidad (tanto la del intent normal como la que sale del menú de
# fallback, opción "Consultar"). Un solo turno: o el usuario acepta, o
# rechaza, o cambia de tema -- nunca reintenta ni queda en bucle.

CLAVE_OFERTA = "oferta_reserva"
INTENT_AUDITORIA_OFERTA = "oferta_reserva"

TEXTO_OFERTA = "\n\n¿Quieres reservar algo? Responde *sí* y te ayudo 🙂"

MENSAJE_OFERTA_RECHAZADA = "Listo, cualquier cosa me avisas 🙂"

PALABRAS_ACEPTAR_OFERTA = {"si", "1", "dale", "listo", "claro", "ok", "bueno", "quiero"}
PALABRAS_RECHAZAR_OFERTA = {"no", "nada", "gracias", "despues", "luego"}


def activar_oferta(sesion: dict, respuesta: str, texto_consulta: str | None = None) -> tuple[str, dict]:
    """Se llama justo después de responder una consulta de disponibilidad.
    Pega la invitación a reservar al final de `respuesta` y marca la sesión
    para interpretar el próximo mensaje contra esta oferta. `texto_consulta`
    es lo que preguntó el usuario: si acepta, la reserva arranca con ese
    texto (con su activo y su fecha), no con el "sí"."""
    nueva_sesion = {**sesion, CLAVE_OFERTA: {"activa": True, "texto": texto_consulta}}
    return respuesta + TEXTO_OFERTA, nueva_sesion


def _interpretar_respuesta_oferta(texto_usuario: str) -> str | None:
    """Devuelve 'aceptar' | 'rechazar' | None (ninguna de las dos: el
    usuario cambió de tema)."""
    texto_norm = normalizar(texto_usuario)
    tokens = set(texto_norm.split())

    # "no" antes que "si" no hace falta acá (no hay subcadena en común),
    # pero sí conviene chequear rechazo primero por si alguien escribe algo
    # como "no, gracias" que también podría matchear una palabra suelta de
    # aceptar en el futuro.
    if tokens & PALABRAS_RECHAZAR_OFERTA:
        return "rechazar"
    if tokens & PALABRAS_ACEPTAR_OFERTA:
        return "aceptar"
    return None


def procesar_respuesta_oferta(texto_usuario: str, sesion: dict) -> tuple[str, str, dict, dict]:
    """Interpreta la respuesta del usuario a la oferta de reservar.

    Devuelve (accion, payload, nueva_sesion, auditoria), misma forma que
    `procesar_respuesta`:
      - accion == "reservar":    payload es el texto a pasarle a
                                   `recursos_service.gestionar_reserva`.
      - accion == "responder":   payload es el texto a enviar tal cual
                                   (rechazo cordial).
      - accion == "seguir_curso": el mensaje no fue ni sí ni no -- el
                                   llamador debe limpiar la oferta y dejar
                                   que el mensaje siga su camino normal
                                   (clasificación de intent). `payload` es
                                   None.
      - auditoria: {"intent": str, "permitido": bool, "motivo_bloqueo": None}
    """
    texto_consulta = (sesion.get(CLAVE_OFERTA) or {}).get("texto")
    sesion_limpia = {**sesion, CLAVE_OFERTA: None}
    opcion = _interpretar_respuesta_oferta(texto_usuario)

    if opcion == "aceptar":
        return (
            "reservar",
            texto_consulta or texto_usuario,
            sesion_limpia,
            {"intent": "reservar", "permitido": True, "motivo_bloqueo": None},
        )

    if opcion == "rechazar":
        return (
            "responder",
            MENSAJE_OFERTA_RECHAZADA,
            sesion_limpia,
            {"intent": INTENT_AUDITORIA_OFERTA, "permitido": True, "motivo_bloqueo": None},
        )

    # Ni sí ni no: el usuario cambió de tema (p. ej. una pregunta nueva).
    # No lo dejamos atrapado en un "¿sí o no?" -- se limpia la oferta y el
    # mensaje sigue su curso normal hacia clasificación de intent.
    return (
        "seguir_curso",
        None,
        sesion_limpia,
        {"intent": INTENT_AUDITORIA_OFERTA, "permitido": True, "motivo_bloqueo": None},
    )
