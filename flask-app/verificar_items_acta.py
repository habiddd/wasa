"""
Verificación de las anclas de marca por activo en el acta (columnas
"Entr."/"Dev." de la tabla de activos, claves entrega_linea_<id>/
devolucion_linea_<id> en acta_pdf.generar_acta_con_anclas, id = Reserva.id).
Usa al usuario habid (id 24), la única cuenta de prueba permitida. Es de solo
lectura: genera el PDF base en memoria y llama a /acta/estado con GET, no
escribe ninguna fila ni archivo.

    docker exec -i lacs_flask_app python - < flask-app/verificar_items_acta.py

El grupo con MÁS líneas para comprobar que las anclas de cada fila existen y
son consistentes entre sí (misma fila -> mismo "y", Dev. a la derecha de
Entr.), y un grupo chico (<=3 activos) para confirmar que la tabla de activos
cabe en la página 1 (las firmas de devolución pueden pasar a la 2).
"""

from sqlalchemy import func

from app import acta_pdf, create_app
from app.extensions import db
from app.models import Reserva
from app.routes.dashboard import _reservas_del_grupo

HABID = 24


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

    grupo_chico = next(gid for gid, n in por_grupo if n <= 3)
    reservas_chico = _reservas_del_grupo(grupo_chico)
    _pdf, anclas_chico = acta_pdf.generar_acta_con_anclas(reservas_chico)
    comprobar_anclas(reservas_chico, anclas_chico)
    # La tabla de activos cabe en la página 1; las firmas de devolución pueden
    # pasar a la 2 (desde 3 activos) y está bien: sus anclas llevan la página.
    assert all(a["pagina"] == 1 for k, a in anclas_chico.items() if "_linea_" in k), (
        f"la tabla del acta del grupo chico {grupo_chico} se corrió a una segunda página"
    )

    # /acta/estado trae TODAS las líneas del grupo (también las canceladas),
    # en el mismo orden que la tabla del PDF.
    cliente = app.test_client()
    auth = (app.config["DASHBOARD_USER"], app.config["DASHBOARD_PASSWORD"])
    estado = cliente.get(f"/dashboard/api/reservas/grupo/{grupo_grande}/acta/estado", auth=auth).get_json()
    assert [l["id"] for l in estado["lineas"]] == [r.id for r in reservas_grande], estado["lineas"]

print(
    f"OK: anclas por línea consistentes en el grupo grande {grupo_grande} ({n_lineas} activos) y en el "
    f"grupo chico {grupo_chico} (tabla en la página 1); /acta/estado devuelve todas las líneas en orden."
)
