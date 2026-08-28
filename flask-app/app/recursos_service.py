"""
Disponibilidad de activos (recursos) contra datos reales de la base de
datos. Nada de esto debe delegarse al LLM: el LLM interpreta lenguaje
natural para llegar al intent, pero la respuesta sobre qué existe y qué
está disponible sale siempre de aquí (ver docs/ARQUITECTURA.md, sección
"Por qué separar el LLM de la lógica de reservas").
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError

from app import llm_client
from app.extensions import db
from app.models import Recurso, Reserva, Usuario
from app.textutils import normalizar as _normalizar

LONGITUD_MINIMA_PALABRA_CLAVE = 4
ZONA_HORARIA_CPA = ZoneInfo("America/Bogota")
MARGEN_INICIO_PASADO = timedelta(minutes=5)


def buscar_recursos_en_texto(texto: str) -> tuple[list[Recurso], bool]:
    """Devuelve (recursos que mejor coinciden con el texto, es_match_debil).

    Usa un score de palabras coincidentes (no solo boolean) para que una
    palabra genérica compartida entre varios recursos (ej. "cámara" en dos
    modelos distintos) no produzca ambigüedad falsa cuando el mensaje en
    realidad menciona una palabra distintiva (ej. "sony") que solo coincide
    con uno.

    `es_match_debil` es True cuando el mejor score viene de una sola
    palabra genérica (ej. "micrófono", "cámara") sin mención del nombre
    completo ni de una segunda palabra distintiva -- casos donde el match
    puede ser un falso positivo (ej. "grúa de cámara" coincide con
    "Cámara Sony" solo por la palabra "cámara", pero es un activo distinto
    que no tenemos). El llamador debe usarlo para agregar una salvedad en
    vez de responder con confianza total.

    Cuando hay varias unidades del mismo modelo (mismo nombre, distinta
    placa), se puntúa un solo
    representante por nombre: al usuario no le interesa la placa exacta,
    así que dos unidades idénticas nunca deben aparecer como dos
    "coincidencias" distintas que haya que desambiguar.

    Si en el futuro hace falta más precisión, este es el punto de
    extensión para pasarle la lista de nombres al LLM y que elija.
    """
    texto_norm = _normalizar(texto)
    puntuados: list[tuple[int, Recurso]] = []

    for _, representante in _grupos_activos():
        nombre_norm = _normalizar(representante.nombre)
        palabras = [p for p in nombre_norm.split() if len(p) >= LONGITUD_MINIMA_PALABRA_CLAVE]
        score = sum(1 for p in palabras if p in texto_norm)
        if nombre_norm in texto_norm:
            score += 10  # nombre completo mencionado: gana sobre cualquier match parcial
        if score > 0:
            puntuados.append((score, representante))

    if not puntuados:
        return [], False

    mejor_score = max(score for score, _ in puntuados)
    candidatos = [recurso for score, recurso in puntuados if score == mejor_score]
    return candidatos, mejor_score == 1


def _grupos_activos() -> list[tuple[str, Recurso]]:
    """(nombre, unidad_representante) por cada modelo distinto entre los
    recursos activos, ordenados por tipo y nombre. Varias filas de
    `recursos` pueden compartir nombre/marca/modelo (una por placa) --
    esto colapsa esas filas a una sola entrada por modelo."""
    recursos = Recurso.query.filter_by(activo=True).order_by(Recurso.tipo, Recurso.nombre, Recurso.id).all()
    vistos: dict[str, Recurso] = {}
    orden: list[str] = []
    for r in recursos:
        if r.nombre not in vistos:
            vistos[r.nombre] = r
            orden.append(r.nombre)
    return [(nombre, vistos[nombre]) for nombre in orden]


def _grupo(nombre: str) -> list[Recurso]:
    """Todas las unidades activas de un mismo modelo (mismo nombre,
    normalmente distinta placa). Son intercambiables para efectos de
    reserva -- al usuario
    le basta con pedir el modelo, no la placa específica."""
    return Recurso.query.filter_by(activo=True, nombre=nombre).order_by(Recurso.id).all()


def listar_nombres_disponibles() -> list[str]:
    return [nombre for nombre, _ in _grupos_activos()]


def _reserva_en_curso(recurso_id: int, ahora: datetime) -> Reserva | None:
    return (
        Reserva.query.filter(
            Reserva.recurso_id == recurso_id,
            Reserva.estado == "confirmada",
            Reserva.inicio <= ahora,
            Reserva.fin > ahora,
        )
        .first()
    )


def _proxima_reserva(recurso_id: int, ahora: datetime) -> Reserva | None:
    return (
        Reserva.query.filter(
            Reserva.recurso_id == recurso_id,
            Reserva.estado == "confirmada",
            Reserva.inicio > ahora,
        )
        .order_by(Reserva.inicio.asc())
        .first()
    )


def _estado_recurso(recurso: Recurso, ahora: datetime) -> str:
    """Descripción corta y determinista del estado actual de un recurso,
    en base a `reservas` -- nunca inventada por el LLM."""
    en_curso = _reserva_en_curso(recurso.id, ahora)
    if en_curso:
        return f"en uso ahora mismo, libre a las {en_curso.fin.strftime('%H:%M')}"

    proxima = _proxima_reserva(recurso.id, ahora)
    if proxima:
        inicio = proxima.inicio.strftime("%d/%m %H:%M")
        fin = proxima.fin.strftime("%H:%M")
        return f"libre ahora, pero reservado del {inicio} a las {fin}"

    return "libre, sin reservas próximas"


def _estado_grupo(nombre: str, ahora: datetime) -> str:
    """Como `_estado_recurso`, pero agregado sobre todas las unidades del
    mismo modelo -- con una sola unidad se comporta igual que antes; con
    varias, reporta cuántas están libres en vez del estado de una sola
    placa (que sería engañoso: otra unidad idéntica puede estar libre
    aunque la primera esté en uso)."""
    unidades = _grupo(nombre)
    if len(unidades) <= 1:
        return _estado_recurso(unidades[0], ahora) if unidades else "sin unidades activas"

    libres = [u for u in unidades if _reserva_en_curso(u.id, ahora) is None]
    if libres:
        return f"disponible ahora ({len(libres)} de {len(unidades)} unidades libres)"

    fin_mas_cercano = min(_reserva_en_curso(u.id, ahora).fin for u in unidades)
    return (
        f"las {len(unidades)} unidades están en uso ahora mismo, "
        f"la primera se libera a las {fin_mas_cercano.strftime('%H:%M')}"
    )


def evaluar_consulta_disponibilidad(texto_usuario: str) -> str:
    """Punto de entrada para el intent 'consultar_disponibilidad'. A
    diferencia de reservar, aquí el usuario quiere saber qué hay y qué no
    -- así que ante varias coincidencias (o ninguna) se listan todas con
    su estado, en vez de preguntar cuál de todas necesita."""
    ahora = datetime.now(timezone.utc)
    candidatos, es_match_debil = buscar_recursos_en_texto(texto_usuario)

    if not candidatos:
        grupos = _grupos_activos()
        if not grupos:
            return "Por ahora no tengo activos cargados en el sistema. Contacta al equipo de la CPA."
        lineas = "\n".join(f"- {nombre}: {_estado_grupo(nombre, ahora)}" for nombre, _ in grupos)
        return f"No identifiqué un activo específico, así que aquí tienes el estado de todo:\n{lineas}"

    lineas = "\n".join(f"- {r.nombre}: {_estado_grupo(r.nombre, ahora)}" for r in candidatos)
    salvedad = (
        "\n(si buscabas otro activo, dime el nombre completo)" if es_match_debil else ""
    )
    return f"Esto es lo que tengo:\n{lineas}{salvedad}"


def _reserva_solapada(recurso_id: int, inicio: datetime, fin: datetime) -> Reserva | None:
    return (
        Reserva.query.filter(
            Reserva.recurso_id == recurso_id,
            Reserva.estado == "confirmada",
            Reserva.inicio < fin,
            Reserva.fin > inicio,
        )
        .first()
    )


def _unidad_libre_en_rango(nombre: str, inicio: datetime, fin: datetime) -> Recurso | None:
    """Primera unidad del grupo sin choque de horario, o None si todas
    están ocupadas. No importa cuál unidad específica se devuelva -- todas
    las del mismo modelo son equivalentes para el usuario, que solo pidió
    el modelo, no una placa."""
    for unidad in _grupo(nombre):
        if _reserva_solapada(unidad.id, inicio, fin) is None:
            return unidad
    return None


def crear_reserva(
    usuario_id: int, recurso_id: int, inicio: datetime, fin: datetime
) -> tuple[Reserva | None, str | None]:
    """Inserta la reserva. La protección real contra choques es la
    restricción EXCLUDE de Postgres sobre (recurso_id, rango horario)
    para estado='confirmada' (ver postgres/init/02-schema.sql) -- el
    chequeo de `_reserva_solapada` antes de llamar a esta función es solo
    para dar un mensaje más claro, no la garantía de consistencia."""
    reserva = Reserva(usuario_id=usuario_id, recurso_id=recurso_id, inicio=inicio, fin=fin, estado="confirmada")
    db.session.add(reserva)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return None, "choque"
    return reserva, None


def _resolver_recurso_para_reserva(
    texto_usuario: str, extraido: dict
) -> tuple[Recurso | None, list[Recurso], bool]:
    """Devuelve (recurso_resuelto, candidatos_por_palabra_clave, es_match_debil).
    `recurso_resuelto` es None si hace falta desambiguar con el usuario
    (ver los otros dos valores para construir ese mensaje)."""
    candidatos, es_match_debil = buscar_recursos_en_texto(texto_usuario)

    nombre_llm = (extraido.get("recurso") or "").strip().lower()
    recurso_llm = None
    if nombre_llm:
        recurso_llm = next(
            (r for r in Recurso.query.filter_by(activo=True).all() if r.nombre.strip().lower() == nombre_llm),
            None,
        )

    if recurso_llm and (not candidatos or len(candidatos) > 1 or recurso_llm in candidatos):
        return recurso_llm, candidatos, False

    if len(candidatos) == 1:
        return candidatos[0], candidatos, es_match_debil

    return None, candidatos, es_match_debil


def _nota_seguro(recurso: Recurso) -> str:
    return (
        " Este activo requiere seguro -- el equipo de la CPA te contactará para completar ese trámite."
        if recurso.requiere_seguro
        else ""
    )


PALABRAS_CONFIRMAR = {"si", "confirmo", "confirmar", "dale", "ok", "vale", "correcto", "claro"}
PALABRAS_CANCELAR = {"no", "cancelar", "cancela"}


def gestionar_reserva(texto_usuario: str, usuario: Usuario, sesion: dict) -> tuple[str, dict]:
    """Punto de entrada para el intent 'reservar'. Intenta resolver activo
    + fecha/hora en un solo mensaje (el bot no tiene memoria de lo que se
    dijo en mensajes previos que no haya quedado en `sesion`, así que no
    puede completar datos que falten en turnos separados) y, si lo logra,
    NO reserva todavía -- manda un resumen y deja la reserva pendiente en
    `sesion` hasta que el usuario la confirme (ver confirmar_reserva_pendiente).
    El LLM solo se usa para extraer {recurso, inicio, fin} del texto libre
    -- nunca decide si hay disponibilidad ni escribe en la base de datos."""
    ahora_utc = datetime.now(timezone.utc)
    ahora_local = ahora_utc.astimezone(ZONA_HORARIA_CPA)
    nombres = listar_nombres_disponibles()

    extraido = llm_client.extraer_datos_reserva(texto_usuario, nombres, ahora_local) or {}

    recurso, candidatos, es_match_debil = _resolver_recurso_para_reserva(texto_usuario, extraido)

    if recurso is None:
        if not candidatos:
            if not nombres:
                return "Por ahora no tengo activos cargados en el sistema. Contacta al equipo de la CPA.", sesion
            lista = "\n".join(f"- {n}" for n in nombres)
            return (
                "No identifiqué qué activo necesitas, o no lo tenemos disponible. "
                f"Esto es lo que sí tenemos:\n{lista}"
            ), sesion
        nombres_candidatos = ", ".join(r.nombre for r in candidatos)
        return (
            f"Encontré varias coincidencias: {nombres_candidatos}. ¿Cuál de estos necesitas? "
            "Dime el nombre completo junto con la fecha y hora, por ejemplo: "
            f"'{candidatos[0].nombre} el viernes de 3pm a 5pm'."
        ), sesion

    salvedad = (
        f" (si no te refieres a '{recurso.nombre}', dime el nombre completo del activo)"
        if es_match_debil
        else ""
    )

    inicio_str, fin_str = extraido.get("inicio"), extraido.get("fin")
    if not inicio_str or not fin_str:
        estado = _estado_grupo(recurso.nombre, ahora_utc)
        return (
            f"'{recurso.nombre}' está {estado}. Para reservarla dime también la fecha y hora en "
            f"el mismo mensaje, por ejemplo: '{recurso.nombre} el viernes de 3pm a 5pm'.{salvedad}"
        ), sesion

    try:
        inicio_local = datetime.strptime(inicio_str, "%Y-%m-%d %H:%M").replace(tzinfo=ZONA_HORARIA_CPA)
        fin_local = datetime.strptime(fin_str, "%Y-%m-%d %H:%M").replace(tzinfo=ZONA_HORARIA_CPA)
    except ValueError:
        return (
            "No logré entender la fecha/hora que diste. ¿Puedes escribirla de forma más clara? "
            f"Ejemplo: '{recurso.nombre} el viernes de 3pm a 5pm'."
        ), sesion

    inicio_utc, fin_utc = inicio_local.astimezone(timezone.utc), fin_local.astimezone(timezone.utc)

    if fin_utc <= inicio_utc:
        return "La hora de fin debe ser después de la hora de inicio. ¿Puedes revisar el horario?", sesion

    if inicio_utc < ahora_utc - MARGEN_INICIO_PASADO:
        return "Esa fecha/hora ya pasó. Dime una fecha y hora futura.", sesion

    if _unidad_libre_en_rango(recurso.nombre, inicio_utc, fin_utc) is None:
        return (
            f"'{recurso.nombre}' ya está reservado (todas sus unidades) en ese horario. "
            "Prueba con otro horario."
        ), sesion

    nueva_sesion = {
        **sesion,
        "reserva_pendiente": {
            # Se guarda el modelo, no una placa fija: cuál unidad concreta se
            # asigna se decide recién en confirmar_reserva_pendiente, sobre
            # la disponibilidad más reciente (cualquier unidad libre sirve).
            "nombre": recurso.nombre,
            "inicio": inicio_utc.isoformat(),
            "fin": fin_utc.isoformat(),
        },
    }
    resumen = (
        "Resumen de tu reserva:\n"
        f"- Activo: {recurso.nombre}\n"
        f"- Reservado por: {usuario.nombre}\n"
        f"- Inicio: {inicio_local.strftime('%d/%m/%Y %H:%M')}\n"
        f"- Fin: {fin_local.strftime('%d/%m/%Y %H:%M')}\n\n"
        "¿Confirmas? Responde 'sí' para confirmar o 'no' para cancelar."
        f"{salvedad}"
    )
    return resumen, nueva_sesion


def confirmar_reserva_pendiente(texto_usuario: str, usuario: Usuario, sesion: dict) -> tuple[str, dict]:
    """Punto de entrada cuando `sesion` ya tiene una 'reserva_pendiente' (el
    usuario vio el resumen de gestionar_reserva y este mensaje es su
    respuesta). Solo aquí se escribe de verdad en `reservas` -- todo lo de
    arriba solo prepara los datos."""
    pendiente = sesion["reserva_pendiente"]
    sesion_limpia = {**sesion, "reserva_pendiente": None}

    palabras = set(_normalizar(texto_usuario).split())

    if palabras & PALABRAS_CANCELAR:
        return (
            "Reserva cancelada, no se guardó nada. Si quieres reservar otra cosa, "
            "dime el activo, fecha y hora."
        ), sesion_limpia

    if not (palabras & PALABRAS_CONFIRMAR):
        return (
            "No entendí tu respuesta. Responde 'sí' para confirmar la reserva o 'no' para cancelarla."
        ), sesion

    nombre = pendiente["nombre"]
    grupo = _grupo(nombre)
    if not grupo:
        return "Ese activo ya no está disponible. Si quieres, intenta reservar de nuevo.", sesion_limpia

    inicio_utc = datetime.fromisoformat(pendiente["inicio"])
    fin_utc = datetime.fromisoformat(pendiente["fin"])
    ahora_utc = datetime.now(timezone.utc)

    if inicio_utc < ahora_utc - MARGEN_INICIO_PASADO:
        return (
            "Esa fecha/hora ya pasó mientras esperaba tu confirmación. Intenta reservar de nuevo "
            "con una fecha futura."
        ), sesion_limpia

    if _unidad_libre_en_rango(nombre, inicio_utc, fin_utc) is None:
        return (
            f"'{nombre}' se acaba de reservar (todas sus unidades) mientras esperaba tu "
            "confirmación, y se cruza con tu horario. Prueba con otro horario."
        ), sesion_limpia

    # Puede haber más de una unidad libre para el mismo modelo; si la
    # primera choca justo en el INSERT (dos usuarios confirmando a la vez),
    # se prueba con la siguiente -- la garantía real contra choques es el
    # EXCLUDE de Postgres que atrapa crear_reserva, esto solo evita que una
    # colisión rara en una unidad tumbe una reserva que otra unidad sí
    # podía cubrir.
    for candidata in grupo:
        reserva, error = crear_reserva(usuario.id, candidata.id, inicio_utc, fin_utc)
        if error is None:
            inicio_local = inicio_utc.astimezone(ZONA_HORARIA_CPA)
            fin_local = fin_utc.astimezone(ZONA_HORARIA_CPA)
            return (
                f"Listo, reservé '{nombre}' del {inicio_local.strftime('%d/%m %H:%M')} "
                f"a las {fin_local.strftime('%H:%M')}.{_nota_seguro(candidata)}"
            ), sesion_limpia

    return (
        f"'{nombre}' ya no está disponible en ese horario. Prueba con otro horario."
    ), sesion_limpia
