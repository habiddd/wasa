"""
Menú de fallback: qué pasa cuando `llm_client.clasificar_intent` no logra
ubicar el mensaje en ninguno de los intents permitidos (en la práctica,
"otro"). Antes se cortaba la conversación con un mensaje estático de
rechazo (ver `webhook.py`); ahora se ofrece un menú de dos opciones
("Reservas" / "Preguntas") que enruta hacia los flujos que ya existen
(`recursos_service.gestionar_reserva` / `evaluar_consulta_disponibilidad`).

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
"""

import re

from app.textutils import normalizar

CLAVE_SESION = "menu_fallback"
INTENT_AUDITORIA = "menu_fallback"
MAX_REINTENTOS = 2

# "no" queda fuera a propósito: en un menú "¿1 o 2?" no es una salida, es
# una respuesta no reconocida (debe generar reintento, no cortar el flujo).
PALABRAS_SALIR = {"salir", "cerrar", "cancelar", "cancela"}

PALABRAS_RESERVAR = {"reserv", "aparta", "prestamo", "alquil", "necesito"}
PALABRAS_PREGUNTAS = {"pregunt", "consult", "disponib", "duda", "informacion", "libre", "tienen"}

TEXTO_MENU = (
    "No estoy seguro de haber entendido. ¿Con qué te ayudo?\n\n"
    "1. Reservas — apartar un equipo o espacio\n"
    "2. Preguntas — saber si un equipo o espacio está disponible\n\n"
    "Responde con el número (1 o 2)."
)

PREGUNTA_CONSULTA = '¿Sobre qué equipo o espacio quieres saber? (ej. "cámara Sony", "sala de edición")'

MENSAJE_REINTENTO = "No reconocí esa opción.\n\n" + TEXTO_MENU

MENSAJE_SALIDA = (
    'Listo, salgo del menú. Escríbeme cuando quieras reservar algo o consultar disponibilidad.\n'
    'Si querías cancelar una reserva que ya tienes, escribe "cancelar mi reserva".'
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
    """Devuelve 'reservar' | 'preguntas' | 'salir' | None (no reconocida).
    Orden deliberado: salir se chequea antes que las palabras clave de
    reservar por el mismo motivo que `llm_client._clasificar_intent_heuristico`
    revisa "cancel" antes que "reserv" -- "cancelar mi reserva" contiene la
    subcadena "reserv"."""
    texto_norm = normalizar(texto_usuario)
    tokens = set(texto_norm.split())

    if tokens & PALABRAS_SALIR:
        return "salir"

    m = re.match(r"^\s*(\d+)", texto_norm)
    if m:
        numero = m.group(1)
        if numero == "1":
            return "reservar"
        if numero == "2":
            return "preguntas"
        return None  # cualquier otro número -> no reconocida, reintento

    es_reservar = any(p in texto_norm for p in PALABRAS_RESERVAR)
    es_preguntas = any(p in texto_norm for p in PALABRAS_PREGUNTAS)
    if es_reservar and not es_preguntas:
        return "reservar"
    if es_preguntas and not es_reservar:
        return "preguntas"
    return None


def procesar_respuesta(texto_usuario: str, sesion: dict) -> tuple[str, str, dict, dict]:
    """Interpreta la respuesta del usuario contra el paso actual del menú.

    Devuelve (accion, payload, nueva_sesion, auditoria):
      - accion == "responder":  payload es el texto a enviar tal cual.
      - accion == "reservar":   payload es el texto a pasarle a
                                 `recursos_service.gestionar_reserva`.
      - accion == "consultar":  payload es el texto a pasarle a
                                 `recursos_service.evaluar_consulta_disponibilidad`.
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

    # No reconocida
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
