"""
Verificación de los equipos marcados al firmar un acta
(actas_service.aplicar_transicion con `ids`): lo que no se entrega se cancela,
y no se puede devolver lo que no se entregó. Solo objetos en memoria, nunca
se agregan a la sesión ni se hace commit -- no escribe nada en la base.

    docker exec -i lacs_flask_app python - < flask-app/verificar_equipos_acta.py
"""

from datetime import datetime, timedelta, timezone

from app import actas_service, create_app
from app.models import Reserva

app = create_app()
with app.app_context():
    ahora = datetime.now(timezone.utc)

    def linea(id_, estado):
        return Reserva(id=id_, estado=estado, inicio=ahora, fin=ahora + timedelta(hours=2))

    # Entrega parcial: 1 y 2 se entregan, 3 no -> cancelada.
    grupo = [linea(1, "confirmada"), linea(2, "confirmada"), linea(3, "confirmada")]
    assert actas_service.aplicar_transicion(grupo, "entrega", {1, 2}) == (2, None)
    assert [r.estado for r in grupo] == ["entregada", "entregada", "cancelada"]
    assert actas_service.momento_siguiente(grupo) == "devolucion"

    # Devolver la 3 (nunca entregada) se rechaza sin tocar nada.
    cambiadas, error = actas_service.aplicar_transicion(grupo, "devolucion", {1, 3})
    assert cambiadas == 0 and error, error
    assert [r.estado for r in grupo] == ["entregada", "entregada", "cancelada"]

    # Devolución parcial: la 2 sigue afuera y queda pendiente otra devolución.
    assert actas_service.aplicar_transicion(grupo, "devolucion", {1}) == (1, None)
    assert [r.estado for r in grupo] == ["completada", "entregada", "cancelada"]
    assert actas_service.momento_siguiente(grupo) == "devolucion"

    # Ninguno marcado se rechaza; sin `ids` (acta subida) se mueven todas.
    assert actas_service.aplicar_transicion(grupo, "devolucion", set())[1]
    assert actas_service.aplicar_transicion(grupo, "devolucion") == (1, None)
    assert actas_service.momento_siguiente(grupo) is None

print("OK: entrega parcial cancela lo no entregado; no se devuelve lo no entregado; devolución parcial deja pendiente el resto.")
