"""
Política de retención de auditoria_bot (entregables/OE4/POLITICA_RETENCION_AUDITORIA.md):
guardrails.anonimizar_auditoria_vieja borra texto y teléfono de las filas más
viejas que AUDITORIA_RETENCION_DIAS y conserva lo que usan las métricas.

  1. Una fila de hace 400 días se anonimiza; una de hoy queda intacta.
  2. Una segunda corrida no vuelve a tocar nada (0 filas).
  3. AUDITORIA_RETENCION_DIAS=0 desactiva la purga.

Crea sus dos filas con el número de prueba 000000000000 y las borra al final.

    docker exec -i lacs_flask_app python - < entregables/OE3/pruebas/verificar_retencion.py
"""

from datetime import datetime, timedelta, timezone

from app import create_app, guardrails
from app.extensions import db
from app.models import AuditoriaBot

app = create_app()
with app.app_context():
    app.config["AUDITORIA_RETENCION_DIAS"] = 180
    ahora = datetime.now(timezone.utc)
    vieja = AuditoriaBot(telefono_wa="000000000000", intent_detectado="reservar", mensaje_entrante="texto viejo",
                         respuesta_enviada="resp vieja", permitido=True, latencia_ms=1234, creado_en=ahora - timedelta(days=400))
    nueva = AuditoriaBot(telefono_wa="000000000000", intent_detectado="saludo", mensaje_entrante="texto nuevo",
                         respuesta_enviada="resp nueva", permitido=True, creado_en=ahora)
    db.session.add_all([vieja, nueva])
    db.session.commit()
    try:
        assert guardrails.anonimizar_auditoria_vieja() >= 1
        db.session.refresh(vieja)
        db.session.refresh(nueva)
        assert (vieja.telefono_wa, vieja.mensaje_entrante, vieja.respuesta_enviada) == ("anonimizado", None, None), vieja.__dict__
        assert (vieja.intent_detectado, vieja.latencia_ms, vieja.permitido) == ("reservar", 1234, True)
        assert (nueva.telefono_wa, nueva.mensaje_entrante) == ("000000000000", "texto nuevo")

        assert guardrails.anonimizar_auditoria_vieja() == 0

        app.config["AUDITORIA_RETENCION_DIAS"] = 0
        nueva.creado_en = ahora - timedelta(days=400)
        db.session.commit()
        assert guardrails.anonimizar_auditoria_vieja() == 0
        db.session.refresh(nueva)
        assert nueva.mensaje_entrante == "texto nuevo"
    finally:
        db.session.rollback()
        db.session.delete(vieja)
        db.session.delete(nueva)
        db.session.commit()

print("OK: fila de 400 días anonimizada (intent y latencia conservados), la de hoy intacta, "
      "segunda corrida sin cambios, retención 0 desactiva la purga. Filas de prueba borradas.")
