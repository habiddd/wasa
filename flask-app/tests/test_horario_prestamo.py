"""Check del horario de atención y de la regla de préstamo largo (puro: sin
DB, sin LLM). Correr dentro del contenedor: python tests/test_horario_prestamo.py"""
from datetime import datetime, time, timedelta
from types import SimpleNamespace as NS

from app import recursos_service as R

REGLAS = NS(hora_apertura=time(8, 0), hora_cierre=time(18, 0))


def test_horario():
    assert R._error_horario((8, 0), "empezar", REGLAS) is None
    assert R._error_horario((18, 0), "terminar", REGLAS) is None
    assert R._error_horario((12, 30), "empezar", REGLAS) is None
    msg = R._error_horario((7, 0), "empezar", REGLAS)
    assert msg and "8:00 a.m. a 6:00 p.m." in msg and "empezar a las 7:00 a.m." in msg, msg
    assert R._error_horario((18, 30), "terminar", REGLAS)
    assert R._error_horario((17, 30), "terminar", NS(hora_apertura=time(8), hora_cierre=time(17)))


def test_prestamo_largo():
    ini = datetime(2026, 10, 19, 8, 0)
    assert not R.es_prestamo_largo(ini, ini + timedelta(days=2))
    assert not R.es_prestamo_largo(ini, ini + timedelta(days=7))  # justo una semana: no
    assert R.es_prestamo_largo(ini, ini + timedelta(days=7, minutes=1))


def test_titulo_factura():
    linea = lambda nombre, estado: NS(estado=estado, recurso=NS(nombre=nombre))
    assert R._estado_de_reserva_creada([linea("Trípode Manfrotto", "confirmada")])[0] == "RESERVA CONFIRMADA"
    assert R._estado_de_reserva_creada([linea("Trípode Manfrotto", "pendiente")])[0] == "RESERVA PENDIENTE DE CONFIRMACIÓN"
    assert R._estado_de_reserva_creada([linea("A", "confirmada"), linea("B", "pendiente")])[0] == "RESERVA PARCIAL"


if __name__ == "__main__":
    test_horario()
    test_prestamo_largo()
    test_titulo_factura()
    print("horario/prestamo OK")
