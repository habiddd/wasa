"""
Alta de identidad del usuario por WhatsApp, un dato a la vez. Antes de
dejar reservar/consultar nada, el bot necesita saber quién es quién:
nombre completo, id de estudiante (código Uninorte) y cédula. El número de
celular no se pregunta -- ya lo tenemos, es el canal por el que está
escribiendo (telefono_wa) -- salvo a quien escribe por JID "@lid" sin número
real (telefono_wa es el LID): a esa persona se le pide de último.

El estado de "qué campo le toca responder" vive en la sesión de Redis
(ver sesiones.py), pero la fuente de verdad real es la propia fila de
Usuario: si por lo que sea la sesión expiró o se perdió, este módulo
vuelve a calcular el siguiente campo faltante mirando la base de datos,
así que nunca interpreta un mensaje random como respuesta a una pregunta
que ya no está pendiente.
"""

import re
import unicodedata
from datetime import datetime, timezone

from app import usuarios_service  # import circular: solo se usa en tiempo de ejecución
from app.extensions import db
from app.models import Usuario

# (nombre del campo en Usuario, pregunta a mandar)
CAMPOS_REGISTRO = [
    ("nombre", "¿Cuál es tu nombre completo?"),
    ("codigo_uninorte", "¿Cuál es tu ID o código de estudiante de Uninorte?"),
    ("cedula", "¿Cuál es tu número de cédula?"),
]

PREGUNTA_CELULAR = "¿Cuál es tu número de celular? (ej. 3001234567)"
PREGUNTAS = {**dict(CAMPOS_REGISTRO), "celular": PREGUNTA_CELULAR}
ETIQUETAS = {
    "nombre": "Nombre", "codigo_uninorte": "ID Uninorte", "cedula": "Cédula", "celular": "Celular",
}
RESPUESTAS_SI = {"si", "s", "ok", "correcto", "confirmo"}
RESPUESTAS_NO = {"no", "n"}

MENSAJE_CELULAR_OCUPADO = (
    "Ese número ya está registrado con otra cuenta de WhatsApp. Escribe tu número "
    "o comunícate con la LACS."
)

MENSAJE_BIENVENIDA = (
    "¡Hola! Soy el asistente de reservas de la LACS. Antes de ayudarte, necesito "
    "registrarte -- son unos datos rápidos."
)

MENSAJE_DATO_FALTANTE = "¡Hola de nuevo! Antes de seguir, necesito completar tu registro."

MENSAJE_AVISO_PRIVACIDAD = (
    "*AVISO*\n"
    "Esta conversación queda guardada y es monitoreada por la LACS para "
    "métricas de uso y archivo de las solicitudes."
)


def usuario_esta_completo(usuario: Usuario) -> bool:
    return usuario.registro_completo and not usuarios_service.telefono_es_lid(usuario)


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

    if campo == "celular":
        return usuarios_service.normalizar_telefono(valor)

    return valor


def _siguiente_campo_faltante(usuario: Usuario) -> tuple[str, str] | None:
    for campo, pregunta in CAMPOS_REGISTRO:
        if not getattr(usuario, campo):
            return campo, pregunta
    if usuarios_service.telefono_es_lid(usuario):
        return "celular", PREGUNTA_CELULAR
    return None


def _normalizar_respuesta(texto: str) -> str:
    """Minúsculas, sin tildes ni puntuación: "Sí." -> "si"."""
    sin_tildes = unicodedata.normalize("NFD", texto.lower())
    return re.sub(r"[^a-z0-9 ]", "", sin_tildes).strip()


def _resumen(usuario: Usuario) -> str:
    return (
        "Revisa tus datos:\n"
        f"• Nombre: {usuario.nombre}\n"
        f"• ID Uninorte: {usuario.codigo_uninorte}\n"
        f"• Cédula: {usuario.cedula}\n"
        f"• Celular: {usuario.telefono_wa}\n"
        "¿Son correctos? Responde *sí* para confirmar o *no* para corregir alguno."
    )


def _campos_corregibles(usuario: Usuario) -> list[str]:
    # El celular solo si lo escribió la persona: el que dio WhatsApp es la
    # identidad de la cuenta y no se toca.
    campos = [campo for campo, _ in CAMPOS_REGISTRO]
    return campos + ["celular"] if usuario.celular_escrito else campos


def _opciones_correccion(usuario: Usuario) -> str:
    lineas = [f"{i}. {ETIQUETAS[c]}" for i, c in enumerate(_campos_corregibles(usuario), 1)]
    return "¿Qué dato quieres corregir? Responde con el número:\n" + "\n".join(lineas)


def procesar_registro(usuario: Usuario, texto_usuario: str, sesion: dict) -> tuple[str, dict]:
    """Avanza el registro un campo y devuelve (respuesta, nueva_sesion).
    Se asume que ya se comprobó `not usuario_esta_completo(usuario)` antes
    de llamar esto."""
    campo_pendiente = sesion.get("registro_campo_pendiente")
    es_primera_pregunta = campo_pendiente is None

    # Registrado antes de que se pidiera el celular (llegó por LID): vuelve
    # a pasar por el registro y por el resumen final.
    if usuario.registro_completo:
        usuario.registro_completo = False
        db.session.commit()

    siguiente = _siguiente_campo_faltante(usuario)

    # Con todos los datos llenos, la sesión dice si está confirmando ("sí"/"no")
    # o eligiendo qué corregir. Sale de la BD, así que si la sesión se pierde
    # se vuelve a mandar el resumen.
    if siguiente is None and campo_pendiente == "confirmar":
        respuesta_norm = _normalizar_respuesta(texto_usuario)
        if respuesta_norm in RESPUESTAS_SI:
            usuario.registro_completo = True
            db.session.commit()
            return (
                "¡Listo, ya quedaste registrado! Ahora sí, ¿en qué te ayudo? Puedo reservar un "
                "equipo/espacio, consultar disponibilidad o cancelar una reserva.",
                {**sesion, "registro_campo_pendiente": None},
            )
        if respuesta_norm in RESPUESTAS_NO:
            return _opciones_correccion(usuario), {**sesion, "registro_campo_pendiente": "corregir"}
        return f"No entendí. {_resumen(usuario)}", sesion

    campos_corregibles = _campos_corregibles(usuario)
    if siguiente is None and campo_pendiente == "corregir":
        opcion = texto_usuario.strip()
        if opcion.isdigit() and 1 <= int(opcion) <= len(campos_corregibles):
            campo = campos_corregibles[int(opcion) - 1]
            return PREGUNTAS[campo], {**sesion, "registro_campo_pendiente": f"corregir:{campo}"}
        return _opciones_correccion(usuario), sesion

    # Si había un campo pendiente Y sigue siendo el que realmente falta en
    # la BD (por si la sesión quedó desincronizada), o es una corrección, lo
    # tratamos como respuesta a esa pregunta.
    campo_respuesta = None
    if campo_pendiente and campo_pendiente.startswith("corregir:"):
        campo_respuesta = campo_pendiente.split(":")[1]
    elif siguiente and campo_pendiente == siguiente[0]:
        campo_respuesta = campo_pendiente
    if campo_respuesta:
        valor = _validar(campo_respuesta, texto_usuario)
        if valor is None:
            return f"No entendí ese dato, intenta de nuevo. {PREGUNTAS[campo_respuesta]}", sesion
        if campo_respuesta == "celular":
            nuevo = usuarios_service.asignar_celular(usuario, valor)  # puede ser otra fila (fusión)
            if nuevo is None:
                return f"{MENSAJE_CELULAR_OCUPADO} {PREGUNTA_CELULAR}", sesion
            usuario = nuevo
        else:
            setattr(usuario, campo_respuesta, valor)
            db.session.commit()
        siguiente = _siguiente_campo_faltante(usuario)

    if siguiente is None:
        prefijo = "¡Gracias! " if campo_respuesta else ""
        return f"{prefijo}{_resumen(usuario)}", {**sesion, "registro_campo_pendiente": "confirmar"}

    campo, pregunta = siguiente
    if not es_primera_pregunta:
        prefijo = "¡Gracias! "
    elif usuario.nombre:  # ya registrado, solo le falta el celular
        prefijo = MENSAJE_DATO_FALTANTE + " "
    else:
        prefijo = MENSAJE_BIENVENIDA + " "
    respuesta = f"{prefijo}{pregunta}"

    # El aviso de tratamiento de datos abre el primer mensaje que recibe la
    # persona. Cuelga de la columna en Postgres y no de `es_primera_pregunta`
    # (que sale de la sesión de Redis) para que salga exactamente una vez: si
    # la sesión expira a medio registro la bienvenida se repite, el aviso no.
    # Se marca acá, al generar el texto, no cuando webhook.py confirma el
    # envío a WhatsApp: si `enviar_mensaje_texto` falla después, esta fila
    # queda marcada sin que la persona haya visto el aviso. Se acepta ese
    # riesgo en vez de partir esta lógica entre registro_service.py y
    # webhook.py por un caso de borde -- la verdad de si de verdad se
    # entregó ya está en `auditoria_bot.estado_envio`, que guarda el
    # `respuesta` completo (aviso incluido) junto a su estado de envío.
    if usuario.aviso_privacidad_en is None:
        usuario.aviso_privacidad_en = datetime.now(timezone.utc)
        db.session.commit()
        respuesta = f"{MENSAJE_AVISO_PRIVACIDAD}\n\n{respuesta}"

    return respuesta, {**sesion, "registro_campo_pendiente": campo}
