"""
Verificación de los equipos marcados al firmar un acta
(actas_service.aplicar_transicion con `ids`): lo que no se entrega se cancela,
y no se puede devolver lo que no se entregó. Solo objetos en memoria, nunca
se agregan a la sesión ni se hace commit -- no escribe nada en la base.

    docker exec -i lacs_flask_app python - < entregables/OE3/pruebas/verificar_equipos_acta.py
"""

from datetime import datetime, timedelta, timezone

from app import actas_service, create_app
from app.models import Recurso, Reserva

app = create_app()
with app.app_context():
    ahora = datetime.now(timezone.utc)

    def linea(id_, estado):
        return Reserva(
            id=id_, estado=estado, inicio=ahora, fin=ahora + timedelta(hours=2),
            recurso=Recurso(id=100 + id_, estado="disponible"),
        )

    # Entrega parcial: 1 y 2 se entregan, 3 no -> cancelada.
    grupo = [linea(1, "confirmada"), linea(2, "confirmada"), linea(3, "confirmada")]
    assert actas_service.aplicar_transicion(grupo, "entrega", {1, 2}) == (2, None)
    assert [r.estado for r in grupo] == ["entregada", "entregada", "cancelada"]
    assert actas_service.momento_siguiente(grupo) == "devolucion"

    # Devolver la 3 (nunca entregada) se rechaza sin tocar nada.
    cambiadas, error = actas_service.aplicar_transicion(grupo, "devolucion", {1, 3})
    assert cambiadas == 0 and error, error
    assert [r.estado for r in grupo] == ["entregada", "entregada", "cancelada"]

    # Devolución: la 1 vuelve, la 2 no -> no_devuelta y su activo no_devuelto. Nada queda pendiente.
    assert actas_service.aplicar_transicion(grupo, "devolucion", {1}) == (2, None)
    assert [r.estado for r in grupo] == ["completada", "no_devuelta", "cancelada"]
    assert [r.recurso.estado for r in grupo] == ["disponible", "no_devuelto", "disponible"]
    assert actas_service.momento_siguiente(grupo) is None

    # Devolución sin nada marcado (perdió todo) es válida; sin `ids` (acta subida) todo vuelve.
    perdido = [linea(4, "entregada"), linea(5, "entregada")]
    assert actas_service.aplicar_transicion(perdido, "devolucion", set()) == (2, None)
    assert {r.estado for r in perdido} == {"no_devuelta"}
    subida = [linea(6, "entregada")]
    assert actas_service.aplicar_transicion(subida, "devolucion") == (1, None)
    assert subida[0].estado == "completada" and subida[0].recurso.estado == "disponible"
    # En la entrega, ninguno marcado se sigue rechazando.
    assert actas_service.aplicar_transicion([linea(7, "confirmada")], "entrega", set())[1]

print("OK: entrega parcial cancela lo no entregado; no se devuelve lo no entregado; lo no devuelto queda no_devuelta/no_devuelto.")
