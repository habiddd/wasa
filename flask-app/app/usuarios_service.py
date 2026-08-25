"""
Alta de usuarios a partir del número de WhatsApp. El primer mensaje que
manda un número ya lo convierte en fila de Usuario (hace falta un
usuario_id real para poder crear una Reserva -- FK NOT NULL), pero queda
con `registro_completo=False` y sin nombre/código/cédula hasta que
registro_service.py complete esos datos por el flujo de preguntas.
"""

from app.extensions import db
from app.models import Usuario


def obtener_o_crear_usuario(telefono_wa: str) -> Usuario:
    usuario = Usuario.query.filter_by(telefono_wa=telefono_wa).first()
    if usuario:
        return usuario

    usuario = Usuario(telefono_wa=telefono_wa, rol="estudiante")
    db.session.add(usuario)
    db.session.commit()
    return usuario
