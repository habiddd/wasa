"""
Cliente mínimo para tu LLM local. Asume un endpoint compatible con la API
de chat de OpenAI, que es lo que exponen tanto Ollama (`/v1/chat/completions`)
como LM Studio. Ajusta la ruta si tu setup difiere.
"""

import requests
from flask import current_app

SYSTEM_PROMPT = """Eres el asistente de reservas de la CPA (Centro de Producción
Audiovisual) de Uninorte. SOLO puedes ayudar con: reservar equipos/espacios,
consultar disponibilidad, cancelar una reserva, o dar ayuda sobre cómo usar
este chat. Si el mensaje pide cualquier otra cosa, responde amablemente que
no puedes ayudar con eso y sugiere escribir "ayuda". Nunca inventes
disponibilidad: si no tienes el dato, dilo explícitamente. Sé breve y claro,
como si hablaras por WhatsApp."""


def clasificar_intent(texto_usuario: str) -> str:
    """
    Punto de extensión: aquí va tu lógica real de NLU/LLM para detectar
    el intent (reservar, consultar_disponibilidad, cancelar, ayuda, otro).
    De momento hace un match simple por palabras clave como placeholder
    para que el flujo funcione end-to-end desde el primer día.
    """
    texto = texto_usuario.lower()
    if any(p in texto for p in ("reserv", "aparta", "necesito")):
        return "reservar"
    if any(p in texto for p in ("disponib", "libre", "horario")):
        return "consultar_disponibilidad"
    if "cancel" in texto:
        return "cancelar"
    if "ayuda" in texto or "help" in texto:
        return "ayuda"
    return "otro"


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
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except (requests.RequestException, KeyError, IndexError) as exc:
        current_app.logger.error("Error consultando el LLM local: %s", exc)
        return (
            "Tuve un problema procesando tu mensaje. Por favor intenta de "
            "nuevo en un momento, o escribe 'ayuda'."
        )
