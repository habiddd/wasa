"""
Canal Meta + selector (docs/PLAN_baileys7_y_meta_2026-09-30.md, parte B).
Usa el test_client de Flask dentro de UN app_context (comparte db.session),
con un secreto y un token de prueba puestos en app.config, y `requests.post`
parcheado: no llama a Meta ni manda mensajes. Los servicios hacen
db.session.commit(): se parchea a flush y todo se revierte al final. Las
claves de Redis de prueba se borran y el proveedor vuelve al que estaba.

    docker exec -i lacs_flask_app python - < flask-app/verificar_meta_whatsapp.py
"""

import hashlib
import hmac
import json
import uuid

from app import create_app, whatsapp_client as W
from app.extensions import db, redis_client
from app.models import AuditoriaBot, Usuario

NUM, NUM2 = "573000077001", "573000077002"
SECRETO, VERIFY = "secreto-de-prueba", "verify-de-prueba"
WAMID = "wamid.PRUEBA" + uuid.uuid4().hex
WAMID_ENVIO = "wamid.ENVIO" + uuid.uuid4().hex

app = create_app()
with app.app_context():
    assert not Usuario.query.filter(Usuario.telefono_wa.in_([NUM, NUM2])).count()
    assert not AuditoriaBot.query.filter(AuditoriaBot.telefono_wa.in_([NUM, NUM2])).count()
    db.session.commit = db.session.flush
    antes = redis_client.get("whatsapp:proveedor")
    vars_meta = ("META_WA_TOKEN", "META_WA_PHONE_NUMBER_ID", "META_APP_SECRET", "META_WEBHOOK_VERIFY_TOKEN",
                 "META_PLANTILLA_AVISO")
    guardadas = {v: app.config[v] for v in vars_meta}
    post_real = W.requests.post
    enviados = []

    def post_falso(url, json=None, headers=None, timeout=None):
        enviados.append((url, json, headers))

        class R:
            def raise_for_status(self): pass
            def json(self): return {"messages": [{"id": WAMID_ENVIO}]}
        return R()

    def firmado(payload, secreto=SECRETO):
        cuerpo = json.dumps(payload).encode()
        firma = "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()
        return dict(data=cuerpo, headers={"X-Hub-Signature-256": firma, "Content-Type": "application/json"})

    def entrante(wamid, numero=NUM, texto="hola"):
        return {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp", "metadata": {"phone_number_id": "123"},
            "contacts": [{"profile": {"name": "Prueba"}, "wa_id": numero, "user_id": "CO.abc"}],
            "messages": [{"from": numero, "from_user_id": "CO.abc", "id": wamid, "timestamp": "1",
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
        estado = {"entry": [{"changes": [{"value": {"statuses": [
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
        redis_client.delete(f"meta:wamid:{WAMID}", f"meta:ventana:{NUM}", f"meta:ventana:{NUM2}")
        if antes is None:
            redis_client.delete("whatsapp:proveedor")
        else:
            redis_client.set("whatsapp:proveedor", antes)
