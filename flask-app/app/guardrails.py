"""
Guardrails del chatbot. Todo lo que decide si un mensaje se procesa,
se bloquea, o se registra vive aquí — mantenlo separado de la lógica
conversacional para poder auditarlo y testearlo de forma aislada.
"""

import time
from collections import defaultdict, deque

from flask import current_app

from app.extensions import db
from app.models import AuditoriaBot

# --- 1. Rate limiting por número de WhatsApp (en memoria; para producción
#     multi-instancia, mover esto a Redis) ---------------------------------
_mensajes_por_numero: dict[str, deque] = defaultdict(deque)


def excede_limite_mensajes(telefono_wa: str) -> bool:
    limite = current_app.config["MAX_MESSAGES_PER_MINUTE"]
    ahora = time.time()
    cola = _mensajes_por_numero[telefono_wa]

    while cola and ahora - cola[0] > 60:
        cola.popleft()

    if len(cola) >= limite:
        return True

    cola.append(ahora)
    return False


# --- 2. Filtrado básico de entrada ----------------------------------------
LONGITUD_MAXIMA_MENSAJE = 1000
# Tope cuando el bot SÍ está esperando texto libre del usuario (hoy, la nota
# de una reserva: ver recursos_service.espera_texto_libre). Ahí un mensaje
# largo no es sospechoso, es alguien que se pasó del máximo de la nota, y
# quien se lo tiene que decir -- con el límite concreto y pidiéndole que la
# reescriba -- es el wizard, no el mensaje genérico de este guardrail. Sigue
# habiendo tope: un pegote enorme se corta igual, solo que más arriba.
LONGITUD_MAXIMA_TEXTO_LIBRE = 10 * LONGITUD_MAXIMA_MENSAJE

PATRONES_SOSPECHOSOS = (
    "ignora las instrucciones anteriores",
    "eres ahora",
    "system prompt",
    "actua como si no tuvieras reglas",
)


def mensaje_es_sospechoso(texto: str, texto_libre: bool = False) -> bool:
    """Heurística simple contra intentos de inyección de prompt.
    No sustituye validación seria de intents (ver ALLOWED_INTENTS),
    solo evita casos obvios antes de llegar al LLM.

    `texto_libre=True` sube el tope de longitud a LONGITUD_MAXIMA_TEXTO_LIBRE
    (el bot está esperando texto largo a propósito); los patrones de
    inyección se revisan igual, sin excepción."""
    limite = LONGITUD_MAXIMA_TEXTO_LIBRE if texto_libre else LONGITUD_MAXIMA_MENSAJE
    if not texto or len(texto) > limite:
        return True
    texto_normalizado = texto.lower()
    return any(patron in texto_normalizado for patron in PATRONES_SOSPECHOSOS)


# --- 3. Whitelisting de intents --------------------------------------------
def intent_permitido(intent: str) -> bool:
    return intent in current_app.config["ALLOWED_INTENTS"]


# --- 4. Auditoría ------------------------------------------------------------
def registrar_interaccion(
    telefono_wa: str,
    mensaje_entrante: str,
    intent_detectado: str | None,
    respuesta_enviada: str | None,
    permitido: bool,
    motivo_bloqueo: str | None = None,
    mensaje_id: str | None = None,
    estado_envio: str = "n/a",
):
    entrada = AuditoriaBot(
        telefono_wa=telefono_wa,
        mensaje_entrante=mensaje_entrante,
        intent_detectado=intent_detectado,
        respuesta_enviada=respuesta_enviada,
        permitido=permitido,
        motivo_bloqueo=motivo_bloqueo,
        mensaje_id=mensaje_id,
        estado_envio=estado_envio,
    )
    db.session.add(entrada)
    db.session.commit()


def actualizar_estado_envio(mensaje_id: str, estado: str) -> bool:
    """Llamado desde /webhook/ack cuando Baileys reporta un cambio de
    estado (server_ack/delivery_ack/leido/fallido) para un mensaje que
    nosotros enviamos. Devuelve False si no encuentra el mensaje (por
    ejemplo, si la auditoría se limpió antes de que llegara el ack)."""
    entrada = (
        AuditoriaBot.query.filter_by(mensaje_id=mensaje_id)
        .order_by(AuditoriaBot.creado_en.desc())
        .first()
    )
    if entrada is None:
        return False
    entrada.estado_envio = estado
    db.session.commit()
    return True
