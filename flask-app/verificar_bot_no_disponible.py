"""
Qué dice el bot cuando le nombran un equipo sin unidades disponibles
(docs/PRUEBAS_NO_DEVUELTO_2026-09-28.md, sección "El bot..."). No guarda nada:
pone la Canon EOS R (2) y el Rode NTG3 (4) en 'no_devuelto' dentro de una
transacción que se revierte al final (con el lente 24-70 = 19, que sí sigue así,
y el trípode 3 disponible). No manda mensajes.

    docker exec -i lacs_flask_app python - < flask-app/verificar_bot_no_disponible.py
"""

from app import create_app
from app import recursos_service as R
from app.extensions import db
from app.models import Recurso, Usuario

app = create_app()
with app.app_context():
    for i in (2, 4):
        db.session.get(Recurso, i).estado = "no_devuelto"
    db.session.flush()
    assert [db.session.get(Recurso, i).estado for i in (3, 19)] == ["disponible", "no_devuelto"]

    def consulta(texto):
        respuesta = R.evaluar_consulta_disponibilidad(texto)
        print(f"  consulta {texto!r}\n    -> {respuesta!r}")
        return respuesta

    r = consulta("tienen la cámara canon eos r?")
    assert "Cámara Canon EOS R: no está disponible" in r and "R6" not in r
    r = consulta("tienen la canon eos r6?")
    assert "EOS R6" in r and "no está disponible" not in r
    r = consulta("hay cámaras canon?")  # empate: se muestran las dos, cada una con su estado
    assert "EOS R6: libre" in r and "Cámara Canon EOS R: no está disponible" in r
    r = consulta("el micrófono rode ntg3 está libre?")
    assert "Micrófono Rode NTG3: no está disponible" in r
    r = consulta("tienen el lente sony 24-70?")
    assert "Lente Sony 24-70mm f/2.8: no está disponible" in r
    r = consulta("tienen el trípode manfrotto?")  # recuperado: vuelve a estar
    assert "no está disponible" not in r

    habid = db.session.get(Usuario, 24)
    mensaje, sesion = R._iniciar_reserva("quiero reservar la cámara canon eos r", habid, {})
    print(f"  reservar 'cámara canon eos r'\n    -> {mensaje!r}")
    assert "no se puede reservar" in mensaje and sesion == {}
    mensaje, sesion = R._iniciar_reserva("quiero reservar la canon eos r6", habid, {})
    print(f"  reservar 'canon eos r6'\n    -> {mensaje[:120]!r}...")
    assert "reserva_wizard" in sesion
    mensaje, sesion = R._iniciar_reserva("quiero reservar una cámara canon", habid, {})  # empate: sigue normal
    print(f"  reservar 'cámara canon'\n    -> {mensaje[:120]!r}...")
    assert "reserva_wizard" in sesion
    db.session.rollback()

print("OK: el bot dice 'no está disponible' por lo no disponible/no devuelto, no lo cambia por otro modelo y no deja reservarlo.")
