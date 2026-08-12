"""
Estado en memoria del canal de WhatsApp (último QR de vinculación recibido
de Baileys). Un solo proceso Flask, por eso alcanza con variables de módulo;
si se corre con más de un worker de Gunicorn, mover esto a Redis (mismo caso
que el rate limiter en guardrails.py).
"""

_ultimo_qr: str | None = None


def guardar_qr(qr: str | None) -> None:
    global _ultimo_qr
    _ultimo_qr = qr


def obtener_qr() -> str | None:
    return _ultimo_qr
