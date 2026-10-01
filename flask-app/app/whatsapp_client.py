"""
Cliente para enviar mensajes de texto a WhatsApp por dos canales, a elegir
desde el dashboard (Servicios):
- Baileys: librería de Node.js que conecta directamente a WhatsApp (sin la
  API oficial de Meta). Este módulo envía mensajes al servicio de Baileys
  que corre en Docker.
- Meta: la Cloud API oficial (graph.facebook.com).
El canal activo vive en Redis (`whatsapp:proveedor`); sin clave, el de
WHATSAPP_PROVEEDOR_DEFAULT.
"""

import requests
from flask import current_app

from app import usuarios_service
from app.extensions import redis_client

PROVEEDORES = ("baileys", "meta")
_CLAVE_PROVEEDOR = "whatsapp:proveedor"
# Sin estas, Meta no puede ni enviar ni recibir (ver routes/webhook.py).
VARIABLES_META = ("META_WA_TOKEN", "META_WA_PHONE_NUMBER_ID", "META_APP_SECRET", "META_WEBHOOK_VERIFY_TOKEN")


def proveedor_activo() -> str:
    try:
        valor = redis_client.get(_CLAVE_PROVEEDOR)
    except Exception:  # noqa: BLE001 - Redis caído: se cae al canal por defecto
        valor = None
    valor = valor.decode() if valor else current_app.config["WHATSAPP_PROVEEDOR_DEFAULT"]
    return valor if valor in PROVEEDORES else "baileys"


def meta_faltantes() -> list[str]:
    return [v for v in VARIABLES_META if not current_app.config.get(v)]


def cambiar_proveedor(proveedor: str) -> list[str]:
    """Devuelve las variables de .env que faltan (vacía = cambió). A Baileys
    siempre se puede volver."""
    if proveedor not in PROVEEDORES:
        raise ValueError(proveedor)
    faltan = meta_faltantes() if proveedor == "meta" else []
    if not faltan:
        redis_client.set(_CLAVE_PROVEEDOR, proveedor)
    return faltan


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
    enviar_mensaje_texto). Con Meta el LID no sirve: solo se escribe por
    número o BSUID."""
    if proveedor_activo() == "meta":
        if usuarios_service.telefono_es_lid(usuario):
            return usuario.jid_wa  # @lid: _enviar_meta lo rechaza; @bsuid: sale por recipient
        return f"{usuario.telefono_wa}@s.whatsapp.net"
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
    `mensaje_id`. Con Meta el canal es otro (_enviar_meta): el ack llega por
    POST /webhook/meta y `mensaje_id` es el wamid.
    """
    if proveedor_activo() == "meta":
        return _enviar_meta(jid_destino, texto, timeout)

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


def _escribio_en_24h(numero: str) -> bool:
    # Solo Redis: la auditoría mezcla mensajes de los dos canales y la ventana
    # de Meta solo la abre un mensaje que llegó por Meta (ver routes/webhook.py).
    return bool(redis_client.exists(f"meta:ventana:{numero}"))


def _enviar_meta(jid_destino: str, texto: str, timeout: int) -> tuple[bool, str | None]:
    """Cloud API. Texto libre solo si la persona escribió en las últimas 24 h;
    si no, la plantilla META_PLANTILLA_AVISO con el texto aplanado (los
    parámetros de plantilla no admiten saltos de línea).
    ponytail: una sola plantilla genérica; si Meta la rechaza o se quieren
    textos por tipo de aviso, pasar a una por tipo."""
    cfg = current_app.config
    if jid_destino.endswith("@bsuid"):
        numero, destino = jid_destino.removesuffix("@bsuid"), "recipient"
    elif jid_destino.endswith("@s.whatsapp.net"):
        numero, destino = jid_destino.split("@")[0], "to"
    else:
        current_app.logger.error("Meta necesita número o BSUID, no el JID %s: no se envía.", jid_destino)
        return False, None

    payload = {"messaging_product": "whatsapp", "recipient_type": "individual", destino: numero}
    if _escribio_en_24h(numero):
        payload.update(type="text", text={"body": texto})
    elif cfg["META_PLANTILLA_AVISO"]:
        # Meta: sin \n ni \t ni más de 4 espacios seguidos, y el cuerpo entero ≤ 1024 (el texto fijo de lacs_aviso ocupa ~96).
        plano = " · ".join(" ".join(l.split()) for l in texto.splitlines() if l.strip())
        plano = plano if len(plano) <= 900 else plano[:899] + "…"
        payload.update(type="template", template={
            "name": cfg["META_PLANTILLA_AVISO"],
            "language": {"code": cfg["META_PLANTILLA_IDIOMA"]},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": plano}]}],
        })
    else:
        current_app.logger.error("Fuera de la ventana de 24 h y sin META_PLANTILLA_AVISO: no se envía a %s.", numero)
        return False, None

    return _post_meta(payload, timeout)


def _post_meta(payload: dict, timeout: int) -> tuple[bool, str | None]:
    cfg = current_app.config
    url = f"https://graph.facebook.com/{cfg['META_GRAPH_VERSION']}/{cfg['META_WA_PHONE_NUMBER_ID']}/messages"
    try:
        resp = requests.post(
            url, json=payload, timeout=timeout,
            headers={"Authorization": f"Bearer {cfg['META_WA_TOKEN']}"},
        )
        if not resp.ok:
            # El cuerpo trae error.code (190 token, 131030, 132xxx plantilla…).
            # ponytail: sin reenviar como plantilla al recibir 131047; el texto libre ya solo sale con la ventana abierta.
            current_app.logger.error("Meta rechazó el envío (%s): %s", resp.status_code, resp.text[:500])
            return False, None
        return True, resp.json()["messages"][0]["id"]
    except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
        current_app.logger.error("Error enviando mensaje por Meta: %s", exc)
        return False, None
