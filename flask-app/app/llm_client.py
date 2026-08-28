"""
Cliente mínimo para tu LLM local. Asume un endpoint compatible con la API
de chat de OpenAI, que es lo que exponen tanto Ollama (`/v1/chat/completions`)
como LM Studio. Ajusta la ruta si tu setup difiere.
"""

import json
from datetime import datetime

import requests
from flask import current_app

from app.textutils import normalizar

SYSTEM_PROMPT = """Eres el asistente de reservas de la CPA (Centro de Producción
Audiovisual) de Uninorte. SOLO puedes ayudar con: reservar equipos/espacios,
consultar disponibilidad, cancelar una reserva, o dar ayuda sobre cómo usar
este chat. Si el mensaje pide cualquier otra cosa, responde amablemente que
no puedes ayudar con eso y sugiere escribir "ayuda". Nunca inventes
disponibilidad: si no tienes el dato, dilo explícitamente. Sé breve y claro,
como si hablaras por WhatsApp."""


INTENTS_VALIDOS = ("reservar", "consultar_disponibilidad", "cancelar", "ayuda", "otro")

PROMPT_CLASIFICACION = """Eres un clasificador de intents, no un asistente
conversacional. Dado un mensaje de WhatsApp dirigido al bot de reservas de
la CPA (Centro de Producción Audiovisual) de Uninorte, clasifícalo en
EXACTAMENTE una de estas categorías y responde SOLO con la palabra de la
categoría (sin comillas, sin explicación, sin markdown, sin texto extra):

- reservar: quiere reservar/apartar un equipo o espacio, o le está
  respondiendo al bot dentro de un intento de reserva ya en curso (ej.
  eligiendo cuál activo de una lista que el bot ofreció, dando fecha/hora,
  corrigiendo un dato).
- consultar_disponibilidad: pregunta qué activos existen, cuáles están
  libres/ocupados, o pide información del catálogo sin pedir reservar
  todavía (ej. "qué cámaras tienen", "qué hay disponible", "tienen
  micrófonos").
- cancelar: quiere cancelar una reserva que ya tiene hecha.
- ayuda: pregunta qué puede hacer el bot o cómo usarlo.
- otro: cualquier otra cosa (saludos sueltos, temas no relacionados, etc.)

Responde solo la palabra de la categoría."""


def clasificar_intent(texto_usuario: str) -> str:
    """Clasifica el intent del mensaje con el LLM local (mismo endpoint que
    `extraer_datos_reserva`). Si el LLM no responde, tarda demasiado, o
    responde algo que no es una de las categorías válidas, cae al
    heurístico de palabras clave -- mejor una clasificación aproximada que
    dejar el mensaje sin clasificar y bloqueado por la whitelist."""
    base_url = current_app.config["LLM_BASE_URL"].rstrip("/")
    modelo = current_app.config["LLM_MODEL"]
    url = f"{base_url}/v1/chat/completions"

    payload = {
        "model": modelo,
        "messages": [
            {"role": "system", "content": PROMPT_CLASIFICACION},
            {"role": "user", "content": texto_usuario},
        ],
        "temperature": 0,
        "max_tokens": 2000,  # igual que extraer_datos_reserva: ver comentario ahí sobre por qué 700 se quedaba corto
    }

    try:
        resp = requests.post(url, json=payload, headers=_headers(), timeout=150)
        resp.raise_for_status()
        mensaje = resp.json()["choices"][0]["message"]
        contenido = (mensaje.get("content") or mensaje.get("reasoning_content") or "").strip().lower()
    except (requests.RequestException, KeyError, IndexError) as exc:
        current_app.logger.error("Error consultando el LLM para clasificar intent: %s", exc)
        return _clasificar_intent_heuristico(texto_usuario)

    for intent in INTENTS_VALIDOS:
        if intent in contenido:
            return intent

    current_app.logger.warning("El LLM devolvió un intent no reconocido: %r", contenido)
    return _clasificar_intent_heuristico(texto_usuario)
ca

def _clasificar_intent_heuristico(texto_usuario: str) -> str:
    """Heurística de respaldo por palabras clave -- solo se usa si el LLM
    no está disponible o responde algo irreconocible (ver `clasificar_intent`).
    Antes era la única lógica de clasificación; se queda como red de
    seguridad porque es instantánea y no depende de ningún servicio externo.

    "cancel" se revisa antes que "reserv" a propósito: "cancelar mi
    reserva" contiene la subcadena "reserv" (de "reserva"), así que si el
    orden fuera al revés, cancelar algo caería mal clasificado como
    "reservar"."""
    texto = normalizar(texto_usuario)
    if "cancel" in texto:
        return "cancelar"
    if any(p in texto for p in ("reserv", "aparta", "necesito")):
        return "reservar"
    if any(p in texto for p in ("disponib", "libre", "horario")):
        return "consultar_disponibilidad"
    if "ayuda" in texto or "help" in texto:
        return "ayuda"
    return "otro"


def _headers() -> dict:
    api_key = current_app.config.get("LLM_API_KEY")
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def generar_respuesta(texto_usuario: str) -> str:
    base_url = current_app.config["LLM_BASE_URL"].rstrip("/")
    modelo = current_app.config["LLM_MODEL"]
    url = f"{base_url}/v1/chat/completions"

    payload = {
        "model": modelo,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": texto_usuario},
        ],
        "temperature": 0.3,
        "max_tokens": 300,
    }

    try:
        resp = requests.post(url, json=payload, headers=_headers(), timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except (requests.RequestException, KeyError, IndexError) as exc:
        current_app.logger.error("Error consultando el LLM local: %s", exc)
        return (
            "Tuve un problema procesando tu mensaje. Por favor intenta de "
            "nuevo en un momento, o escribe 'ayuda'."
        )


_DIAS_ES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

PROMPT_EXTRACCION = """Eres un extractor de datos, no un asistente conversacional.
Dado un mensaje de un usuario que quiere reservar un activo de la CPA
(Centro de Producción Audiovisual), devuelve SOLO un objeto JSON (sin
markdown, sin explicación, sin texto antes o después) con estas claves:

- "recurso": el nombre EXACTO (copiado tal cual) de uno de estos activos,
  solo si el mensaje lo menciona con claridad; si no, null.
  Activos disponibles: {nombres_recursos}
- "inicio": fecha y hora de inicio solicitada, formato "YYYY-MM-DD HH:MM"
  (24 horas), o null si el mensaje no da información suficiente para
  calcularla.
- "fin": fecha y hora de fin, mismo formato. Si el usuario da una
  duración en vez de hora de fin (ej. "por 2 horas"), calcúlala sumando
  esa duración al inicio. Si no hay ninguna pista de duración ni de fin,
  asume 1 hora de duración. null si no se pudo calcular "inicio".

Hoy es {dia_semana} {fecha}, son las {hora} (hora de Colombia, esa es tu
única zona horaria de referencia). Usa esto para resolver fechas
relativas ("hoy", "mañana", "el viernes", "en 2 horas", etc.) -- si el
usuario nombra un día de la semana, asume el próximo que ocurra a partir
de hoy (inclusive si es hoy mismo y la hora todavía no pasó).

Responde solo el JSON."""


def extraer_datos_reserva(
    texto_usuario: str, nombres_recursos: list[str], ahora_local: datetime
) -> dict | None:
    """Le pide al LLM que extraiga {recurso, inicio, fin} del mensaje del
    usuario como JSON estructurado. El LLM SOLO interpreta lenguaje
    natural aquí -- nunca decide si hay disponibilidad ni escribe en la
    base de datos, eso lo valida recursos_service con los datos reales
    (ver docs/ARQUITECTURA.md). Devuelve None si el LLM no respondió con
    JSON parseable; el llamador debe tratarlo como "no se pudo extraer
    nada" y pedirle al usuario que reformule, no como error fatal."""
    base_url = current_app.config["LLM_BASE_URL"].rstrip("/")
    modelo = current_app.config["LLM_MODEL"]
    url = f"{base_url}/v1/chat/completions"

    system_prompt = PROMPT_EXTRACCION.format(
        nombres_recursos=", ".join(nombres_recursos) if nombres_recursos else "(ninguno cargado)",
        dia_semana=_DIAS_ES[ahora_local.weekday()],
        fecha=ahora_local.strftime("%d/%m/%Y"),
        hora=ahora_local.strftime("%H:%M"),
    )

    payload = {
        "model": modelo,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": texto_usuario},
        ],
        "temperature": 0,
        # Algunos modelos locales (ej. lfm2.5, otros "razonadores") gastan
        # buena parte del presupuesto de tokens en un campo separado de
        # cadena de pensamiento ("reasoning_content") antes de escribir la
        # respuesta final -- con un max_tokens bajo se cortan a mitad de
        # razonar y "content" llega vacío o incompleto. Medido en la
        # práctica con lfm2.5-2.6b: un mensaje simple ya gastó 523 tokens
        # solo en razonar antes de escribir un JSON de 3 campos, así que
        # 700 se quedaba corto -- 2000 da margen real.
        "max_tokens": 2000,
    }

    try:
        # El modelo local en uso razona antes de responder y puede tardar
        # bastante más que una respuesta conversacional corta (se han visto
        # 40-50s con max_tokens=2000) -- timeout generoso para no cortarlo
        # a mitad de camino, ya que eso es indistinguible de "no hay red".
        resp = requests.post(url, json=payload, headers=_headers(), timeout=150)
        resp.raise_for_status()
        mensaje = resp.json()["choices"][0]["message"]
        # Fallback: si el modelo dejó "content" vacío pero sí razonó, el
        # JSON a veces queda dentro de "reasoning_content" en vez de
        # "content" -- lo intentamos igual antes de rendirnos.
        contenido = (mensaje.get("content") or mensaje.get("reasoning_content") or "").strip()
    except (requests.RequestException, KeyError, IndexError) as exc:
        current_app.logger.error("Error consultando el LLM para extracción de reserva: %s", exc)
        return None

    inicio_json = contenido.find("{")
    fin_json = contenido.rfind("}")
    if inicio_json == -1 or fin_json == -1:
        current_app.logger.warning("El LLM no devolvió JSON en extracción de reserva: %r", contenido)
        return None

    try:
        datos = json.loads(contenido[inicio_json : fin_json + 1])
    except json.JSONDecodeError:
        current_app.logger.warning("JSON inválido del LLM en extracción de reserva: %r", contenido)
        return None

    if not isinstance(datos, dict):
        return None

    return {
        "recurso": datos.get("recurso") or None,
        "inicio": datos.get("inicio") or None,
        "fin": datos.get("fin") or None,
    }
