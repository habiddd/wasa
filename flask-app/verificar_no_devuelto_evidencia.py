"""
Prueba de sistema con evidencia: alguien no devuelve un equipo (ver
docs/PRUEBAS_NO_DEVUELTO_2026-09-28.md). A diferencia de verificar_no_devuelto.py
NO borra nada: reservas, actas (PDF en disco + filas), historial y el estado de
los activos quedan en la base para revisarlos en el dashboard.

Todo contra los endpoints reales del dashboard (test_client), con habid (id 24).
Las reservas son a dos días: el barrido de avisos nunca las ve 'confirmada', así
que no sale ningún WhatsApp.

  1. Grupo con 3 equipos: se entregan los 3, devuelve solo el 1. El 2 queda
     no_devuelto (se deja así). El 3 lo "recupera" la LACS desde Activos.
  2. Grupo con 1 equipo: no devuelve nada (acta de devolución sin marcas).

    docker exec -i lacs_flask_app python - < flask-app/verificar_no_devuelto_evidencia.py
"""

import io
from datetime import datetime, timedelta, timezone

from app import acta_pdf, create_app, recursos_service
from app.extensions import db
from app.models import Recurso, Reserva, ReservaGrupo
from app.routes.dashboard import _reservas_del_grupo

HABID = 24
API = "/dashboard/api"

app = create_app()
with app.app_context():
    cliente = app.test_client()
    auth = (app.config["DASHBOARD_USER"], app.config["DASHBOARD_PASSWORD"])
    inicio = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=2)
    fin = inicio + timedelta(hours=2)

    libres = [r for r in Recurso.query.filter_by(estado="disponible", tipo="equipo").order_by(Recurso.id)
              if recursos_service._reserva_solapada(r.id, inicio, fin) is None][:4]
    assert len(libres) == 4, "no hay cuatro equipos libres para la prueba"
    nombre = {r.id: f"{r.nombre} (recurso {r.id})" for r in libres}

    def crear_grupo(recurso_ids):
        g = ReservaGrupo(usuario_id=HABID)
        db.session.add(g)
        db.session.flush()
        for rid in recurso_ids:
            db.session.add(Reserva(usuario_id=HABID, recurso_id=rid, grupo_id=g.id,
                                   canal_origen="dashboard", inicio=inicio, fin=fin))
        db.session.commit()
        return g.id, {r.recurso_id: r.id for r in Reserva.query.filter_by(grupo_id=g.id)}

    def firmar(gid, momento, ids, nota=None):
        datos = {"archivo": (io.BytesIO(acta_pdf.generar_acta(_reservas_del_grupo(gid))), "acta.pdf"),
                 "momento": momento, "origen": "firmada", "firmado_por": "Habid Abdala (prueba)",
                 "equipos": ",".join(str(i) for i in ids)}
        if nota:
            datos["nota"] = nota
        resp = cliente.post(f"{API}/reservas/grupo/{gid}/actas", data=datos,
                            content_type="multipart/form-data", auth=auth)
        assert resp.status_code == 201, resp.get_json()
        return {r["id"]: r["estado"] for r in resp.get_json()["reservas"]}

    def estado_activo(rid):
        db.session.expire_all()
        return db.session.get(Recurso, rid).estado

    def bot_lo_ofrece(rid):
        db.session.expire_all()
        return bool(recursos_service._unidades_fijas_libres([rid], inicio, fin))

    def paso(texto):
        print(f"  - {texto}")

    # ---- Escenario 1 --------------------------------------------------------
    a, b, c = (r.id for r in libres[:3])
    gid, lineas = crear_grupo([a, b, c])
    print(f"ESCENARIO 1: grupo {gid}, lineas {lineas}")
    paso(f"reserva creada: {[nombre[x] for x in (a, b, c)]}, del {inicio:%Y-%m-%d %H:%M} al {fin:%H:%M} UTC")

    estados = firmar(gid, "entrega", lineas.values())
    assert set(estados.values()) == {"entregada"}, estados
    paso(f"acta de ENTREGA firmada con los 3 marcados -> {estados}")

    estados = firmar(gid, "devolucion", [lineas[a]], nota="Prueba: no trajo dos de los tres equipos.")
    assert estados == {lineas[a]: "completada", lineas[b]: "no_devuelta", lineas[c]: "no_devuelta"}, estados
    paso(f"acta de DEVOLUCION firmada solo con {nombre[a]} marcado -> {estados}")

    sig = cliente.get(f"{API}/reservas/grupo/{gid}/acta/estado", auth=auth).get_json()["momento_siguiente"]
    assert sig is None, sig
    paso("el acta ya no pide otra devolucion (momento_siguiente = None)")

    assert (estado_activo(a), estado_activo(b), estado_activo(c)) == ("disponible", "no_devuelto", "no_devuelto")
    paso(f"activos: {nombre[a]}=disponible, {nombre[b]}=no_devuelto, {nombre[c]}=no_devuelto")

    assert bot_lo_ofrece(a) and not bot_lo_ofrece(b) and not bot_lo_ofrece(c)
    paso("el bot ofrece el devuelto y NO ofrece los no devueltos")
    for rid in (b, c):
        paso(f"bot, consulta '{libres[[r.id for r in libres].index(rid)].nombre}': "
             f"{recursos_service.evaluar_consulta_disponibilidad(db.session.get(Recurso, rid).nombre)!r}")

    resp = cliente.post(f"{API}/reservas", json={"usuario_id": HABID, "recurso_ids": [b],
                                                 "inicio": inicio.isoformat(), "fin": fin.isoformat()}, auth=auth)
    assert resp.status_code == 400, (resp.status_code, resp.get_json())
    paso(f"dashboard, crear reserva con {nombre[b]} -> {resp.status_code} {resp.get_json()['error']!r}")

    resp = cliente.patch(f"{API}/activos/{a}", json={"estado": "no_devuelto"}, auth=auth)
    assert resp.status_code == 409, (resp.status_code, resp.get_json())
    paso(f"Activos, poner {nombre[a]} a mano en no_devuelto -> {resp.status_code} {resp.get_json()['error']!r}")

    resp = cliente.patch(f"{API}/activos/{c}", json={"estado": "disponible"}, auth=auth)
    assert resp.status_code == 200, resp.get_json()
    assert estado_activo(c) == "disponible" and bot_lo_ofrece(c)
    paso(f"Activos, la LACS recupera {nombre[c]} y lo pasa a disponible -> 200; el bot vuelve a ofrecerlo")
    paso(f"{nombre[b]} se DEJA en no_devuelto como evidencia")

    # ---- Escenario 2 --------------------------------------------------------
    d = libres[3].id
    gid2, lineas2 = crear_grupo([d])
    print(f"ESCENARIO 2: grupo {gid2}, lineas {lineas2}")
    firmar(gid2, "entrega", lineas2.values())
    paso(f"acta de ENTREGA firmada con {nombre[d]}")
    estados = firmar(gid2, "devolucion", [], nota="Prueba: no devolvió nada.")
    assert estados == {lineas2[d]: "no_devuelta"}, estados
    assert estado_activo(d) == "no_devuelto" and not bot_lo_ofrece(d)
    paso(f"acta de DEVOLUCION sin nada marcado -> {estados}; {nombre[d]}=no_devuelto, el bot no lo ofrece")
    paso(f"{nombre[d]} se DEJA en no_devuelto como evidencia")

print(f"OK. Evidencia: grupos {gid} y {gid2}; activos no_devuelto: {b}, {d}; recuperado: {c}.")
