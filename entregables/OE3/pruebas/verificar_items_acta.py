"""
Verificación de las anclas de marca por activo en el acta (columnas
"Entr."/"Dev." de la tabla de activos, claves entrega_linea_<id>/
devolucion_linea_<id> en acta_pdf.generar_acta_con_anclas, id = Reserva.id).
Usa al usuario habid (telefono 573045246117), la única cuenta de prueba permitida. Es de solo
lectura: genera el PDF base en memoria y llama a /acta/estado con GET, no
escribe ninguna fila ni archivo.

    docker exec -i lacs_flask_app python - < entregables/OE3/pruebas/verificar_items_acta.py

El grupo con MÁS líneas para comprobar que las anclas de cada fila existen y
son consistentes entre sí (misma fila -> mismo "y", Dev. a la derecha de
Entr.), y el grupo más grande de hasta 4 activos para confirmar que el acta entera
cabe en una página (si no cabe, pasa a 2 con las firmas juntas).
"""

from sqlalchemy import func

from app import acta_pdf, create_app
from app.extensions import db
from app.models import Reserva, Usuario
from app.routes.dashboard import _reservas_del_grupo



def comprobar_anclas(reservas, anclas):
    for r in reservas:
        entrega = anclas.get(f"entrega_linea_{r.id}")
        devolucion = anclas.get(f"devolucion_linea_{r.id}")
        assert entrega and devolucion, f"línea {r.id}: faltan sus anclas"
        assert entrega["y"] == devolucion["y"], f"línea {r.id}: Entr. y Dev. no comparten y"
        assert entrega["pagina"] == devolucion["pagina"], f"línea {r.id}: páginas distintas"
        assert devolucion["x"] > entrega["x"], f"línea {r.id}: Dev. no quedó a la derecha de Entr."
    # Filas distintas -> "y" distintas: si dos líneas compartieran ancla, la
    # marca de una caería en la fila de la otra.
    posiciones = {(anclas[f"entrega_linea_{r.id}"]["pagina"], anclas[f"entrega_linea_{r.id}"]["y"]) for r in reservas}
    assert len(posiciones) == len(reservas), "dos líneas comparten la misma fila de anclas"


app = create_app()
with app.app_context():
    HABID = Usuario.query.filter_by(telefono_wa="573045246117").one().id
    por_grupo = (
        db.session.query(Reserva.grupo_id, func.count())
        .filter(Reserva.usuario_id == HABID)
        .group_by(Reserva.grupo_id)
        .order_by(func.count().desc())
        .all()
    )
    grupo_grande, n_lineas = por_grupo[0]
    reservas_grande = _reservas_del_grupo(grupo_grande)
    _pdf, anclas_grande = acta_pdf.generar_acta_con_anclas(reservas_grande)
    comprobar_anclas(reservas_grande, anclas_grande)

    grupo_chico = max((gid for gid, n in por_grupo if n <= 4), key=lambda g: dict(por_grupo)[g])
    reservas_chico = _reservas_del_grupo(grupo_chico)
    _pdf, anclas_chico = acta_pdf.generar_acta_con_anclas(reservas_chico)
    comprobar_anclas(reservas_chico, anclas_chico)
    # Hasta 4 activos el acta entra ENTERA en una hoja (diseño compacto, ver
    # acta_pdf.generar_acta_con_anclas).
    assert all(a["pagina"] == 1 for a in anclas_chico.values()), (
        f"el acta del grupo chico {grupo_chico} se corrió a una segunda página"
    )
    # Si no cabe, pasa a 2 páginas, pero el bloque de firmas y notas va junto.
    paginas_firmas = {a["pagina"] for k, a in anclas_grande.items() if "_linea_" not in k}
    assert len(paginas_firmas) == 1, f"firmas del grupo grande repartidas en {paginas_firmas}"

    # /acta/estado trae TODAS las líneas del grupo (también las canceladas),
    # en el mismo orden que la tabla del PDF.
    cliente = app.test_client()
    auth = (app.config["DASHBOARD_USER"], app.config["DASHBOARD_PASSWORD"])
    estado = cliente.get(f"/dashboard/api/reservas/grupo/{grupo_grande}/acta/estado", auth=auth).get_json()
    assert [l["id"] for l in estado["lineas"]] == [r.id for r in reservas_grande], estado["lineas"]

print(
    f"OK: anclas por línea consistentes en el grupo grande {grupo_grande} ({n_lineas} activos) y en el "
    f"grupo chico {grupo_chico} (acta entera en 1 página); /acta/estado devuelve todas las líneas en orden."
)
