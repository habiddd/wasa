"""
Alta de identidad del usuario por WhatsApp, un dato a la vez. Antes de
dejar reservar/consultar nada, el bot necesita saber quién es quién:
nombre completo, id de estudiante (código Uninorte) y cédula. El número de
celular no se pregunta -- ya lo tenemos, es el canal por el que está
escribiendo (telefono_wa).

El estado de "qué campo le toca responder" vive en la sesión de Redis
(ver sesiones.py), pero la fuente de verdad real es la propia fila de
Usuario: si por lo que sea la sesión expiró o se perdió, este módulo
vuelve a calcular el siguiente campo faltante mirando la base de datos,
así que nunca interpreta un mensaje random como respuesta a una pregunta
que ya no está pendiente.
"""

from app.extensions import db
from app.models import Usuario

# (nombre del campo en Usuario, pregunta a mandar)
CAMPOS_REGISTRO = [
    ("nombre", "¿Cuál es tu nombre completo?"),
    ("codigo_uninorte", "¿Cuál es tu ID o código de estudiante de Uninorte?"),
    ("cedula", "¿Cuál es tu número de cédula?"),
]

MENSAJE_BIENVENIDA = (
    "¡Hola! Soy el asistente de reservas de la CPA. Antes de ayudarte, necesito "
    "registrarte -- son solo 3 datos rápidos."
)


def usuario_esta_completo(usuario: Usuario) -> bool:
    return usuario.registro_completo


def validar_codigo_uninorte(valor: str) -> str | None:
    """Código/ID de estudiante de Uninorte, limpio y válido, o None. Se usa
    tanto en el registro (campo del titular) como en el wizard de reservas
    (los ID de los demás integrantes del grupo, ver recursos_service.py)."""
    valor = valor.strip()
    return valor if 3 <= len(valor) <= 30 else None


def validar_campo(campo: str, valor: str) -> str | None:
    """Versión pública de `_validar`, para reusar las mismas reglas fuera
    del flujo de registro por WhatsApp (ver
    usuarios_service.preparar_usuario_dashboard)."""
    return _validar(campo, valor)


def _validar(campo: str, valor: str) -> str | None:
    """Devuelve el valor limpio y válido, o None si hay que pedirlo de nuevo."""
    valor = valor.strip()
    if not valor or len(valor) > 150:
        return None

    if campo == "nombre":
        # nombre completo: al menos dos palabras (nombre + apellido)
        return valor if len(valor.split()) >= 2 else None

    if campo == "cedula":
        solo_digitos = valor.replace(".", "").replace(" ", "").replace("-", "")
        return solo_digitos if solo_digitos.isdigit() and 5 <= len(solo_digitos) <= 15 else None

    if campo == "codigo_uninorte":
        return validar_codigo_uninorte(valor)

    return valor


def _siguiente_campo_faltante(usuario: Usuario) -> tuple[str, str] | None:
    for campo, pregunta in CAMPOS_REGISTRO:
        if not getattr(usuario, campo):
            return campo, pregunta
    return None


def procesar_registro(usuario: Usuario, texto_usuario: str, sesion: dict) -> tuple[str, dict]:
    """Avanza el registro un campo y devuelve (respuesta, nueva_sesion).
    Se asume que ya se comprobó `not usuario_esta_completo(usuario)` antes
    de llamar esto."""
    campo_pendiente = sesion.get("registro_campo_pendiente")
    es_primera_pregunta = campo_pendiente is None

    # Si había un campo pendiente Y sigue siendo el que realmente falta en
    # la BD (por si la sesión quedó desincronizada), lo tratamos como
    # respuesta a esa pregunta.
    siguiente = _siguiente_campo_faltante(usuario)
    if campo_pendiente and siguiente and campo_pendiente == siguiente[0]:
        valor = _validar(campo_pendiente, texto_usuario)
        if valor is None:
            _, pregunta = siguiente
            return f"No entendí ese dato, intenta de nuevo. {pregunta}", sesion
        setattr(usuario, campo_pendiente, valor)
        db.session.commit()
        siguiente = _siguiente_campo_faltante(usuario)

    if siguiente is None:
        usuario.registro_completo = True
        db.session.commit()
        return (
            "¡Listo, ya quedaste registrado! Ahora sí, ¿en qué te ayudo? Puedo reservar un "
            "equipo/espacio, consultar disponibilidad o cancelar una reserva.",
            {**sesion, "registro_campo_pendiente": None},
        )

    campo, pregunta = siguiente
    prefijo = MENSAJE_BIENVENIDA + " " if es_primera_pregunta else "¡Gracias! "
    return f"{prefijo}{pregunta}", {**sesion, "registro_campo_pendiente": campo}
