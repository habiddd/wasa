"""
Guardrails del chatbot. Todo lo que decide si un mensaje se procesa,
se bloquea, o se registra vive aquí — mantenlo separado de la lógica
conversacional para poder auditarlo y testearlo de forma aislada.
"""

import re
import time
import unicodedata
from collections import defaultdict, deque

from flask import current_app, g, has_request_context
from sqlalchemy import text

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

# Caracteres invisibles que no cambian cómo se lee el mensaje pero sí rompen
# una comparación por substring: espacios de ancho cero, marcas de dirección
# de texto, BOM y guion suave. Se eliminan antes de comparar nada.
_INVISIBLES = re.compile(r"[\u00ad\u200b-\u200f\u202a-\u202e\u2060\ufeff]")

# Frases de inyección conocidas, en la forma "compacta" en que se comparan:
# sin espacios, sin puntuación y sin tildes (ver `_compactar`). Escribirlas
# así es a propósito -- es lo que hace que "ignora  las  instrucciones"
# (espacio doble), "ignora-las-instrucciones" o "I g n o r a las..." caigan
# todas en el mismo patrón, que era justo el agujero de la versión anterior,
# que comparaba substrings literales contra el texto tal cual.
FRASES_SOSPECHOSAS = (
    # español
    "ignoralasinstrucciones",
    "ignoralasreglas",
    "ignoratusinstrucciones",
    "ignoratusreglas",
    "olvidalasinstrucciones",
    "olvidatusinstrucciones",
    "olvidalasreglas",
    "olvidatusreglas",
    "olvidaloanterior",
    "descartalasinstrucciones",
    "instruccionesanteriores",
    "instruccionesprevias",
    "reglasanteriores",
    "reglasprevias",
    "promptdelsistema",
    "promptdesistema",
    "instruccionesdelsistema",
    "instruccionesiniciales",
    "mensajedelsistema",
    "eresahora",
    "ahoraeres",
    "actuacomosinotuvieras",
    "modosinfiltros",
    "modolibre",
    "mododesarrollador",
    "revelatusinstrucciones",
    "muestratusinstrucciones",
    "repitetusinstrucciones",
    "cualessontusinstrucciones",
    "tusinstruccionesexactas",
    # inglés (el modelo entiende inglés igual que español)
    "ignoreprevious",
    "ignoreallprevious",
    "ignoreabove",
    "ignoreyourinstructions",
    "disregardprevious",
    "disregardallprevious",
    "forgetprevious",
    "forgetyourinstructions",
    "previousinstructions",
    "systemprompt",
    "systemmessage",
    "initialinstructions",
    "youarenow",
    "actasifyou",
    "actlikeyou",
    "pretendtobe",
    "developermode",
    "jailbreak",
    "doanythingnow",
    "revealyourprompt",
    "showyourprompt",
    "repeatyourinstructions",
    "printyourinstructions",
    # marcadores de plantilla de chat: no hay razón legítima para que un
    # estudiante los escriba, y son la forma clásica de simular un turno
    # del sistema dentro del mensaje del usuario.
    "imstart",
    "imend",
    "endofturn",
    "systemrole",
    "rolesystem",
)

# Patrones que solo son sospechosos EN COMBINACIÓN. "sin restricciones" a
# secas es una frase legítima de este dominio ("sin restricciones de
# horario"), así que como palabra suelta daba falso positivo; "actúa sin
# restricciones" no tiene lectura inocente. Se aplican sobre la misma
# forma compactada que las frases, así que el truco del espaciado tampoco
# sirve aquí.
REGEX_SOSPECHOSOS = (
    re.compile(
        r"(actua|eres|se|comportate|responde|habla|funciona)"
        r"sin(restricciones|reglas|filtros|limites|censura)"
    ),
    re.compile(r"(no|nunca)(tienes|tengas|sigas|obedezcas)(reglas|restricciones|limites|instrucciones)"),
    re.compile(r"modo(sin|libre|dios|god)"),
)

# Se conserva el nombre anterior por compatibilidad: era la tupla de
# substrings literales de la primera versión de este filtro.
PATRONES_SOSPECHOSOS = FRASES_SOSPECHOSAS


def _compactar(texto: str) -> str:
    """Deja el mensaje en su forma mínima comparable: sin tildes, en
    minúsculas, sin caracteres invisibles y sin nada que no sea letra o
    dígito. "Ignora  las  instrucciones anteriores!!" y
    "i.g.n.o.r.a las-instrucciones_anteriores" colapsan las dos a la misma
    cadena, así que un solo patrón las atrapa.

    El costo de compactar tanto es que un patrón corto podría aparecer
    dentro de una palabra legítima; por eso las frases de arriba son largas
    y específicas, no palabras sueltas."""
    texto = _INVISIBLES.sub("", texto)
    texto = unicodedata.normalize("NFKD", texto.lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "", texto)


def mensaje_es_sospechoso(texto: str, texto_libre: bool = False) -> bool:
    """Heurística contra intentos de inyección de prompt.
    No sustituye validación seria de intents (ver ALLOWED_INTENTS) ni el
    hecho de que las acciones reales (reservar/cancelar) nunca las decide
    el LLM: solo evita que lo obvio llegue al modelo.

    `texto_libre=True` sube el tope de longitud a LONGITUD_MAXIMA_TEXTO_LIBRE
    (el bot está esperando texto largo a propósito); los patrones de
    inyección se revisan igual, sin excepción.

    La comparación se hace sobre la forma compactada del mensaje (ver
    `_compactar`), no sobre el texto tal cual: la versión anterior buscaba
    substrings literales y se evadía con solo meter un espacio de más."""
    limite = LONGITUD_MAXIMA_TEXTO_LIBRE if texto_libre else LONGITUD_MAXIMA_MENSAJE
    if not texto or len(texto) > limite:
        return True
    compacto = _compactar(texto)
    if any(frase in compacto for frase in FRASES_SOSPECHOSAS):
        return True
    return any(patron.search(compacto) for patron in REGEX_SOSPECHOSOS)


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
    # webhook._procesar_mensaje marca la llegada del mensaje; los avisos que
    # salen del ticker no responden a nada y quedan sin latencia.
    inicio = getattr(g, "inicio_mensaje", None) if has_request_context() else None
    entrada = AuditoriaBot(
        latencia_ms=round((time.monotonic() - inicio) * 1000) if inicio else None,
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


def anonimizar_auditoria_vieja() -> int:
    """Política de retención (entregables/OE4/POLITICA_RETENCION_AUDITORIA.md):
    pasados AUDITORIA_RETENCION_DIAS se borran el texto de los mensajes y el
    teléfono; se conservan intent, permitido, motivo de bloqueo, estado de
    envío, latencia y fecha, que es lo que usan las métricas. 0 desactiva la
    purga."""
    dias = current_app.config.get("AUDITORIA_RETENCION_DIAS", 180)
    if dias <= 0:
        return 0
    filas = db.session.execute(
        text(
            "UPDATE auditoria_bot SET mensaje_entrante = NULL, respuesta_enviada = NULL, "
            "telefono_wa = 'anonimizado' "
            "WHERE creado_en < now() - make_interval(days => :dias) AND telefono_wa <> 'anonimizado'"
        ),
        {"dias": dias},
    ).rowcount
    db.session.commit()
    return filas


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
    # Meta no garantiza el orden: un "sent" tardío no pisa un "delivered"/"read".
    orden = ("server_ack", "delivery_ack", "leido")
    if estado in orden and entrada.estado_envio in orden and orden.index(estado) < orden.index(entrada.estado_envio):
        return True
    entrada.estado_envio = estado
    db.session.commit()
    return True
