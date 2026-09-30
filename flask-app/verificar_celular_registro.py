"""
Celular y resumen de confirmación en el registro por WhatsApp
(docs/PLAN_celular_registro_2026-09-30.md, revisión 3). Llama a
`procesar_registro` y a `obtener_o_crear_usuario` directo (no el webhook), así
que no manda mensajes. Los servicios hacen db.session.commit(): se parchea a
flush y todo se revierte al final, la base queda como estaba.

    docker exec -i lacs_flask_app python - < flask-app/verificar_celular_registro.py
"""

from datetime import datetime, timedelta, timezone

from app import create_app
from app import registro_service as R
from app import usuarios_service as U
from app.extensions import db
from app.models import Reserva, ReservaGrupo, Usuario

LID = "999000000000001"
JID_LID = f"{LID}@lid"
TELS = [f"57300999000{i}" for i in range(1, 10)]  # 573009990001 .. 573009990009
JID_REAL = f"{TELS[1]}@s.whatsapp.net"

app = create_app()
with app.app_context():
    assert not Usuario.query.filter(Usuario.telefono_wa.in_([LID, *TELS])).count()
    assert not Usuario.query.filter(Usuario.jid_wa.in_([JID_LID, JID_REAL])).count()
    db.session.commit = db.session.flush

    def paso(usuario, texto, sesion):
        respuesta, sesion = R.procesar_registro(usuario, texto, sesion)
        print(f"  {texto!r}\n    -> {respuesta[-90:]!r}")
        return respuesta, sesion

    def tres_campos(usuario, sesion, sufijo):
        """nombre -> código -> cédula; devuelve la última respuesta."""
        for texto in ("", f"Persona Prueba{sufijo}", f"P{sufijo}123", f"555{sufijo}00"):
            r, sesion = paso(usuario, texto, sesion)
        return r, sesion

    def limpiar(*usuarios):
        for u in usuarios:
            db.session.delete(u)
        db.session.flush()

    # 1. JID real: resumen con el celular de WhatsApp, no lo pregunta -> "sí"
    u = U.obtener_o_crear_usuario(TELS[1], JID_REAL)
    r, s = tres_campos(u, {}, "1")
    assert "celular?" not in r and f"Celular: {TELS[1]}" in r and not u.registro_completo
    assert s["registro_campo_pendiente"] == "confirmar"
    r, s = paso(u, "sí", s)
    assert u.registro_completo and R.usuario_esta_completo(u) and "ya quedaste registrado" in r

    # 4. Número de WhatsApp: las opciones no traen "4"; responder "4" las repite
    u.registro_completo = False
    r, s = paso(u, "hola", {"registro_campo_pendiente": "confirmar"})
    assert "No entendí" in r  # 5. "tal vez"/otra cosa repite la pregunta y sigue incompleto
    assert not u.registro_completo
    r, s = paso(u, "no", s)
    assert "3. Cédula" in r and "4." not in r and s["registro_campo_pendiente"] == "corregir"
    r, s = paso(u, "4", s)
    assert "3. Cédula" in r and "4." not in r and s["registro_campo_pendiente"] == "corregir"

    # 3. Corregir el nombre: no -> 1 -> nombre nuevo -> resumen -> si
    r, s = paso(u, "1", s)
    assert "nombre completo" in r and s["registro_campo_pendiente"] == "corregir:nombre"
    r, s = paso(u, "Nombre Corregido", s)
    assert "Nombre: Nombre Corregido" in r and s["registro_campo_pendiente"] == "confirmar"
    r, s = paso(u, "si", s)
    assert u.registro_completo and u.nombre == "Nombre Corregido"

    # 6. Sesión perdida con todos los datos: sale el resumen, no la bienvenida
    u.registro_completo = False
    r, s = paso(u, "hola", {})
    assert "Revisa tus datos" in r and "registrarte" not in r
    assert s["registro_campo_pendiente"] == "confirmar"
    limpiar(u)

    # 2. LID sin número: 3 campos -> celular -> resumen -> no -> 4 -> otro -> "Sí."
    u = U.obtener_o_crear_usuario(LID, JID_LID)
    assert U.telefono_es_lid(u) and not R.usuario_esta_completo(u)
    r, s = tres_campos(u, {}, "2")
    assert "celular" in r and not u.registro_completo
    r, s = paso(u, "abc", s)
    assert "No entendí" in r and u.telefono_wa == LID
    r, s = paso(u, "300 999 0002", s)
    assert u.telefono_wa == TELS[1] and u.celular_escrito and u.jid_wa == JID_LID
    assert f"Celular: {TELS[1]}" in r and not u.registro_completo
    r, s = paso(u, "no", s)
    assert "4. Celular" in r
    r, s = paso(u, "4", s)
    assert "celular" in r and s["registro_campo_pendiente"] == "corregir:celular"
    r, s = paso(u, "300 999 0003", s)
    assert u.telefono_wa == TELS[2] and f"Celular: {TELS[2]}" in r
    r, s = paso(u, "Sí.", s)
    assert u.registro_completo and u.celular_escrito and R.usuario_esta_completo(u)
    assert U.obtener_o_crear_usuario(LID, JID_LID).id == u.id

    # 9. Ahora sí llega `senderPn`: el celular ya no es "escrito"
    u2 = U.obtener_o_crear_usuario(TELS[8], JID_LID)
    assert u2.id == u.id and u.telefono_wa == TELS[8] and not u.celular_escrito

    # 7. Ya completo por LID sin número: hola de nuevo -> celular -> resumen -> sí
    u.telefono_wa = LID
    u.celular_escrito = False
    db.session.flush()
    assert u.registro_completo and not R.usuario_esta_completo(u)
    r, s = paso(u, "hola", {})
    assert "Hola de nuevo" in r and "celular" in r and "nombre completo" not in r
    assert not u.registro_completo
    r, s = paso(u, "3009990004", s)
    assert "Revisa tus datos" in r and u.telefono_wa == TELS[3]
    r, s = paso(u, "sí", s)
    assert u.registro_completo and R.usuario_esta_completo(u)
    limpiar(u)

    # 8a. Fusión con fila del dashboard (sin jid_wa): una sola fila, confirmada al final
    ahora = datetime.now(timezone.utc) + timedelta(days=3650)
    existente = Usuario(telefono_wa=TELS[4], nombre="Nombre Dashboard", rol="estudiante")
    lid = Usuario(
        telefono_wa=LID, jid_wa=JID_LID, nombre="Nombre Bot", codigo_uninorte="L123456",
        cedula="7654321", registro_completo=True, rol="estudiante", aviso_privacidad_en=ahora,
    )
    db.session.add_all([existente, lid])
    db.session.flush()
    ids = {existente.id, lid.id}
    for i, dueno in enumerate((existente, lid)):
        g = ReservaGrupo(usuario_id=dueno.id)
        db.session.add(g)
        db.session.flush()
        db.session.add(Reserva(
            usuario_id=dueno.id, recurso_id=2 + i, grupo_id=g.id,
            inicio=ahora + timedelta(hours=i), fin=ahora + timedelta(hours=i + 1),
        ))
    db.session.flush()
    r, s = paso(lid, "hola", {})
    assert "Hola de nuevo" in r and "celular" in r
    r, s = paso(lid, TELS[4], s)
    assert "Revisa tus datos" in r and s["registro_campo_pendiente"] == "confirmar"
    filas = Usuario.query.filter(Usuario.id.in_(ids)).all()
    assert len(filas) == 1 and filas[0].id == existente.id
    f = filas[0]
    assert f.telefono_wa == TELS[4] and f.jid_wa == JID_LID and f.celular_escrito
    assert not f.registro_completo  # solo tras el "sí"
    assert f.nombre == "Nombre Dashboard" and f.cedula == "7654321" and f.codigo_uninorte == "L123456"
    assert f.aviso_privacidad_en == ahora
    assert Reserva.query.filter_by(usuario_id=f.id).count() == 2
    assert ReservaGrupo.query.filter_by(usuario_id=f.id).count() == 2
    r, s = paso(f, "sí", s)
    assert f.registro_completo and R.usuario_esta_completo(f)

    # 8b. LID que escribe el número de una fila CON jid_wa: rechazo, nada cambia
    ajeno = Usuario(
        telefono_wa=TELS[5], jid_wa=f"{TELS[5]}@s.whatsapp.net", nombre="Otra Persona",
        codigo_uninorte="X1234", cedula="999999", registro_completo=True, rol="estudiante",
    )
    lid = Usuario(
        telefono_wa=LID, jid_wa=JID_LID, nombre="Persona Lid", codigo_uninorte="L777",
        cedula="777777", rol="estudiante",
    )
    db.session.add_all([ajeno, lid])
    db.session.flush()
    r, s = paso(lid, "hola", {})
    r, s = paso(lid, TELS[5], s)
    assert "otra cuenta de WhatsApp" in r and s["registro_campo_pendiente"] == "celular"
    assert lid.telefono_wa == LID and not lid.registro_completo and not lid.celular_escrito
    assert ajeno.telefono_wa == TELS[5] and ajeno.jid_wa == f"{TELS[5]}@s.whatsapp.net"
    assert ajeno.nombre == "Otra Persona" and not ajeno.celular_escrito
    assert Usuario.query.filter(Usuario.id.in_([ajeno.id, lid.id])).count() == 2

    db.session.rollback()
    assert not Usuario.query.filter(Usuario.telefono_wa.in_([LID, *TELS])).count()

print("OK: el celular se pide solo si WhatsApp no lo dio y el registro termina con resumen y confirmación.")
