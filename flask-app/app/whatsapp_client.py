"""
Cliente para enviar mensajes de texto a WhatsApp vía Baileys.
Baileys es una librería de Node.js que conecta directamente a WhatsApp
(sin necesidad de la API oficial de Meta). Este módulo envía mensajes
al servicio de Baileys que corre en Docker.
"""

import requests
from flask import current_app


def _headers():
    return {
        "x-api-key": current_app.config["WHATSAPP_API_KEY"],
        "Content-Type": "application/json",
    }


def enviar_mensaje_texto(numero_destino: str, texto: str, timeout: int = 10) -> bool:
    """
    numero_destino: número en formato internacional sin '+' (ej: 573001234567)
    """
    base_url = current_app.config["WHATSAPP_API_URL"].rstrip("/")
    url = f"{base_url}/send"

    payload = {
        "number": numero_destino,
        "text": texto,
    }

    try:
        resp = requests.post(url, json=payload, headers=_headers(), timeout=timeout)
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        current_app.logger.error("Error enviando mensaje por Baileys: %s", exc)
        return False
