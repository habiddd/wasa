"""
Alta de usuarios a partir del número de WhatsApp. El primer mensaje que
manda un número ya lo convierte en fila de Usuario (hace falta un
usuario_id real para poder crear una Reserva -- FK NOT NULL), pero queda
con `registro_completo=False` y sin nombre/código/cédula hasta que
registro_service.py complete esos datos por el flujo de preguntas.
"""

import re

from app import registro_service
from app.extensions import db
from app.models import Reserva, ReservaGrupo, Usuario


def obtener_o_crear_usuario(telefono_wa: str, jid_wa: str | None = None) -> Usuario:
    """`jid_wa` es el JID completo del mensaje entrante. Se guarda (o se
    actualiza si cambió) para poder escribirle después sin un mensaje suyo
    al que responder -- ver Usuario.jid_wa."""
    usuario = Usuario.query.filter_by(telefono_wa=telefono_wa).first()
    if not usuario and jid_wa and jid_wa.endswith("@lid"):
        usuario = Usuario.query.filter_by(jid_wa=jid_wa).first()
        # Si `telefono_wa` es el propio LID, este mensaje llegó sin
        # `senderPn` (WhatsApp no siempre lo manda, a veces ni en el mismo
        # mensaje repetido): se usa el usuario tal cual, sin pisarle el
        # número real con el LID. Si es un número de verdad, es un usuario
        # dado de alta antes de que llegara `senderPn`, que quedó con el LID
        # como telefono_wa: se le corrige en vez de crearle una cuenta nueva.
        if usuario and telefono_wa != jid_wa.split("@")[0]:
            usuario.telefono_wa = telefono_wa
            usuario.celular_escrito = False  # ahora sí vino de WhatsApp
            db.session.commit()
    if usuario:
        if jid_wa and usuario.jid_wa != jid_wa:
            usuario.jid_wa = jid_wa
            db.session.commit()
        return usuario

    usuario = Usuario(telefono_wa=telefono_wa, jid_wa=jid_wa, rol="estudiante")
    db.session.add(usuario)
    db.session.commit()
    return usuario


def normalizar_telefono(valor: str) -> str | None:
    """Deja solo dígitos y antepone el indicativo de Colombia (57) a un
    celular de 10 dígitos escrito sin él -- mismo formato que usa
    telefono_wa (ver whatsapp_client). None si no queda un número
    plausible (8 a 15 dígitos)."""
    digitos = re.sub(r"\D", "", valor or "")
    if len(digitos) == 10 and digitos.startswith("3"):
        digitos = "57" + digitos
    return digitos if 8 <= len(digitos) <= 15 else None


def telefono_es_lid(usuario: Usuario) -> bool:
    """True si la persona escribe por JID "@lid" y aún no tenemos su número
    real: `telefono_wa` es el propio LID (ver obtener_o_crear_usuario)."""
    jid = usuario.jid_wa or ""
    return jid.endswith("@lid") and usuario.telefono_wa == jid.split("@")[0]


def asignar_celular(usuario: Usuario, telefono_normalizado: str) -> Usuario | None:
    """Guarda el celular escrito por la persona en `telefono_wa`. Si ya es de
    otra fila creada desde el dashboard (sin `jid_wa`) se fusiona la fila de
    `usuario` en esa: conserva sus datos, solo se le rellenan los campos
    vacíos (misma regla de "no pisar" que preparar_usuario_dashboard), se le
    pasan las reservas y se borra la fila vieja. Devuelve la fila que queda,
    o None si el número es de una cuenta que ya escribió por WhatsApp (no se
    toca nada)."""
    existente = Usuario.query.filter_by(telefono_wa=telefono_normalizado).first()
    if existente is None or existente.id == usuario.id:
        usuario.telefono_wa = telefono_normalizado
        usuario.celular_escrito = True
        db.session.commit()
        return usuario
    if existente.jid_wa:
        return None

    # ponytail: sin OTP no se prueba que el número sea de quien lo escribe; quien
    # sepa el de un cliente creado desde el dashboard puede reclamar esa fila.
    existente.celular_escrito = True
    existente.jid_wa = usuario.jid_wa
    for campo in ("nombre", "codigo_uninorte", "cedula"):
        if not getattr(existente, campo):
            setattr(existente, campo, getattr(usuario, campo))
    existente.registro_completo = False  # se marca solo tras el "sí" del resumen
    avisos = [a for a in (existente.aviso_privacidad_en, usuario.aviso_privacidad_en) if a]
    existente.aviso_privacidad_en = min(avisos) if avisos else None
    # Las dos únicas FK a usuarios.
    Reserva.query.filter_by(usuario_id=usuario.id).update({"usuario_id": existente.id})
    ReservaGrupo.query.filter_by(usuario_id=usuario.id).update({"usuario_id": existente.id})
    db.session.delete(usuario)
    db.session.commit()
    return existente


def preparar_usuario_dashboard(
    nombre: str, telefono: str, codigo_uninorte: str | None = None, cedula: str | None = None
) -> tuple[Usuario | None, bool, str | None]:
    """Da de alta (o reusa) el cliente de una reserva creada desde el
    dashboard, con las mismas reglas de validación que el registro por
    WhatsApp (registro_service). Devuelve (usuario, creado, error); en caso
    de error `usuario` es None.

    Si ya existe un Usuario con ese `telefono_wa`, se reusa esa fila y solo
    se rellenan los campos que tenía vacíos -- nunca se pisa un dato que el
    cliente ya había dado por WhatsApp. `registro_completo` solo se puede
    poner en True acá (nunca se apaga): si con estos datos ya quedan los
    tres campos, se marca completo igual que lo haría el bot.

    No hace commit -- solo `db.session.add`/`flush` -- para que la
    transacción la controle quien llama (la creación de la reserva), y así
    un choque de disponibilidad revierta también este alta."""
    nombre_valido = registro_service.validar_campo("nombre", nombre or "")
    if nombre_valido is None:
        return None, False, "Escribe nombre y apellido."

    telefono_valido = normalizar_telefono(telefono or "")
    if telefono_valido is None:
        return None, False, "Teléfono inválido: incluye el número de celular (ej. 3001234567)."

    codigo_valido = None
    if codigo_uninorte:
        codigo_valido = registro_service.validar_codigo_uninorte(codigo_uninorte)
        if codigo_valido is None:
            return None, False, "ID uninorte inválido (3 a 30 caracteres)."

    cedula_valida = None
    if cedula:
        cedula_valida = registro_service.validar_campo("cedula", cedula)
        if cedula_valida is None:
            return None, False, "Cédula inválida (5 a 15 dígitos)."

    usuario = Usuario.query.filter_by(telefono_wa=telefono_valido).first()
    if usuario:
        if not usuario.nombre:
            usuario.nombre = nombre_valido
        if not usuario.codigo_uninorte and codigo_valido:
            usuario.codigo_uninorte = codigo_valido
        if not usuario.cedula and cedula_valida:
            usuario.cedula = cedula_valida
        if usuario.nombre and usuario.codigo_uninorte and usuario.cedula:
            usuario.registro_completo = True
        db.session.flush()
        return usuario, False, None

    usuario = Usuario(
        telefono_wa=telefono_valido,
        nombre=nombre_valido,
        codigo_uninorte=codigo_valido,
        cedula=cedula_valida,
        rol="estudiante",
        registro_completo=bool(nombre_valido and codigo_valido and cedula_valida),
    )
    db.session.add(usuario)
    db.session.flush()
    return usuario, True, None
