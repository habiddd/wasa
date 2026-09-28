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
from app.routes.dashboard import _reservas_del_grupo, _validar_nota

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

    # --- Grupo de un solo activo: SÍ tiene que caber en una página ----------
    # (con 3+ activos las firmas de devolución pasan a la página 2 y está bien).
    grupo_chico = (
        db.session.query(Reserva.grupo_id)
        .filter(Reserva.usuario_id == HABID)
        .group_by(Reserva.grupo_id)
        .having(func.count() == 1)
        .order_by(Reserva.grupo_id.desc())
        .limit(1)
        .scalar()
    )
    assert grupo_chico is not None, "no hay ningún grupo de un activo del usuario de prueba"
    _pdf_chico, anclas_chico = acta_pdf.generar_acta_con_anclas(_reservas_del_grupo(grupo_chico))
    assert all(a["pagina"] == 1 for a in anclas_chico.values()), (
        f"el acta del grupo chico {grupo_chico} se corrió a una segunda página"
    )

    # --- POST real: nota de 301 caracteres debe rechazar sin escribir nada --
    # Hace falta un grupo con algo por firmar (si no, el endpoint responde 409
    # antes de mirar la nota). Si no hay ninguno, se prueba la validación sola.
    pendiente = next(
        (gid for (gid,) in db.session.query(Reserva.grupo_id).filter(Reserva.usuario_id == HABID).distinct()
         if actas_service.momento_siguiente(_reservas_del_grupo(gid)) is not None),
        None,
    )
    if pendiente is None:
        _nota, error = _validar_nota("x" * 301)
        assert error and "300" in error, error
        via = "_validar_nota directo (no hay grupo pendiente por firmar)"
    else:
        via = f"POST al grupo {pendiente}"
        momento = actas_service.momento_siguiente(_reservas_del_grupo(pendiente))
        filas_antes = ReservaActa.query.filter_by(grupo_id=pendiente).count()
        cliente = app.test_client()
        auth = (app.config["DASHBOARD_USER"], app.config["DASHBOARD_PASSWORD"])
        resp = cliente.post(
            f"/dashboard/api/reservas/grupo/{pendiente}/actas",
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
        filas_despues = ReservaActa.query.filter_by(grupo_id=pendiente).count()
        assert filas_despues == filas_antes, "el POST rechazado no debía escribir ninguna fila"

print(
    f"OK: entrega_nota/devolucion_nota consistentes en página {pagina_firmas} del grupo grande "
    f"{grupo_grande} ({n_lineas} activos); grupo chico {grupo_chico} cabe en 1 página; "
    f"nota de 301 caracteres rechazada vía {via}."
)
