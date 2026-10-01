"""
Concurrencia de reservas: la garantía de no-solapamiento la da Postgres
(restricción EXCLUDE reservas_no_solapamiento sobre recurso_id + tstzrange),
no el chequeo previo de la app.

  1. Carrera: N hilos, cada uno con su propia sesión, consultan
     `_reserva_solapada` a la vez (todos ven el equipo libre) y después llaman
     a `crear_reservas_grupo` a la vez. Debe quedar exactamente UNA reserva;
     las otras N-1 vuelven con "choque".
  2. Solapamiento parcial (misma franja corrida 1 h): "choque".
  3. Franja contigua ([fin, fin+2h)): se acepta, el rango es semiabierto.
  4. Una reserva cancelada no ocupa: se puede reservar encima.

Usa al usuario habid (573045246117), 60 días en el futuro y de madrugada, para
no cruzarse con reservas reales ni disparar avisos. Borra todo lo que crea.

    docker exec -i lacs_flask_app python - < entregables/OE3/pruebas/verificar_concurrencia.py
"""

import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app import create_app, recursos_service
from app.extensions import db
from app.models import Recurso, Reserva, Usuario

N = 8
API = "/dashboard/api"

app = create_app()
with app.app_context():
    HABID = Usuario.query.filter_by(telefono_wa="573045246117").one().id
    cliente = app.test_client()
    auth = (app.config["DASHBOARD_USER"], app.config["DASHBOARD_PASSWORD"])
    dia = (datetime.now(timezone.utc) + timedelta(days=60)).replace(hour=8, minute=0, second=0, microsecond=0)
    inicio, fin = dia, dia + timedelta(hours=2)  # 03:00-05:00 hora Colombia
    recurso = next(
        r for r in Recurso.query.filter_by(estado="disponible", tipo="equipo", requiere_confirmacion=False).order_by(Recurso.id)
        if recursos_service._reserva_solapada(r.id, inicio - timedelta(hours=2), fin + timedelta(hours=4)) is None
    )
    RID = recurso.id

gids: list[int] = []
vio_libre: list[bool] = []
resultados: list[str | None] = []
lock = threading.Lock()
barrera = threading.Barrier(N)


def intentar():
    with app.app_context():
        libre = recursos_service._reserva_solapada(RID, inicio, fin) is None
        barrera.wait()  # todos ya chequearon antes de que alguno inserte
        r = db.session.get(Recurso, RID)
        reservas, error = recursos_service.crear_reservas_grupo(HABID, [r], inicio, fin, canal_origen="dashboard")
        with lock:
            vio_libre.append(libre)
            resultados.append(error)
            if reservas:
                gids.append(reservas[0].grupo_id)
        db.session.remove()


def crear(i, f):
    reservas, error = recursos_service.crear_reservas_grupo(
        HABID, [db.session.get(Recurso, RID)], i, f, canal_origen="dashboard")
    if reservas:
        gids.append(reservas[0].grupo_id)
    return reservas, error


try:
    hilos = [threading.Thread(target=intentar) for _ in range(N)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    with app.app_context():
        assert all(vio_libre), vio_libre  # el chequeo de la app no ve la carrera...
        assert resultados.count(None) == 1, resultados  # ...pero Postgres deja pasar solo una
        assert resultados.count("choque") == N - 1, resultados
        ocupan = Reserva.query.filter(Reserva.recurso_id == RID, Reserva.inicio == inicio, Reserva.fin == fin,
                                      Reserva.estado != "cancelada").count()
        assert ocupan == 1, ocupan

        _, error = crear(inicio + timedelta(hours=1), fin + timedelta(hours=1))
        assert error == "choque", error

        contigua, error = crear(fin, fin + timedelta(hours=2))
        assert error is None and contigua, error

        # Cancelar la contigua libera su franja: se puede volver a reservar.
        contigua[0].estado = "cancelada"
        db.session.commit()
        _, error = crear(fin, fin + timedelta(hours=2))
        assert error is None, error
finally:
    with app.app_context():
        for gid in gids:
            cliente.delete(f"{API}/reservas/grupo/{gid}", auth=auth)
        restos = db.session.execute(text("SELECT count(*) FROM reserva_grupos WHERE id = ANY(:ids)"),
                                    {"ids": gids}).scalar()
        assert restos == 0, restos

print(f"OK: {N} hilos vieron el recurso {RID} libre y solo 1 reservó ({N - 1} 'choque'); "
      "solapamiento parcial rechazado, franja contigua aceptada, cancelada no ocupa. Prueba borrada.")
