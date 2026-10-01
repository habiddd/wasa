"""
Canal Meta + selector (docs/PLAN_baileys7_y_meta_2026-09-30.md, parte B).
Usa el test_client de Flask dentro de UN app_context (comparte db.session),
con un secreto y un token de prueba puestos en app.config, y `requests.post`
parcheado: no llama a Meta ni manda mensajes. Los servicios hacen
db.session.commit(): se parchea a flush y todo se revierte al final. Las
claves de Redis de prueba se borran y el proveedor vuelve al que estaba.

    docker exec -i lacs_flask_app python - < entregables/OE3/pruebas/verificar_meta_whatsapp.py
"""

import hashlib
import hmac
import json
import time
import uuid

from app import create_app, guardrails, usuarios_service, whatsapp_client as W
from app.extensions import db, redis_client
from app.models import AuditoriaBot, Usuario

NUM, NUM2, NUM3, NUM4, NUM5, NUM6 = (f"57300007700{i}" for i in range(1, 7))
LID = "999000000000077"
BS = "CO.PRUEBA" + uuid.uuid4().hex[:20]
OTROS_WAMID = []  # wamids de las pruebas nuevas, para limpiar Redis al final
SECRETO, VERIFY = "secreto-de-prueba", "verify-de-prueba"
WAMID = "wamid.PRUEBA" + uuid.uuid4().hex
WAMID_ENVIO = "wamid.ENVIO" + uuid.uuid4().hex

app = create_app()
with app.app_context():
    NUMS = [NUM, NUM2, NUM3, NUM4, NUM5, NUM6, LID, BS]
    assert not Usuario.query.filter(Usuario.telefono_wa.in_(NUMS)).count()
    assert not AuditoriaBot.query.filter(AuditoriaBot.telefono_wa.in_(NUMS)).count()
    db.session.commit = db.session.flush
    antes = redis_client.get("whatsapp:proveedor")
    vars_meta = ("META_WA_TOKEN", "META_WA_PHONE_NUMBER_ID", "META_APP_SECRET", "META_WEBHOOK_VERIFY_TOKEN",
                 "META_PLANTILLA_AVISO")
    guardadas = {v: app.config[v] for v in vars_meta}
    post_real = W.requests.post
    enviados = []
    fallar = {"on": False}  # simula que Meta rechaza el envío

    def post_falso(url, json=None, headers=None, timeout=None):
        enviados.append((url, json, headers))

        class R:
            ok = not fallar["on"]
            status_code = 400 if fallar["on"] else 200
            text = '{"error":{"code":131030}}' if fallar["on"] else "{}"
            def raise_for_status(self): pass
            def json(self): return {"messages": [{"id": WAMID_ENVIO}]}
        return R()

    def firmado(payload, secreto=SECRETO):
        cuerpo = json.dumps(payload).encode()
        firma = "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()
        return dict(data=cuerpo, headers={"X-Hub-Signature-256": firma, "Content-Type": "application/json"})

    def entrante(wamid, numero=NUM, texto="hola", ts=None, phone="123"):
        OTROS_WAMID.append(wamid)
        return {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp", "metadata": {"phone_number_id": phone},
            "contacts": [{"profile": {"name": "Prueba"}, "wa_id": numero, "user_id": "CO.abc"}],
            "messages": [{"from": numero, "from_user_id": "CO.abc", "id": wamid,
                          "timestamp": str(int(time.time()) if ts is None else ts),
                          "type": "text", "text": {"body": texto}}]}}]}]}

    c = app.test_client()
    try:
        # cambiar_proveedor sin variables: error con la lista, no cambia nada
        for v in vars_meta:
            app.config[v] = ""
        assert W.cambiar_proveedor("meta") == list(W.VARIABLES_META)
        assert W.proveedor_activo() == (antes.decode() if antes else "baileys")
        print("cambiar_proveedor sin variables: ok")

        app.config.update(META_WA_TOKEN="token-falso", META_WA_PHONE_NUMBER_ID="123",
                          META_APP_SECRET=SECRETO, META_WEBHOOK_VERIFY_TOKEN=VERIFY)
        W.requests.post = post_falso

        # Verificación GET
        r = c.get(f"/webhook/meta?hub.mode=subscribe&hub.verify_token={VERIFY}&hub.challenge=1234")
        assert r.status_code == 200 and r.get_data(as_text=True) == "1234", r.get_data()
        r = c.get("/webhook/meta?hub.mode=subscribe&hub.verify_token=malo&hub.challenge=1234")
        assert r.status_code == 403
        print("verificacion GET: ok")

        # Firma inválida / ausente: 403 y no se procesa nada
        r = c.post("/webhook/meta", **firmado(entrante(WAMID), "otro-secreto"))
        assert r.status_code == 403
        r = c.post("/webhook/meta", data=b"{}", headers={"Content-Type": "application/json"})
        assert r.status_code == 403
        assert not enviados and not Usuario.query.filter_by(telefono_wa=NUM).count()
        print("firma invalida: 403")

        # Con Baileys activo, Meta ignora el mensaje (firma válida, 200)
        assert W.cambiar_proveedor("baileys") == []
        r = c.post("/webhook/meta", **firmado(entrante(WAMID)))
        assert r.status_code == 200 and not enviados
        redis_client.delete(f"meta:wamid:{WAMID}", f"meta:ventana:{NUM}")  # el dedupe ya lo marcó

        # Con Meta activo: se procesa y responde por la rama Meta (texto, ventana abierta)
        assert W.cambiar_proveedor("meta") == [] and W.proveedor_activo() == "meta"
        r = c.post("/webhook/meta", **firmado(entrante(WAMID)))
        assert r.status_code == 200
        assert len(enviados) == 1, enviados
        url, cuerpo, headers = enviados[0]
        assert url == f"https://graph.facebook.com/{app.config['META_GRAPH_VERSION']}/123/messages", url
        assert headers["Authorization"] == "Bearer token-falso"
        assert cuerpo["to"] == NUM and cuerpo["type"] == "text" and cuerpo["text"]["body"], cuerpo
        fila = AuditoriaBot.query.filter_by(telefono_wa=NUM, mensaje_entrante="hola").one()
        assert fila.mensaje_id == WAMID_ENVIO and fila.estado_envio == "pendiente"
        print("entrante Meta -> auditoria + envio texto:", repr(cuerpo["text"]["body"][:50]))

        # Mismo wamid otra vez (reintento de Meta): una sola vez
        r = c.post("/webhook/meta", **firmado(entrante(WAMID)))
        assert r.status_code == 200 and len(enviados) == 1
        assert AuditoriaBot.query.filter_by(telefono_wa=NUM, mensaje_entrante="hola").count() == 1
        print("deduplicado por wamid: ok")

        # Status delivered -> auditoria_bot.estado_envio
        estado = {"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "123"}, "statuses": [
            {"id": WAMID_ENVIO, "status": "delivered", "recipient_id": NUM}]}}]}]}
        r = c.post("/webhook/meta", **firmado(estado))
        assert r.status_code == 200
        db.session.refresh(fila)
        assert fila.estado_envio == "delivery_ack", fila.estado_envio
        print("status delivered -> delivery_ack")

        # Fuera de 24 h: plantilla con el texto aplanado; sin plantilla, no envía
        enviados.clear()
        app.config["META_PLANTILLA_AVISO"] = ""
        assert W.enviar_mensaje_texto(f"{NUM2}@s.whatsapp.net", "a\nb") == (False, None) and not enviados
        app.config["META_PLANTILLA_AVISO"] = "lacs_aviso"
        ok, wamid = W.enviar_mensaje_texto(f"{NUM2}@s.whatsapp.net", "Línea 1\n\nLínea 2")
        assert (ok, wamid) == (True, WAMID_ENVIO)
        t = enviados[0][1]
        assert t["type"] == "template" and t["to"] == NUM2 and t["template"]["name"] == "lacs_aviso"
        assert t["template"]["language"]["code"] == "es"
        assert t["template"]["components"][0]["parameters"][0]["text"] == "Línea 1 · Línea 2", t
        # un @lid no tiene número: no se envía
        assert W.enviar_mensaje_texto("144186850431118@lid", "x") == (False, None) and len(enviados) == 1
        print("fuera de 24 h -> plantilla:", t["template"]["components"][0]["parameters"][0]["text"])

        # Con Meta activo, el webhook de Baileys no contesta
        r = c.post("/webhook/whatsapp", json={"event": "messages.upsert", "data": {
            "key": {"remoteJid": f"{NUM2}@s.whatsapp.net", "fromMe": False}, "message": {"conversation": "hola"}}})
        assert r.get_json() == {"status": "ignorado_proveedor"}, r.get_json()
        assert not Usuario.query.filter_by(telefono_wa=NUM2).count()
        print("webhook Baileys con Meta activo: ignorado_proveedor")

        # Paso 1: la auditoría (mensajes de cualquier canal) ya no abre la ventana
        enviados.clear()
        guardrails.registrar_interaccion(NUM2, "hola", None, None, permitido=True)
        W.enviar_mensaje_texto(f"{NUM2}@s.whatsapp.net", "x")
        assert enviados[0][1]["type"] == "template", enviados[0][1]
        # la ventana se mide desde el timestamp del mensaje
        c.post("/webhook/meta", **firmado(entrante("wamid.VIEJO" + uuid.uuid4().hex, NUM3, ts=int(time.time()) - 25 * 3600)))
        assert not redis_client.exists(f"meta:ventana:{NUM3}")
        c.post("/webhook/meta", **firmado(entrante("wamid.NUEVO" + uuid.uuid4().hex, NUM3)))
        assert 0 < redis_client.ttl(f"meta:ventana:{NUM3}") <= 86100
        print("paso 1: ventana solo con Redis y desde el timestamp: ok")

        # Paso 2: parámetro de plantilla válido
        enviados.clear()
        W.enviar_mensaje_texto(f"{NUM2}@s.whatsapp.net", "a\tb     c\n\n d")
        assert enviados[0][1]["template"]["components"][0]["parameters"][0]["text"] == "a b c · d"
        W.enviar_mensaje_texto(f"{NUM2}@s.whatsapp.net", "x" * 2000)
        param = enviados[1][1]["template"]["components"][0]["parameters"][0]["text"]
        assert len(param) == 900 and param.endswith("…")
        print("paso 2: parámetro de plantilla: ok")

        # Paso 3: un rechazo de Meta no lanza excepción
        enviados.clear()
        fallar["on"] = True
        try:
            assert W.enviar_mensaje_texto(f"{NUM2}@s.whatsapp.net", "x") == (False, None)
        finally:
            fallar["on"] = False
        print("paso 3: rechazo de Meta -> (False, None)")

        # Paso 4: otro phone_number_id se ignora; los failed quedan registrados
        enviados.clear()
        r = c.post("/webhook/meta", **firmado(entrante("wamid.OTRO" + uuid.uuid4().hex, NUM6, phone="999")))
        assert r.status_code == 200 and not enviados and not Usuario.query.filter_by(telefono_wa=NUM6).count()
        fallido = lambda wamid: {"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "123"}, "statuses": [
            {"id": wamid, "status": "failed", "errors": [{"code": 131026}]}]}}]}]}
        assert c.post("/webhook/meta", **firmado(fallido("wamid.DESCONOCIDO"))).status_code == 200
        # wamid propio: los envíos falsos comparten WAMID_ENVIO y el más reciente no sería `fila`
        wamid_fallo = "wamid.FALLO" + uuid.uuid4().hex
        guardrails.registrar_interaccion(NUM, "x", None, "y", True, mensaje_id=wamid_fallo, estado_envio="pendiente")
        assert c.post("/webhook/meta", **firmado(fallido(wamid_fallo))).status_code == 200
        assert AuditoriaBot.query.filter_by(mensaje_id=wamid_fallo).one().estado_envio == "fallido"
        print("paso 4: phone_number_id y failed: ok")

        # Paso 5: Meta no pisa un @lid de Baileys, pero sí rellena un jid vacío
        enviados.clear()
        db.session.add(Usuario(telefono_wa=NUM4, jid_wa="999000000000078@lid", rol="estudiante"))
        db.session.add(Usuario(telefono_wa=NUM5, jid_wa=None, rol="estudiante"))
        db.session.flush()
        c.post("/webhook/meta", **firmado(entrante("wamid.LID" + uuid.uuid4().hex, NUM4)))
        assert Usuario.query.filter_by(telefono_wa=NUM4).one().jid_wa == "999000000000078@lid"
        assert enviados[0][1]["to"] == NUM4, enviados[0][1]
        c.post("/webhook/meta", **firmado(entrante("wamid.NULO" + uuid.uuid4().hex, NUM5)))
        assert Usuario.query.filter_by(telefono_wa=NUM5).one().jid_wa == f"{NUM5}@s.whatsapp.net"
        print("paso 5: jid_wa de Baileys intacto: ok")

        # Paso 6: wamid largo cabe en la auditoría
        guardrails.registrar_interaccion(NUM, "x", None, "y", True, mensaje_id="wamid." + "A" * 100)
        db.session.flush()
        print("paso 6: mensaje_id largo: ok")

        # Paso 7: BSUID (sin `from`): se registra con el BSUID y se le escribe por `recipient`
        enviados.clear()
        e = entrante("wamid.BSUID" + uuid.uuid4().hex)
        msg = e["entry"][0]["changes"][0]["value"]["messages"][0]
        del msg["from"]
        msg["from_user_id"] = BS
        assert c.post("/webhook/meta", **firmado(e)).status_code == 200
        u = Usuario.query.filter_by(telefono_wa=BS).one()
        assert u.jid_wa == f"{BS}@bsuid" and usuarios_service.telefono_es_lid(u)
        cuerpo = enviados[0][1]
        assert cuerpo["recipient"] == BS and "to" not in cuerpo and cuerpo["type"] == "text", cuerpo
        redis_client.delete(f"meta:ventana:{BS}")
        assert W.jid_de(u) == f"{BS}@bsuid"
        enviados.clear()
        W.enviar_mensaje_texto(W.jid_de(u), "x")
        assert enviados[0][1]["type"] == "template" and enviados[0][1]["recipient"] == BS
        # persona con solo LID y Meta activo: no sale nada
        enviados.clear()
        sin_numero = Usuario(telefono_wa=LID, jid_wa=f"{LID}@lid")
        assert W.enviar_mensaje_texto(W.jid_de(sin_numero), "x") == (False, None) and not enviados
        print("paso 7: BSUID: ok")

        # Baileys no cambia
        assert W.cambiar_proveedor("baileys") == []
        enviados.clear()
        W.enviar_mensaje_texto(f"{LID}@lid", "x")
        url, cuerpo, _ = enviados[0]
        assert url.endswith("/send") and cuerpo == {"jid": f"{LID}@lid", "text": "x"}, (url, cuerpo)
        assert W.jid_de(sin_numero) == sin_numero.jid_wa
        assert W.cambiar_proveedor("meta") == []
        print("Baileys sin cambios: ok")

        # Periodos inválidos de las métricas de Meta: se rechazan antes de consultar la Graph API
        from base64 import b64encode
        from datetime import date, timedelta
        auth = {"Authorization": "Basic " + b64encode(
            f"{app.config.get('DASHBOARD_USER', '')}:{app.config.get('DASHBOARD_PASSWORD', '')}".encode()).decode()}
        hoy = date.today()
        for q, error in (
            ("periodo=99", "periodo debe ser"),
            (f"periodo=rango&desde={hoy}&hasta={hoy - timedelta(days=3)}", "anterior a desde"),
            (f"periodo=rango&desde={hoy - timedelta(days=400)}&hasta={hoy}", "últimos 365 días"),
            (f"periodo=rango&desde={hoy + timedelta(days=3)}&hasta={hoy + timedelta(days=5)}", "no ha empezado"),
            ("periodo=rango&desde=ayer&hasta=hoy", "YYYY-MM-DD"),
        ):
            r = c.get(f"/dashboard/api/whatsapp/meta/metricas?{q}", headers=auth)
            assert r.status_code == 400 and error in r.get_json()["error"], (q, r.status_code, r.get_json())
        print("periodos inválidos de métricas Meta: 400")
        print("OK")
    finally:
        W.requests.post = post_real
        app.config.update(guardadas)
        db.session.rollback()
        redis_client.delete(f"meta:wamid:{WAMID}", *(f"meta:ventana:{n}" for n in NUMS + ["CO.abc"]),
                            *(f"meta:wamid:{w}" for w in OTROS_WAMID))
        if antes is None:
            redis_client.delete("whatsapp:proveedor")
        else:
            redis_client.set("whatsapp:proveedor", antes)
