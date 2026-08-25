"""
Memoria de conversación por número de WhatsApp. Cada mensaje entrante le
llega a Flask como un webhook HTTP sin estado -- si el bot pregunta algo
en un mensaje y espera la respuesta en el siguiente, necesita guardar en
algún lado en qué punto de la conversación se quedó. Eso es lo que vive
aquí: un blob JSON pequeño por número, en Redis, con expiración automática
a los 7 días de inactividad (cada lectura/escritura renueva el TTL, así
que una conversación activa no se corta a media pregunta).

Hoy lo usa registro_service.py para el flujo de alta de usuario ("¿cuál es
tu nombre?" / "¿cuál es tu código?" / ...). Es un punto de extensión
genérico: cualquier flujo futuro que necesite recordar contexto entre
mensajes (ej. una aclaración de qué recurso quiso decir el usuario) puede
guardar su propio estado aquí bajo su propia clave.
"""

import json

from flask import current_app

from app.extensions import redis_client

TTL_SESION_SEGUNDOS = 7 * 24 * 60 * 60  # 7 días
_PREFIJO = "sesion:"


def obtener_sesion(telefono_wa: str) -> dict:
    try:
        crudo = redis_client.get(_PREFIJO + telefono_wa)
    except Exception as exc:  # noqa: BLE001 - Redis caído no debe tumbar el webhook
        current_app.logger.error("Error leyendo sesión de Redis para %s: %s", telefono_wa, exc)
        return {}

    if not crudo:
        return {}
    try:
        datos = json.loads(crudo)
        return datos if isinstance(datos, dict) else {}
    except json.JSONDecodeError:
        return {}


def guardar_sesion(telefono_wa: str, datos: dict) -> None:
    try:
        redis_client.set(_PREFIJO + telefono_wa, json.dumps(datos), ex=TTL_SESION_SEGUNDOS)
    except Exception as exc:  # noqa: BLE001
        current_app.logger.error("Error guardando sesión de Redis para %s: %s", telefono_wa, exc)


def limpiar_sesion(telefono_wa: str) -> None:
    try:
        redis_client.delete(_PREFIJO + telefono_wa)
    except Exception as exc:  # noqa: BLE001
        current_app.logger.error("Error borrando sesión de Redis para %s: %s", telefono_wa, exc)
