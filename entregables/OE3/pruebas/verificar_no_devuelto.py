"""
Prueba de sistema del estado no_devuelto (docs/SESION_2026-09-28.md, sección 10).
Crea una reserva de habid (telefono 573045246117) con dos equipos libres, firma la entrega con
los dos marcados y la devolución con uno solo, contra los endpoints reales.
Al final borra SOLO esa reserva (y sus PDFs) y deja el activo 'disponible'. El
historial de esas líneas y del activo queda como evidencia (la app no puede
borrarlo).

    docker exec -i lacs_flask_app python - < entregables/OE3/pruebas/verificar_no_devuelto.py
"""

import io
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app import acta_pdf, create_app, recursos_service
from app.extensions import db
from app.models import Recurso, Reserva, ReservaGrupo, Usuario
from app.routes.dashboard import _reservas_del_grupo

API = "/dashboard/api"

app = create_app()
with app.app_context():
    HABID = Usuario.query.filter_by(telefono_wa="573045246117").one().id
    cliente = app.test_client()
    auth = (app.config["DASHBOARD_USER"], app.config["DASHBOARD_PASSWORD"])
    # Dos días adelante: el barrido de avisos/no-show nunca la ve 'confirmada'.
    inicio = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=2)
    fin = inicio + timedelta(hours=2)

    libres = [r for r in Recurso.query.filter_by(estado="disponible", tipo="equipo").order_by(Recurso.id)
              if recursos_service._reserva_solapada(r.id, inicio, fin) is None][:2]
    assert len(libres) == 2, "no hay dos equipos libres para la prueba"
    devuelto_id, perdido_id = libres[0].id, libres[1].id

    grupo = ReservaGrupo(usuario_id=HABID)
    db.session.add(grupo)
    db.session.flush()
    gid = grupo.id
    for rid in (devuelto_id, perdido_id):
        db.session.add(Reserva(usuario_id=HABID, recurso_id=rid, grupo_id=gid,
                               canal_origen="dashboard", inicio=inicio, fin=fin))
    db.session.commit()
    lineas = {r.recurso_id: r.id for r in Reserva.query.filter_by(grupo_id=gid)}
    hist_desde = db.session.execute(text("SELECT coalesce(max(id), 0) FROM recurso_estado_historial")).scalar()

    def firmar(momento, ids):
        pdf = acta_pdf.generar_acta(_reservas_del_grupo(gid))
        return cliente.post(f"{API}/reservas/grupo/{gid}/actas", data={
            "archivo": (io.BytesIO(pdf), "acta.pdf"), "momento": momento, "origen": "firmada",
            "firmado_por": "Prueba no_devuelto", "equipos": ",".join(str(i) for i in ids),
        }, content_type="multipart/form-data", auth=auth)

    try:
        resp = firmar("entrega", lineas.values())
        assert resp.status_code == 201, resp.get_json()
        resp = firmar("devolucion", [lineas[devuelto_id]])
        assert resp.status_code == 201, resp.get_json()
        estados = {r["id"]: r["estado"] for r in resp.get_json()["reservas"]}
        assert estados == {lineas[devuelto_id]: "completada", lineas[perdido_id]: "no_devuelta"}, estados

        estado_acta = cliente.get(f"{API}/reservas/grupo/{gid}/acta/estado", auth=auth).get_json()
        assert estado_acta["momento_siguiente"] is None, estado_acta["momento_siguiente"]

        activos = {a["id"]: a["estado"] for a in cliente.get(f"{API}/activos", auth=auth).get_json()}
        assert activos[perdido_id] == "no_devuelto" and activos[devuelto_id] == "disponible", activos

        # El bot ya no lo ofrece (mismo filtro de inventario del wizard).
        db.session.expire_all()
        assert recursos_service._unidades_fijas_libres([perdido_id], inicio, fin) == []
        assert [u.id for u in recursos_service._unidades_fijas_libres([devuelto_id], inicio, fin)] == [devuelto_id]

        # A mano no se llega a no_devuelto...
        resp = cliente.patch(f"{API}/activos/{devuelto_id}", json={"estado": "no_devuelto"}, auth=auth)
        assert resp.status_code == 409, (resp.status_code, resp.get_json())
        # ...el formulario de edición puede reenviar el estado que ya tiene...
        resp = cliente.patch(f"{API}/activos/{perdido_id}", json={"estado": "no_devuelto"}, auth=auth)
        assert resp.status_code == 200, resp.get_json()
        # ...sacarlo de no_devuelto (a cualquier estado) exige motivo...
        for cuerpo in ({"estado": "disponible"}, {"estado": "en_mantenimiento"},
                       {"estado": "disponible", "motivo_estado": "perdido"},
                       {"estado": "disponible", "motivo_estado": "otro", "nota_estado": "  "}):
            resp = cliente.patch(f"{API}/activos/{perdido_id}", json=cuerpo, auth=auth)
            assert resp.status_code == 400, (cuerpo, resp.status_code, resp.get_json())
        db.session.expire_all()
        assert Recurso.query.get(perdido_id).estado == "no_devuelto"
        # ...y con él vuelve a disponible desde Activos.
        resp = cliente.patch(f"{API}/activos/{perdido_id}", auth=auth, json={
            "estado": "disponible", "motivo_estado": "devuelto_tarde", "nota_estado": "Lo trajo al día siguiente (prueba)."})
        assert resp.status_code == 200, resp.get_json()

        hist = [tuple(f) for f in db.session.execute(text(
            "SELECT recurso_id, estado_anterior, estado_nuevo, reserva_id, motivo, nota FROM recurso_estado_historial "
            "WHERE id > :desde AND recurso_id IN (:a, :b) ORDER BY id"),
            {"desde": hist_desde, "a": devuelto_id, "b": perdido_id})]
        linea_perdida = lineas[perdido_id]
        assert hist == [
            (perdido_id, "disponible", "no_devuelto", linea_perdida, None, None),
            (perdido_id, "no_devuelto", "disponible", linea_perdida, "devuelto_tarde", "Lo trajo al día siguiente (prueba)."),
        ], hist
        # El motivo es local a esa transacción: el siguiente cambio no lo hereda.
        cliente.patch(f"{API}/activos/{perdido_id}", json={"estado": "en_mantenimiento"}, auth=auth)
        cliente.patch(f"{API}/activos/{perdido_id}", json={"estado": "disponible"}, auth=auth)
        ultimos = [tuple(f) for f in db.session.execute(text(
            "SELECT reserva_id, motivo, nota FROM recurso_estado_historial WHERE recurso_id = :id ORDER BY id DESC LIMIT 2"),
            {"id": perdido_id})]
        assert ultimos == [(None, None, None), (None, None, None)], ultimos

        linea = [tuple(f) for f in db.session.execute(text(
            "SELECT estado_anterior, estado_nuevo FROM reserva_estado_historial WHERE reserva_id = :id ORDER BY id"),
            {"id": lineas[perdido_id]})]
        assert linea[-1] == ("entregada", "no_devuelta"), linea

        try:
            db.session.execute(text("DELETE FROM recurso_estado_historial WHERE recurso_id = :id"), {"id": perdido_id})
            raise AssertionError("la app pudo borrar recurso_estado_historial")
        except ProgrammingError:
            db.session.rollback()
    finally:
        db.session.rollback()
        perdido = Recurso.query.get(perdido_id)
        if perdido.estado != "disponible":
            perdido.estado = "disponible"
            db.session.commit()
        cliente.delete(f"{API}/reservas/grupo/{gid}", auth=auth)
        shutil.rmtree(Path(app.config["ACTAS_DIR"]) / str(gid), ignore_errors=True)

print(f"OK: grupo {gid} -> completada + no_devuelta; activo {perdido_id} no_devuelto, fuera del bot, "
      f"rechazado a mano (409), sin motivo no sale (400), con motivo vuelve a disponible y queda con reserva_id/motivo/nota en recurso_estado_historial. Prueba borrada.")
