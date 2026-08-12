from datetime import datetime, timedelta

from flask import Blueprint, jsonify
from sqlalchemy import func

from app.extensions import db
from app.models import Reserva

bp = Blueprint("analytics", __name__)


@bp.route("/api/analytics/reservas-por-dia", methods=["GET"])
def reservas_por_dia():
    """Últimos 30 días. Punto de partida para tu dashboard/back-office."""
    desde = datetime.utcnow() - timedelta(days=30)

    filas = (
        db.session.query(
            func.date(Reserva.inicio).label("dia"),
            func.count(Reserva.id).label("total"),
        )
        .filter(Reserva.inicio >= desde)
        .group_by(func.date(Reserva.inicio))
        .order_by(func.date(Reserva.inicio))
        .all()
    )

    return jsonify([{"dia": str(f.dia), "total": f.total} for f in filas])


@bp.route("/api/analytics/recursos-mas-usados", methods=["GET"])
def recursos_mas_usados():
    filas = (
        db.session.query(
            Reserva.recurso_id,
            func.count(Reserva.id).label("total"),
        )
        .filter(Reserva.estado != "cancelada")
        .group_by(Reserva.recurso_id)
        .order_by(func.count(Reserva.id).desc())
        .limit(10)
        .all()
    )

    return jsonify([{"recurso_id": f.recurso_id, "total": f.total} for f in filas])
