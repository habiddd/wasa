"""
Envoltorio delgado sobre la API REST de Evolution API.
Como Evolution API expone el mismo contrato sin importar si por debajo usa
Baileys o la Cloud API de Meta, este módulo no debería necesitar cambios
cuando migres de canal — solo cambia la config de Evolution API.
"""

import requests
from flask import current_app


def _headers():
    return {
        "apikey": current_app.config["EVOLUTION_API_KEY"],
        "Content-Type": "application/json",
    }


def enviar_mensaje_texto(numero_destino: str, texto: str, timeout: int = 10) -> bool:
    """
    numero_destino: número en formato internacional sin '+' (ej: 573001234567)
    """
    base_url = current_app.config["EVOLUTION_API_URL"]
    instancia = current_app.config["EVOLUTION_INSTANCE_NAME"]
    url = f"{base_url}/message/sendText/{instancia}"

    payload = {
        "number": numero_destino,
        "text": texto,
    }

    try:
        resp = requests.post(url, json=payload, headers=_headers(), timeout=timeout)
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        current_app.logger.error("Error enviando mensaje por Evolution API: %s", exc)
        return False


def crear_instancia(nombre_instancia: str, integracion: str = "WHATSAPP-BAILEYS") -> dict:
    """
    Crea una instancia nueva. Para migrar a Meta más adelante, cambia
    integracion a "WHATSAPP-BUSINESS" y agrega el token de Meta en la
    configuración de Evolution API (ver docker-compose.yml).
    """
    base_url = current_app.config["EVOLUTION_API_URL"]
    url = f"{base_url}/instance/create"
    payload = {
        "instanceName": nombre_instancia,
        "integration": integracion,
        "qrcode": integracion == "WHATSAPP-BAILEYS",
    }
    resp = requests.post(url, json=payload, headers=_headers(), timeout=15)
    resp.raise_for_status()
    return resp.json()
