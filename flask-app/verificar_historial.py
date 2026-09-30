"""
Verificación del historial de estados (reserva_estado_historial + vista
reserva_puntualidad) y de la bandera entrega_tardia. Usa al usuario habid
(telefono 573045246117), pero cada escenario se crea, transiciona y confirma en UNA sola
transacción: el barrido de avisos nunca ve una 'confirmada' de prueba, así
que no sale ningún WhatsApp. No escribe actas a disco (llama directo a
actas_service.aplicar_transicion).

    docker exec -i lacs_flask_app python - < flask-app/verificar_historial.py

No borra nada al final: las reservas y su historial quedan como evidencia
(ver docs/PRUEBAS_HISTORIAL_2026-09-28.md). La de "via_no_show" queda
'entregada' hasta 2 h después de la corrida y ocupa su equipo: si se vuelve a
correr antes, cambiar RECURSOS.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app import actas_service as A
from app import create_app
from app.extensions import db
from app.models import Recurso, Reserva, ReservaGrupo, Usuario

NOMBRES = ("Cámara Sony A7III", "Cámara Canon EOS R6", "Trípode Manfrotto", "Micrófono Rode NTG3")  # equipos libres ahora mismo; cambiar si chocan

app = create_app()
with app.app_context():
    HABID = Usuario.query.filter_by(telefono_wa="573045246117").one().id
    RECURSOS = [Recurso.query.filter_by(nombre=n).one().id for n in NOMBRES]
    ahora = datetime.now(timezone.utc)
    ids = {}

    def crear(nombre, recurso_id, inicio_min, fin_min=120, previo=None):
        g = ReservaGrupo(usuario_id=HABID)
        db.session.add(g)
        db.session.flush()
        r = Reserva(usuario_id=HABID, recurso_id=recurso_id, grupo_id=g.id, canal_origen="dashboard",
                    inicio=ahora + timedelta(minutes=inicio_min), fin=ahora + timedelta(minutes=fin_min))
        db.session.add(r)
        db.session.flush()
        if previo:
            r.estado = previo
            db.session.flush()
        _, err = A.aplicar_transicion([r], A.MOMENTO_ENTREGA)
        assert err is None, err
        db.session.flush()
        ids[nombre] = r.id
        return r

    # 1. Recoge 10 min ANTES y devuelve a tiempo.
    r = crear("temprano", RECURSOS[0], +10)
    assert not r.entrega_tardia
    A.aplicar_transicion([r], A.MOMENTO_DEVOLUCION)
    assert r.estado == "completada"
    db.session.commit()

    # 2. Recoge 10 min tarde, dentro del margen de no-show: no es tardía.
    r = crear("tarde_en_margen", RECURSOS[1], -10)
    assert not r.entrega_tardia
    db.session.commit()

    # 3. Recoge 45 min tarde SIN haber pasado por no_show (el barrido no
    #    corrió): antes quedaba entrega_tardia=False. Luego se le adelanta el
    #    fin (reprogramación, también debe quedar en el historial) y devuelve
    #    tarde.
    r = crear("tarde_sin_no_show", RECURSOS[2], -45)
    assert r.entrega_tardia, "entrega_tardia debía marcarse pasado el margen"
    r.fin = ahora - timedelta(minutes=1)
    db.session.flush()
    A.aplicar_transicion([r], A.MOMENTO_DEVOLUCION)
    assert r.estado == "devuelta_tarde"
    db.session.commit()

    # 4. Camino clásico: no_show y luego entrega tardía.
    r = crear("via_no_show", RECURSOS[3], -45, previo="no_show")
    assert r.entrega_tardia
    db.session.commit()

    # 5. Borrar la reserva completa (como api_eliminar_reserva_grupo): el
    #    historial debe sobrevivir.
    borrada = Reserva.query.get(ids["tarde_en_margen"])
    grupo = ReservaGrupo.query.get(borrada.grupo_id)
    db.session.delete(borrada)
    db.session.delete(grupo)
    db.session.commit()

    def filas(sql, **p):
        return db.session.execute(text(sql), p).all()

    hist = {n: [(f.estado_anterior, f.estado_nuevo) for f in filas(
        "SELECT estado_anterior, estado_nuevo FROM reserva_estado_historial "
        "WHERE reserva_id = :id ORDER BY id", id=i)] for n, i in ids.items()}
    esperado = {
        "temprano": [(None, "confirmada"), ("confirmada", "entregada"), ("entregada", "completada")],
        "tarde_en_margen": [(None, "confirmada"), ("confirmada", "entregada"), ("entregada", None)],
        # la fila (entregada, entregada) es el cambio de `fin`
        "tarde_sin_no_show": [(None, "confirmada"), ("confirmada", "entregada"),
                              ("entregada", "entregada"), ("entregada", "devuelta_tarde")],
        "via_no_show": [(None, "confirmada"), ("confirmada", "no_show"), ("no_show", "entregada")],
    }
    for n in esperado:
        print(f"{n:18} {hist[n]}")
        assert hist[n] == esperado[n], (n, hist[n])

    print("\nreserva_puntualidad:")
    for f in filas("SELECT reserva_id, evento, estado, round(extract(epoch FROM retraso)/60) AS min "
                   "FROM reserva_puntualidad WHERE reserva_id = ANY(:ids) ORDER BY reserva_id, ocurrido_en",
                   ids=list(ids.values())):
        print(f"  {f.reserva_id} {f.evento:10} {f.estado:15} retraso {int(f.min):+d} min")
    minutos = {(f.reserva_id, f.evento): f.min for f in filas(
        "SELECT reserva_id, evento, round(extract(epoch FROM retraso)/60) AS min "
        "FROM reserva_puntualidad WHERE reserva_id = ANY(:ids)", ids=list(ids.values()))}
    assert minutos[(ids["temprano"], "entrega")] == -10
    assert minutos[(ids["tarde_en_margen"], "entrega")] == 10  # sobrevivió al borrado
    assert minutos[(ids["tarde_sin_no_show"], "entrega")] == 45
    assert minutos[(ids["tarde_sin_no_show"], "devolucion")] == 1
    assert minutos[(ids["via_no_show"], "entrega")] == 45

    # 6. La app no puede reescribir la historia.
    for sql in ("DELETE FROM reserva_estado_historial WHERE reserva_id = :id",
                "UPDATE reserva_estado_historial SET estado_nuevo = 'completada' WHERE reserva_id = :id"):
        try:
            db.session.execute(text(sql), {"id": ids["tarde_sin_no_show"]})
            raise AssertionError(f"la app pudo ejecutar: {sql}")
        except ProgrammingError:
            db.session.rollback()
    print("\nLa app no puede UPDATE/DELETE en el historial: OK")
    # Sin limpieza: las reservas y su historial quedan como evidencia.
    print(f"\nTODO OK. reserva_id de prueba: {sorted(ids.values())}")
