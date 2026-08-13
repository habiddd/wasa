"""
Dashboard interno de monitoreo para el equipo de la CPA: estado de los
servicios (Postgres, Redis, Baileys/WhatsApp, LLM local), código QR de
vinculación y métricas básicas de uso. Protegido con autenticación básica
(DASHBOARD_USER / DASHBOARD_PASSWORD) porque expone el QR de vinculación
de WhatsApp, que no debería poder escanear cualquiera con acceso a la red.
"""

import base64
import io
import secrets
from datetime import datetime, timedelta

import qrcode
import requests
from flask import Blueprint, Response, current_app, jsonify, render_template, request
from sqlalchemy import func, text

from app import estado_bot
from app.extensions import db, redis_client
from app.models import AuditoriaBot, Reserva

bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


@bp.before_request
def _requiere_auth():
    password_esperada = current_app.config["DASHBOARD_PASSWORD"]
    if not password_esperada:
        current_app.logger.warning(
            "DASHBOARD_PASSWORD no configurada: /dashboard queda deshabilitado."
        )
        return Response("Dashboard no configurado (falta DASHBOARD_PASSWORD).", 503)

    auth = request.authorization
    usuario_ok = auth and secrets.compare_digest(auth.username or "", current_app.config["DASHBOARD_USER"])
    password_ok = auth and secrets.compare_digest(auth.password or "", password_esperada)

    if not (usuario_ok and password_ok):
        return Response(
            "Autenticación requerida.",
            401,
            {"WWW-Authenticate": 'Basic realm="Dashboard CPA"'},
        )


# --- Chequeos de estado de cada servicio ------------------------------------

def _check_postgres() -> dict:
    try:
        db.session.execute(text("SELECT 1"))
        return {"ok": True, "detalle": "conectado"}
    except Exception as exc:  # noqa: BLE001 - queremos capturar cualquier fallo de DB
        return {"ok": False, "detalle": str(exc)}


def _check_redis() -> dict:
    try:
        redis_client.ping()
        return {"ok": True, "detalle": "conectado"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detalle": str(exc)}


def _consultar_baileys() -> dict | None:
    base_url = current_app.config["WHATSAPP_API_URL"].rstrip("/")
    try:
        resp = requests.get(f"{base_url}/status", timeout=3)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return None


def _check_baileys(estado: dict | None) -> dict:
    if estado is None:
        return {"ok": False, "detalle": "no responde"}
    return {
        "ok": True,
        "detalle": estado.get("status", "desconocido"),
        "whatsapp_conectado": estado.get("status") == "open",
        "numero": estado.get("phoneNumber"),
    }


def _check_llm() -> dict:
    base_url = current_app.config["LLM_BASE_URL"].rstrip("/")
    api_key = current_app.config.get("LLM_API_KEY")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        resp = requests.get(f"{base_url}/v1/models", headers=headers, timeout=3)
        resp.raise_for_status()
        return {"ok": True, "detalle": "conectado"}
    except requests.RequestException as exc:
        return {"ok": False, "detalle": str(exc)}


# --- Rutas -------------------------------------------------------------------

@bp.route("/")
def index():
    return render_template("dashboard.html")


@bp.route("/api/status")
def api_status():
    return jsonify(
        {
            "postgres": _check_postgres(),
            "redis": _check_redis(),
            "baileys": _check_baileys(_consultar_baileys()),
            "llm": _check_llm(),
            "flask": {"ok": True, "detalle": "respondiendo"},
            "chequeado_en": datetime.utcnow().isoformat() + "Z",
        }
    )


@bp.route("/api/qr")
def api_qr():
    estado = _consultar_baileys() or {}
    conectado = estado.get("status") == "open"

    qr_imagen = None
    qr_texto = estado_bot.obtener_qr()
    if not conectado and qr_texto:
        buffer = io.BytesIO()
        qrcode.make(qr_texto).save(buffer, format="PNG")
        qr_imagen = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()

    return jsonify(
        {
            "conectado": conectado,
            "numero": estado.get("phoneNumber"),
            "qr_imagen": qr_imagen,
        }
    )


@bp.route("/api/metricas")
def api_metricas():
    desde = datetime.utcnow() - timedelta(days=30)

    reservas_por_dia = (
        db.session.query(
            func.date(Reserva.inicio).label("dia"),
            func.count(Reserva.id).label("total"),
        )
        .filter(Reserva.inicio >= desde)
        .group_by(func.date(Reserva.inicio))
        .order_by(func.date(Reserva.inicio))
        .all()
    )

    total_bloqueadas_30d = (
        db.session.query(func.count(AuditoriaBot.id))
        .filter(AuditoriaBot.creado_en >= desde, AuditoriaBot.permitido.is_(False))
        .scalar()
    )

    ultimas_interacciones = (
        db.session.query(AuditoriaBot)
        .order_by(AuditoriaBot.creado_en.desc())
        .limit(20)
        .all()
    )

    return jsonify(
        {
            "reservas_por_dia": [{"dia": str(f.dia), "total": f.total} for f in reservas_por_dia],
            "total_reservas_30d": sum(f.total for f in reservas_por_dia),
            "total_bloqueadas_30d": total_bloqueadas_30d or 0,
            "ultimas_interacciones": [
                {
                    "telefono_wa": i.telefono_wa,
                    "intent": i.intent_detectado,
                    "permitido": i.permitido,
                    "motivo_bloqueo": i.motivo_bloqueo,
                    "creado_en": i.creado_en.isoformat() if i.creado_en else None,
                }
                for i in ultimas_interacciones
            ],
        }
    )
