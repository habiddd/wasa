"""
Alta de usuarios a partir del número de WhatsApp. El primer mensaje que
manda un número ya lo convierte en fila de Usuario (hace falta un
usuario_id real para poder crear una Reserva -- FK NOT NULL), pero queda
con `registro_completo=False` y sin nombre/código/cédula hasta que
registro_service.py complete esos datos por el flujo de preguntas.
"""

from app.extensions import db
from app.models import Usuario


def obtener_o_crear_usuario(telefono_wa: str, jid_wa: str | None = None) -> Usuario:
    """`jid_wa` es el JID completo del mensaje entrante. Se guarda (o se
    actualiza si cambió) para poder escribirle después sin un mensaje suyo
    al que responder -- ver Usuario.jid_wa."""
    usuario = Usuario.query.filter_by(telefono_wa=telefono_wa).first()
    if usuario:
        if jid_wa and usuario.jid_wa != jid_wa:
            usuario.jid_wa = jid_wa
            db.session.commit()
        return usuario

    usuario = Usuario(telefono_wa=telefono_wa, jid_wa=jid_wa, rol="estudiante")
    db.session.add(usuario)
    db.session.commit()
    return usuario
