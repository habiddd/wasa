"""
Verificación de la nota del administrador en el acta (reserva_actas.nota,
anclas entrega_nota/devolucion_nota, ver docs/SESION_2026-09-28.md). Usa al
usuario habid (id 24), la única cuenta de prueba permitida. Es de solo
lectura: genera el PDF base en memoria y prueba el rechazo de una nota
demasiado larga contra el endpoint real -- no escribe ninguna fila ni
archivo (el POST se rechaza con 400 antes de llegar a actas_service.guardar).

    docker exec -i lacs_flask_app python - < flask-app/verificar_nota_acta.py

Dos grupos de prueba, no uno: el grupo con MÁS líneas (7 activos, id 9 al
escribir esto) YA se corría a una segunda página ANTES de este cambio --
verificado regenerando el acta con la versión de acta_pdf.py en HEAD, mismo
resultado. No es una regresión del bloque de notas, así que no tiene sentido
exigirle una sola página; ahí solo se comprueba que las anclas existan y
caigan en la MISMA página que el resto de las firmas. La exigencia real de
"una sola página" (la que motivó bajar _ALTO_NOTA de 3 a 2 líneas) se prueba
aparte con un grupo de un solo activo.
"""

import io

from sqlalchemy import func

from app import acta_pdf, actas_service, create_app
from app.extensions import db
from app.models import Reserva, ReservaActa
from app.routes.dashboard import _reservas_del_grupo

HABID = 24

app = create_app()
with app.app_context():
    # --- Grupo con más líneas: existencia y consistencia de las anclas ------
    grupo_grande, n_lineas = (
        db.session.query(Reserva.grupo_id, func.count())
        .filter(Reserva.usuario_id == HABID)
        .group_by(Reserva.grupo_id)
        .order_by(func.count().desc())
        .first()
    )
    reservas_grande = _reservas_del_grupo(grupo_grande)
    pdf_grande, anclas_grande = acta_pdf.generar_acta_con_anclas(reservas_grande)

    assert "entrega_nota" in anclas_grande, "falta el ancla entrega_nota"
    assert "devolucion_nota" in anclas_grande, "falta el ancla devolucion_nota"
    pagina_firmas = anclas_grande["devolucion_admin"]["pagina"]
    assert anclas_grande["entrega_nota"]["pagina"] == pagina_firmas, "entrega_nota quedó en otra página que las firmas"
    assert anclas_grande["devolucion_nota"]["pagina"] == pagina_firmas, "devolucion_nota quedó en otra página que las firmas"

    # --- Grupo de un solo activo (≤3): SÍ tiene que caber en una página -----
    candidatos_chicos = (
        db.session.query(Reserva.grupo_id, func.count())
        .filter(Reserva.usuario_id == HABID)
        .group_by(Reserva.grupo_id)
        .having(func.count() <= 3)
        .all()
    )
    grupo_chico = None
    for gid, _n in candidatos_chicos:
        reservas_chico = _reservas_del_grupo(gid)
        if actas_service.momento_siguiente(reservas_chico) is not None:
            grupo_chico = gid
            break
    assert grupo_chico is not None, "no hay ningún grupo chico (<=3 activos) del usuario de prueba con algo pendiente por firmar"

    _pdf_chico, anclas_chico = acta_pdf.generar_acta_con_anclas(reservas_chico)
    assert all(a["pagina"] == 1 for a in anclas_chico.values()), (
        f"el acta del grupo chico {grupo_chico} se corrió a una segunda página"
    )

    # --- POST real: nota de 301 caracteres debe rechazar sin escribir nada --
    momento = actas_service.momento_siguiente(reservas_grande)
    assert momento is not None, f"el grupo {grupo_grande} ya no tiene nada pendiente por firmar"

    filas_antes = ReservaActa.query.filter_by(grupo_id=grupo_grande).count()

    cliente = app.test_client()
    auth = (app.config["DASHBOARD_USER"], app.config["DASHBOARD_PASSWORD"])
    resp = cliente.post(
        f"/dashboard/api/reservas/grupo/{grupo_grande}/actas",
        data={
            "archivo": (io.BytesIO(pdf_grande), "acta.pdf"),
            "momento": momento,
            "origen": "firmada",
            "nota": "x" * 301,
        },
        content_type="multipart/form-data",
        auth=auth,
    )
    assert resp.status_code == 400, f"esperaba 400, llegó {resp.status_code}: {resp.get_data(as_text=True)}"
    assert "300" in resp.get_json()["error"], resp.get_json()

    filas_despues = ReservaActa.query.filter_by(grupo_id=grupo_grande).count()
    assert filas_despues == filas_antes, "el POST rechazado no debía escribir ninguna fila"

print(
    f"OK: entrega_nota/devolucion_nota consistentes en página {pagina_firmas} del grupo grande "
    f"{grupo_grande} ({n_lineas} activos, {len(set(a['pagina'] for a in anclas_grande.values()))} página(s), "
    f"preexistente antes de este cambio); grupo chico {grupo_chico} cabe en 1 página; "
    f"POST con nota de 301 caracteres rechazado con 400 y sin escribir fila."
)
