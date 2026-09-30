"""
Prueba de sistema del aviso de devolución (docs/PLAN_aviso_devolucion_2026-09-29.md).
Con habid (telefono 573045246117; el aviso de A le llega de verdad por WhatsApp) y el reloj
real. A C se le marcan de antemano los avisos del ciclo de no-show como ya
resueltos, para que habid no reciba un "recordatorio" de una reserva de prueba:
  A: 'entregada' con fin = ahora+20 min  -> una fila (gid, 'devolucion') y un
     mensaje aviso_devolucion en auditoria_bot con "20 minutos" o "19 minutos";
     un segundo barrer() no duplica nada.
  B: 'entregada' con fin = ahora+2 h     -> sin fila de devolución.
  C: 'confirmada' con fin = ahora+20 min -> sin fila de devolución.
Al final borra SOLO los grupos creados (el historial de estados queda como
evidencia, igual que en los otros verificar_*.py).

    docker exec -i lacs_flask_app python - < flask-app/verificar_aviso_devolucion.py
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app import avisos_reservas, create_app, recursos_service
from app.extensions import db
from app.models import AuditoriaBot, Recurso, Reserva, ReservaAviso, ReservaGrupo, Usuario

API = "/dashboard/api"

app = create_app()
with app.app_context():
    HABID = Usuario.query.filter_by(telefono_wa="573045246117").one().id
    cliente = app.test_client()
    auth = (app.config["DASHBOARD_USER"], app.config["DASHBOARD_PASSWORD"])
    ahora = datetime.now(timezone.utc).replace(microsecond=0)

    # (estado, inicio, fin) de A, B y C.
    casos = {
        "A": ("entregada", ahora - timedelta(hours=1), ahora + timedelta(minutes=20)),
        "B": ("entregada", ahora - timedelta(hours=1), ahora + timedelta(hours=2)),
        "C": ("confirmada", ahora + timedelta(minutes=5), ahora + timedelta(minutes=20)),
    }
    libres = [r for r in Recurso.query.filter_by(estado="disponible", tipo="equipo").order_by(Recurso.id)
              if all(recursos_service._reserva_solapada(r.id, i, f) is None for _, i, f in casos.values())][:3]
    assert len(libres) == 3, "no hay tres equipos libres para la prueba"

    gids = {}
    try:
        for (nombre, (estado, inicio, fin)), recurso in zip(casos.items(), libres):
            grupo = ReservaGrupo(usuario_id=HABID)
            db.session.add(grupo)
            db.session.flush()
            db.session.add(Reserva(usuario_id=HABID, recurso_id=recurso.id, grupo_id=grupo.id,
                                   canal_origen="dashboard", inicio=inicio, fin=fin, estado=estado))
            gids[nombre] = grupo.id
        for tipo in ("previo", "hora", "no_show"):
            db.session.add(ReservaAviso(grupo_id=gids["C"], tipo=tipo, enviado=False, motivo="vencido"))
        db.session.commit()

        def filas(gid):
            return ReservaAviso.query.filter_by(grupo_id=gid, tipo="devolucion").count()

        avisos_reservas.barrer()
        avisos_reservas.barrer()  # el segundo no duplica (UNIQUE + ya avisado)
        db.session.expire_all()

        assert filas(gids["A"]) == 1, filas(gids["A"])
        assert filas(gids["B"]) == 0 and filas(gids["C"]) == 0, (filas(gids["B"]), filas(gids["C"]))

        marca = f"reserva N.º {gids['A']} "
        textos = [a.respuesta_enviada for a in AuditoriaBot.query.filter_by(
            intent_detectado="aviso_devolucion", telefono_wa="573045246117") if marca in a.respuesta_enviada]
        assert len(textos) == 1, textos
        assert "20 minutos" in textos[0] or "19 minutos" in textos[0], textos[0]
    finally:
        db.session.rollback()
        for gid in gids.values():
            cliente.delete(f"{API}/reservas/grupo/{gid}", auth=auth)

    restos = db.session.execute(text("SELECT count(*) FROM reserva_grupos WHERE id = ANY(:ids)"),
                                {"ids": list(gids.values())}).scalar()
    assert restos == 0, restos

print(f"OK: grupo A {gids['A']} -> una fila 'devolucion' y un aviso ({textos[0]!r}); "
      f"B {gids['B']} (fin en 2 h) y C {gids['C']} (confirmada) sin aviso; segundo barrer() sin duplicar. Prueba borrada.")
