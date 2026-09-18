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


def jid_de(usuario) -> str:
    """JID para escribirle a un usuario sin un mensaje suyo al que responder
    (ej. el aviso de que la LACS confirmó su reserva). Usa el del último
    mensaje que mandó (Usuario.jid_wa). Si no hay, porque no ha escrito
    desde que existe esa columna, lo rearma con el número: sirve para las
    cuentas "@s.whatsapp.net" pero no para las "@lid" (ver
    enviar_mensaje_texto)."""
    return usuario.jid_wa or f"{usuario.telefono_wa}@s.whatsapp.net"


def enviar_mensaje_texto(jid_destino: str, texto: str, timeout: int = 10) -> tuple[bool, str | None]:
    """
    jid_destino: JID completo tal cual llegó en el webhook entrante (ej:
    "573001234567@s.whatsapp.net" o "144186850431118@lid"). Hay que
    reenviar el JID completo, no solo el número: WhatsApp usa JIDs "@lid"
    para algunas cuentas, y reconstruirlo como "<numero>@s.whatsapp.net"
    manda el mensaje a un JID distinto que Baileys acepta sin error pero
    que WhatsApp nunca entrega (visto en producción con telefono_wa
    144186850431118).

    Devuelve (enviado, mensaje_id). `enviado` solo indica que Baileys aceptó
    el mensaje para enviarlo (ack a nivel HTTP) -- no que WhatsApp lo haya
    entregado. El estado de entrega real llega después, de forma asíncrona,
    vía POST /webhook/ack (ver app/routes/webhook.py) y se correlaciona por
    `mensaje_id`.
    """
    base_url = current_app.config["WHATSAPP_API_URL"].rstrip("/")
    url = f"{base_url}/send"

    payload = {
        "jid": jid_destino,
        "text": texto,
    }

    try:
        resp = requests.post(url, json=payload, headers=_headers(), timeout=timeout)
        resp.raise_for_status()
        return True, resp.json().get("id")
    except requests.RequestException as exc:
        current_app.logger.error("Error enviando mensaje por Baileys: %s", exc)
        return False, None
