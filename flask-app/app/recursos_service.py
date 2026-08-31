"""
Disponibilidad de activos (recursos) contra datos reales de la base de
datos. Nada de esto debe delegarse al LLM: el LLM interpreta lenguaje
natural para llegar al intent, pero la respuesta sobre qué existe y qué
está disponible sale siempre de aquí (ver docs/ARQUITECTURA.md, sección
"Por qué separar el LLM de la lógica de reservas").
"""

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError

from app import llm_client
from app.extensions import db
from app.models import Recurso, Reserva, ReservaGrupo, Usuario
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
        score = _puntaje_nombre_en_texto(representante.nombre, texto_norm)
        if score > 0:
            puntuados.append((score, representante))

    if not puntuados:
        return [], False

    mejor_score = max(score for score, _ in puntuados)
    candidatos = [recurso for score, recurso in puntuados if score == mejor_score]
    return candidatos, mejor_score == 1


def _puntaje_nombre_en_texto(nombre: str, texto_norm: str) -> int:
    """Cuántas palabras clave (>= LONGITUD_MINIMA_PALABRA_CLAVE) del nombre
    de un recurso aparecen en un texto ya normalizado, +10 si el nombre
    completo aparece tal cual. 0 si no hay ningún indicio real -- usado
    tanto para puntuar candidatos por palabra clave (`buscar_recursos_en_texto`)
    como para exigirle respaldo textual a lo que devuelve el LLM antes de
    confiarlo a ciegas (ver `_resolver_recurso_para_reserva`)."""
    nombre_norm = _normalizar(nombre)
    palabras = [p for p in nombre_norm.split() if len(p) >= LONGITUD_MINIMA_PALABRA_CLAVE]
    score = sum(1 for p in palabras if p in texto_norm)
    if nombre_norm in texto_norm:
        score += 10
    return score


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


def _accesorios_por_defecto(nombre_padre: str) -> list[str]:
    """Nombres (modelo) de los accesorios que se agregan automáticamente al
    reservar `nombre_padre` -- hijos activos con `incluido_por_defecto=True`
    de cualquier unidad del grupo (no de una placa específica: el wizard ya
    trata todo por modelo y solo baja a una unidad concreta al crear la
    reserva, ver `_unidad_libre_en_rango`). Deduplicado por nombre."""
    ids_padres = [u.id for u in _grupo(nombre_padre)]
    if not ids_padres:
        return []
    hijos = (
        Recurso.query.filter_by(activo=True, incluido_por_defecto=True)
        .filter(Recurso.padre_id.in_(ids_padres))
        .order_by(Recurso.id)
        .all()
    )
    vistos: list[str] = []
    for h in hijos:
        if h.nombre not in vistos:
            vistos.append(h.nombre)
    return vistos


def listar_nombres_disponibles() -> list[str]:
    return [nombre for nombre, _ in _grupos_activos()]


def _categorias_activas() -> list[str]:
    """Categorías distintas entre los equipos activos (ej. 'camara',
    'celular', 'dron'), en el orden en que aparecen al agrupar por modelo.
    Permite resolver "qué cámaras tenemos" aunque "cámaras" no coincida
    con ningún nombre de modelo -- ver `_detectar_categoria_en_texto`."""
    vistas: list[str] = []
    for _, representante in _grupos_activos():
        cat = representante.categoria
        if cat and cat not in vistas:
            vistas.append(cat)
    return vistas


def _grupos_por_categoria(categoria: str) -> list[tuple[str, Recurso]]:
    """(nombre, representante) de cada modelo distinto dentro de una
    categoría (comparación normalizada, sin tildes/mayúsculas)."""
    cat_norm = _normalizar(categoria)
    return [
        (nombre, representante)
        for nombre, representante in _grupos_activos()
        if representante.categoria and _normalizar(representante.categoria) == cat_norm
    ]


def _detectar_categoria_en_texto(texto: str) -> str | None:
    """Busca si el texto del usuario menciona una categoría conocida (ej.
    "cámaras", "celulares") aunque no mencione ningún modelo o marca
    específicos. La comparación por substring cubre el plural regular en
    español ("camara" está contenido en "camaras") sin necesitar una
    lista de sinónimos aparte."""
    texto_norm = _normalizar(texto)
    for categoria in _categorias_activas():
        if _normalizar(categoria) in texto_norm:
            return categoria
    return None


def _describir_modelo(nombre: str, representante: Recurso) -> str:
    marca_modelo = " ".join(p for p in [representante.marca, representante.modelo] if p)
    return f"{nombre} ({marca_modelo})" if marca_modelo else nombre


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
        categoria = _detectar_categoria_en_texto(texto_usuario)
        if categoria:
            grupos_categoria = _grupos_por_categoria(categoria)
            if grupos_categoria:
                lineas = "\n".join(
                    f"- {_describir_modelo(nombre, rep)}: {_estado_grupo(nombre, ahora)}"
                    for nombre, rep in grupos_categoria
                )
                return f"Esto es lo que tenemos en '{categoria}':\n{lineas}"

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


def _resolver_disponibilidad_carrito(
    carrito: list[dict], inicio: datetime, fin: datetime
) -> tuple[list[dict], list[Recurso], list[str]]:
    """Revisa la disponibilidad de cada ítem del carrito en el rango dado.
    Ya no distingue accesorio automático de elegido a mano: cualquier ítem
    sin unidad libre en ese horario -- sea el activo que el usuario pidió o
    un hijo que se incluyó por defecto -- se reporta en `no_disponibles`
    para que el llamador se lo diga al usuario y le pregunte qué hacer (ver
    paso "resolver_disponibilidad" en `_continuar_reserva`), en vez de
    decidir en silencio por él. Cuando `no_disponibles` viene vacío,
    `disponibles` y `unidades` quedan en el mismo orden -- una unidad por
    ítem, listas para reservar."""
    disponibles: list[dict] = []
    unidades: list[Recurso] = []
    no_disponibles: list[str] = []

    for item in carrito:
        unidad = _unidad_libre_en_rango(item["nombre_recurso"], inicio, fin)
        if unidad is not None:
            disponibles.append(item)
            unidades.append(unidad)
        else:
            no_disponibles.append(item["nombre_recurso"])

    return disponibles, unidades, no_disponibles


def crear_reservas_grupo(
    usuario_id: int, recursos: list[Recurso], inicio: datetime, fin: datetime
) -> tuple[list[Reserva] | None, str | None]:
    """Inserta una reserva por cada recurso en `recursos`, todas ligadas al
    mismo ReservaGrupo y en una sola transacción -- si alguna choca al
    confirmar (la restricción EXCLUDE de Postgres sobre (recurso_id, rango
    horario) para estado='confirmada', ver postgres/init/02-schema.sql), se
    revierte todo el grupo en vez de dejar una reserva parcial del carrito.
    El chequeo de disponibilidad antes de llamar a esta función (ver
    `_resolver_disponibilidad_carrito` / `_unidad_libre_en_rango`) es solo
    para dar un mensaje más claro, no la garantía de consistencia."""
    grupo = ReservaGrupo(usuario_id=usuario_id)
    db.session.add(grupo)
    db.session.flush()  # asigna grupo.id sin cerrar la transacción

    reservas = [
        Reserva(
            usuario_id=usuario_id,
            recurso_id=recurso.id,
            inicio=inicio,
            fin=fin,
            estado="confirmada",
            grupo_id=grupo.id,
        )
        for recurso in recursos
    ]
    db.session.add_all(reservas)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return None, "choque"
    return reservas, None


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
        candidato_llm = next(
            (r for r in Recurso.query.filter_by(activo=True).all() if r.nombre.strip().lower() == nombre_llm),
            None,
        )
        # El prompt le pide al LLM "recurso: null si el mensaje no lo
        # menciona con claridad", pero en la práctica no siempre lo respeta
        # (ej. "quiero reservar" a secas, sin mencionar ningún activo,
        # puede devolver igual un nombre inventado). Se exige que ese
        # nombre tenga respaldo real en lo que escribió el usuario --mismo
        # criterio de palabra clave que `buscar_recursos_en_texto`-- antes
        # de confiarlo; si no, se trata como si el LLM hubiera dicho null.
        if candidato_llm is not None and _puntaje_nombre_en_texto(candidato_llm.nombre, _normalizar(texto_usuario)) > 0:
            recurso_llm = candidato_llm

    if recurso_llm and (not candidatos or len(candidatos) > 1 or recurso_llm in candidatos):
        return recurso_llm, candidatos, False

    if len(candidatos) == 1:
        return candidatos[0], candidatos, es_match_debil

    return None, candidatos, es_match_debil


def _nota_seguro(recurso: Recurso) -> str:
    """Línea aparte (no inline) para el mensaje de confirmación -- lleva el
    nombre del activo porque puede haber más de uno en el carrito que
    requiera seguro, y sin el nombre no se sabría a cuál se refiere."""
    return (
        f"⚠ {recurso.nombre} requiere seguro -- el equipo de la CPA te contactará para completar ese trámite."
        if recurso.requiere_seguro
        else ""
    )


PALABRAS_CANCELAR = {"no", "cancelar", "cancela", "cerrar"}

_DIAS_ES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def _formatear_hora(dt_local: datetime) -> str:
    return dt_local.strftime("%I:%M %p").lstrip("0").lower().replace("am", "a.m.").replace("pm", "p.m.")


def _formatear_fecha_hora(dt_local: datetime) -> str:
    """'viernes 29/08/2026, 3:00 p.m.' -- incluye el día de la semana y
    hora en formato 12h a propósito: es lo que se le muestra al usuario
    apenas da una fecha, para que note de inmediato si el bot entendió
    otro día o AM/PM al revés y pueda corregirlo ahí mismo, no hasta el
    final."""
    dia = _DIAS_ES[dt_local.weekday()]
    return f"{dia} {dt_local.strftime('%d/%m/%Y')}, {_formatear_hora(dt_local)}"


def _parsear_fecha_hora(
    inicio_str: str | None, fin_str: str | None, ahora_utc: datetime
) -> tuple[datetime | None, datetime | None, str | None]:
    """Convierte los strings "YYYY-MM-DD HH:MM" (hora de Bogotá) que
    devuelve el LLM a datetimes UTC, con las mismas validaciones que antes
    tenía gestionar_reserva inline. Devuelve (None, None, None) si
    sencillamente no había dato (no es un error); (None, None, mensaje) si
    hay un error que mostrarle al usuario; o (inicio, fin, None) si es
    válido."""
    if not inicio_str or not fin_str:
        return None, None, None

    try:
        inicio_local = datetime.strptime(inicio_str, "%Y-%m-%d %H:%M").replace(tzinfo=ZONA_HORARIA_CPA)
        fin_local = datetime.strptime(fin_str, "%Y-%m-%d %H:%M").replace(tzinfo=ZONA_HORARIA_CPA)
    except ValueError:
        return None, None, (
            "No logré entender la fecha/hora que diste. ¿Puedes escribirla de forma más clara? "
            "Ejemplo: 'el viernes de 3pm a 5pm'."
        )

    inicio_utc, fin_utc = inicio_local.astimezone(timezone.utc), fin_local.astimezone(timezone.utc)

    if fin_utc <= inicio_utc:
        return None, None, "La hora de fin debe ser después de la hora de inicio. ¿Puedes revisar el horario?"

    if inicio_utc < ahora_utc - MARGEN_INICIO_PASADO:
        return None, None, "Esa fecha/hora ya pasó. Dime una fecha y hora futura."

    return inicio_utc, fin_utc, None


_PATRON_FECHA = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$")
_PATRON_HORA = re.compile(
    r"^\s*(\d{1,2})(?::(\d{2}))?\s*(a\.?\s*m\.?|p\.?\s*m\.?)?\s*a\s*"
    r"(\d{1,2})(?::(\d{2}))?\s*(a\.?\s*m\.?|p\.?\s*m\.?)?\s*$",
    re.IGNORECASE,
)


def _parsear_fecha_dd_mm_aaaa(texto: str, ahora_local: datetime) -> tuple[date | None, str | None]:
    """Exige el formato dd/mm/aaaa (ej. 22/11/2026) -- nada de lenguaje
    natural para la fecha: es determinista, no depende del LLM, y evita
    que se "invente" una fecha/hora que el usuario nunca dio (ver
    PROMPT_EXTRACCION, que en la práctica no siempre devuelve null cuando
    debería). Devuelve (fecha, None) o (None, mensaje_de_error)."""
    m = _PATRON_FECHA.match(texto)
    if not m:
        return None, "No reconocí ese formato. Escribe la fecha como dd/mm/aaaa, por ejemplo 22/11/2026."

    dia, mes, anio = (int(g) for g in m.groups())
    try:
        fecha = date(anio, mes, dia)
    except ValueError:
        return None, "Esa fecha no existe. Escríbela como dd/mm/aaaa, por ejemplo 22/11/2026."

    if fecha < ahora_local.date():
        return None, "Esa fecha ya pasó. Dime una fecha de hoy en adelante, en formato dd/mm/aaaa."

    return fecha, None


_MESES_ES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def _interpretar_fecha_natural(texto: str, ahora_local: datetime) -> date | None:
    """Segundo intento cuando el texto no vino en dd/mm/aaaa (ej. "mañana",
    "el viernes", "22 de noviembre"). A diferencia de
    `_parsear_fecha_dd_mm_aaaa` esto SÍ es una interpretación, así que
    quien llama a esta función debe pedirle confirmación al usuario antes
    de darla por buena -- nunca aceptarla directo (ver `_continuar_reserva`,
    paso "fecha"). Devuelve None si no reconoce nada."""
    texto_norm = _normalizar(texto.strip())
    hoy = ahora_local.date()

    if texto_norm == "hoy":
        return hoy
    if texto_norm in ("pasado manana", "pasadomanana"):
        return hoy + timedelta(days=2)
    if texto_norm == "manana":
        return hoy + timedelta(days=1)

    m = re.match(r"^en\s+(\d+)\s+dias?$", texto_norm)
    if m:
        return hoy + timedelta(days=int(m.group(1)))

    for i, dia in enumerate(_DIAS_ES):
        if _normalizar(dia) in texto_norm:
            delta = (i - hoy.weekday()) % 7
            if delta == 0 and any(p in texto_norm for p in ("proximo", "que viene", "entrante")):
                delta = 7
            return hoy + timedelta(days=delta)

    m = re.match(r"^(\d{1,2})\s+de\s+([a-z]+)(?:\s+de\s+(\d{4}))?$", texto_norm)
    if m and m.group(2) in _MESES_ES:
        dia = int(m.group(1))
        mes = _MESES_ES.index(m.group(2)) + 1
        anio = int(m.group(3)) if m.group(3) else hoy.year
        try:
            fecha = date(anio, mes, dia)
        except ValueError:
            return None
        if not m.group(3) and fecha < hoy:
            fecha = date(anio + 1, mes, dia)
        return fecha

    m = re.match(r"^(\d{1,2})[.\-](\d{1,2})[.\-](\d{4})$", texto_norm)
    if m:
        dia, mes, anio = (int(g) for g in m.groups())
        try:
            return date(anio, mes, dia)
        except ValueError:
            return None

    return None


def _parsear_rango_horario(texto: str) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """Interpreta un rango como '3:00pm a 5:00pm', '3pm a 5pm' o '15:00 a
    17:00'. Devuelve ((h_inicio, m_inicio), (h_fin, m_fin)) en 24h, o None
    si el texto no matchea ese patrón."""
    m = _PATRON_HORA.match(texto)
    if not m:
        return None
    h1, m1, ampm1, h2, m2, ampm2 = m.groups()
    h1, m1 = int(h1), int(m1 or 0)
    h2, m2 = int(h2), int(m2 or 0)
    if not (0 <= m1 <= 59 and 0 <= m2 <= 59):
        return None

    def _normalizar_ampm(valor: str | None) -> str | None:
        if not valor:
            return None
        return "pm" if valor.lower().replace(".", "").replace(" ", "") == "pm" else "am"

    p1, p2 = _normalizar_ampm(ampm1), _normalizar_ampm(ampm2)
    # si un lado no trae am/pm explícito pero el otro sí, se asume el mismo
    if p1 is None and p2 is not None:
        p1 = p2
    if p2 is None and p1 is not None:
        p2 = p1

    def _a_24h(hora: int, periodo: str | None) -> int | None:
        if periodo is None:
            return hora if 0 <= hora <= 23 else None  # ya viene en 24h
        if not (1 <= hora <= 12):
            return None
        hora = 0 if hora == 12 else hora
        return hora + 12 if periodo == "pm" else hora

    h1_24, h2_24 = _a_24h(h1, p1), _a_24h(h2, p2)
    if h1_24 is None or h2_24 is None:
        return None
    return (h1_24, m1), (h2_24, m2)


def _interpretar_hora_relajada(texto: str) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """Segundo intento cuando el rango de hora no vino en el formato
    exacto pero es reconocible con variaciones comunes (ej. "de 2pm a
    7pm", "desde las 3 hasta las 5pm"). Igual que
    `_interpretar_fecha_natural`, es una interpretación -- el llamador
    debe confirmarla con el usuario antes de darla por buena."""
    texto_limpio = re.sub(r"^\s*(de|desde|entre)\s+", "", texto, flags=re.IGNORECASE)
    texto_limpio = re.sub(r"^\s*las\s+", "", texto_limpio, flags=re.IGNORECASE)
    texto_limpio = re.sub(r"\bhasta\b", "a", texto_limpio, flags=re.IGNORECASE)
    texto_limpio = re.sub(r"\ba\s+las\s+", "a ", texto_limpio, flags=re.IGNORECASE)
    texto_limpio = re.sub(r"\bhrs?\.?\b|\bhoras?\b", "", texto_limpio, flags=re.IGNORECASE)
    texto_limpio = re.sub(r"\s+", " ", texto_limpio).strip()
    return _parsear_rango_horario(texto_limpio)


# --- Wizard de reserva paso a paso -----------------------------------------
#
# En vez de pedirle al usuario todo en un solo mensaje ("activo + fecha +
# hora"), se le pregunta un dato a la vez con menús numerados (tipo -> marca
# -> modelo -> fecha), igual que registro_service.py ya hace con
# nombre/código/cédula. `sesion["reserva_wizard"]` guarda el progreso; si es
# None, no hay una reserva en curso.
#
# Si el primer mensaje ya trae varios datos (ej. "cámara sony a7iii mañana
# a las 3pm"), se pre-llenan los pasos que ya se pudieron resolver y solo se
# pregunta lo que falta -- ver `_iniciar_reserva`.


def _menu_paso_activo() -> list[tuple[str, str]]:
    """Opciones del primer paso: una por cada categoría de equipo (ej.
    'camara') y una por cada espacio individual (un espacio no tiene
    categoría/marca/modelo, así que se resuelve directo en este paso)."""
    opciones = []
    for categoria in _categorias_activas():
        opciones.append((f"categoria:{categoria}", categoria.capitalize()))
    for nombre, representante in _grupos_activos():
        if representante.tipo == "espacio":
            opciones.append((f"nombre:{nombre}", nombre))
    return opciones


def _formatear_menu_activo(opciones: list[tuple[str, str]]) -> str:
    equipos = [(i, etiqueta) for i, (valor, etiqueta) in enumerate(opciones, start=1) if valor.startswith("categoria:")]
    espacios = [(i, etiqueta) for i, (valor, etiqueta) in enumerate(opciones, start=1) if valor.startswith("nombre:")]
    partes = ["¿Qué necesitas reservar?"]
    if equipos:
        partes.append("\nEquipos:")
        partes.extend(f"{i}. {etiqueta}" for i, etiqueta in equipos)
    if espacios:
        partes.append("\nEspacios:")
        partes.extend(f"{i}. {etiqueta}" for i, etiqueta in espacios)
    partes.append("\nResponde con el número.")
    return "\n".join(partes)


def _marcas_de_categoria(categoria: str) -> list[str]:
    vistas: list[str] = []
    for _, representante in _grupos_por_categoria(categoria):
        if representante.marca and representante.marca not in vistas:
            vistas.append(representante.marca)
    return vistas


def _modelos_de_marca(categoria: str, marca: str) -> list[tuple[str, Recurso]]:
    return [
        (nombre, representante)
        for nombre, representante in _grupos_por_categoria(categoria)
        if representante.marca == marca
    ]


def _formatear_menu(pregunta: str, opciones: list[tuple[str, str]]) -> str:
    lineas = "\n".join(f"{i}. {etiqueta}" for i, (_, etiqueta) in enumerate(opciones, start=1))
    return f"{pregunta}\n{lineas}\n\nResponde con el número."


def _interpretar_opcion(texto_usuario: str, opciones: list[tuple[str, str]]) -> str | None:
    """Interpreta la respuesta a un menú numerado: por número (1, 2, ...)
    o, si el usuario escribió el nombre de la opción en vez del número,
    por coincidencia de texto. Devuelve el valor interno de la opción
    elegida, o None si no se pudo determinar con certeza."""
    texto = texto_usuario.strip()
    coincidencia_numero = re.match(r"^(\d+)", texto)
    if coincidencia_numero:
        idx = int(coincidencia_numero.group(1)) - 1
        return opciones[idx][0] if 0 <= idx < len(opciones) else None

    texto_norm = _normalizar(texto)
    coincidencias = [
        valor
        for valor, etiqueta in opciones
        if _normalizar(etiqueta) in texto_norm or texto_norm in _normalizar(etiqueta)
    ]
    return coincidencias[0] if len(coincidencias) == 1 else None


def _aplicar_respuesta_menu(wizard: dict, texto_usuario: str) -> str | None:
    """Interpreta la respuesta del usuario al menú del paso actual y
    actualiza `wizard` in place. Devuelve un mensaje de error si no se
    pudo interpretar, o None si se aplicó correctamente."""
    opciones = wizard.get("opciones_actuales") or []
    valor = _interpretar_opcion(texto_usuario, opciones)
    if valor is None:
        return "No reconocí esa opción.\n\n" + (wizard.get("_mensaje_menu_actual") or "")

    tipo_valor, _, resto = valor.partition(":")
    paso = wizard["paso"]
    if paso == "elegir_activo":
        if tipo_valor == "categoria":
            wizard["categoria"] = resto
        else:  # "nombre" -- un espacio, ya queda resuelto directo
            wizard["nombre_recurso"] = resto
    elif paso == "elegir_marca":
        wizard["marca"] = valor
    elif paso == "elegir_modelo":
        wizard["nombre_recurso"] = valor

    wizard["opciones_actuales"] = None
    return None


def _mover_item_resuelto_al_carrito(wizard: dict) -> str:
    """Mueve wizard['nombre_recurso'] (recién resuelto) al carrito, sin
    tocar sus accesorios por defecto todavía -- eso lo decide el usuario a
    continuación si el activo trae alguno (ver paso "confirmar_accesorios"
    más abajo, y el chequeo al tope del `while` en `_avanzar_wizard`)."""
    nombre = wizard.pop("nombre_recurso")
    carrito: list[dict] = wizard.setdefault("carrito", [])
    if not any(item["nombre_recurso"] == nombre for item in carrito):
        carrito.append({"nombre_recurso": nombre, "auto": False, "por": None})
    wizard["categoria"] = None
    wizard["marca"] = None
    wizard["resolviendo_item"] = False
    return nombre


OPCIONES_ACCESORIOS = [
    ("incluir", "Sí, inclúyelos"),
    ("excluir", "No, no los necesito"),
]


def _mensaje_confirmar_accesorios(nombre_padre: str, pendientes: list[str]) -> str:
    """Mensaje tipo factura, separado del resumen final, que confirma el
    activo recién elegido y le pregunta al usuario si quiere sus accesorios
    por defecto -- ANTES de pedir fecha/hora, no junto con esa pregunta."""
    lineas = "\n".join(f"    • {n}" for n in pendientes)
    mensaje = (
        "*ACTIVO SELECCIONADO*\n"
        f"{_DIVISOR_FACTURA}\n"
        f"*{nombre_padre}*\n"
        "Incluye por defecto:\n"
        f"{lineas}\n"
        f"{_DIVISOR_FACTURA}"
    )
    return mensaje + "\n\n" + _formatear_menu("¿Deseas incluirlos en tu reserva?", OPCIONES_ACCESORIOS)


def _iniciar_quitar_item(wizard: dict, sesion: dict) -> tuple[str, dict]:
    """Muestra el menú de "quitar un activo" con lo que hay en el carrito.
    Si el carrito se queda vacío después de quitar, el llamador debe volver
    a pedir un activo (`resolviendo_item = True`)."""
    opciones = [
        (
            item["nombre_recurso"],
            item["nombre_recurso"] + (" (incluido por defecto)" if item["auto"] else ""),
        )
        for item in wizard["carrito"]
    ]
    wizard["opciones_actuales"] = opciones
    wizard["paso"] = "quitar_item_menu"
    mensaje = _formatear_menu("¿Qué activo quieres quitar de la reserva?", opciones)
    wizard["_mensaje_menu_actual"] = mensaje
    return mensaje, {**sesion, "reserva_wizard": wizard}


OPCIONES_CONFIRMAR = [
    ("confirmar", "Confirmar"),
    ("agregar_item", "Agregar otro activo o espacio"),
    ("quitar_item", "Quitar un activo de la reserva"),
    ("cambiar_fecha", "Cambiar fecha"),
    ("cambiar_hora", "Cambiar hora"),
    ("cancelar", "Cancelar"),
]

OPCIONES_CONFIRMAR_FECHA = [
    ("si", "Sí, esa fecha está bien"),
    ("no", "No, quiero escribirla en formato dd/mm/aaaa"),
]

OPCIONES_CONFIRMAR_HORA = [
    ("si", "Sí, esa hora está bien"),
    ("no", "No, quiero escribirla en formato hh:mm am/pm"),
]


def _siguiente_paso_wizard(wizard: dict) -> str:
    if wizard.get("resolviendo_item"):
        if not wizard.get("nombre_recurso"):
            if not wizard.get("categoria"):
                return "elegir_activo"
            if not wizard.get("marca"):
                return "elegir_marca"
            return "elegir_modelo"
        # nombre_recurso ya se resolvió pero todavía no se encajó en el
        # carrito -- eso lo maneja _avanzar_wizard antes de llegar aquí
        # (ver el chequeo al tope de su while), así que no debería pasar.
    if not wizard.get("fecha"):
        return "fecha"
    if not wizard.get("inicio") or not wizard.get("fin"):
        return "hora"
    if not wizard.get("confirmado"):
        return "confirmar"
    return "listo"


def _avanzar_wizard(wizard: dict, usuario: Usuario) -> tuple[str, dict, bool]:
    """Avanza el wizard todo lo que se pueda sin pedirle nada al usuario
    (si un paso solo tiene una opción posible, ej. una sola marca en una
    categoría, se salta solo y sigue; y si un ítem recién se resolvió, se
    encaja en el carrito junto con sus accesorios por defecto) y se detiene
    en el primer paso que sí necesita una respuesta, o cuando el usuario ya
    confirmó todo (`listo=True`, momento en el que el llamador crea la
    reserva de verdad). Devuelve (mensaje_para_el_usuario, wizard_actualizado,
    listo)."""
    avisos: list[str] = []

    while True:
        if wizard.get("resolviendo_item") and wizard.get("nombre_recurso"):
            nombre = _mover_item_resuelto_al_carrito(wizard)
            ya_en_carrito = {item["nombre_recurso"] for item in wizard["carrito"]}
            pendientes = [a for a in _accesorios_por_defecto(nombre) if a not in ya_en_carrito]

            if pendientes:
                # Se detiene acá -- no se decide en silencio -- para
                # preguntarle al usuario si quiere los accesorios por
                # defecto ANTES de pedirle fecha/hora (ver
                # `_mensaje_confirmar_accesorios` / paso "confirmar_accesorios").
                wizard["accesorios_pendientes"] = pendientes
                wizard["item_padre_pendiente"] = nombre
                wizard["opciones_actuales"] = OPCIONES_ACCESORIOS
                wizard["paso"] = "confirmar_accesorios"
                mensaje = _mensaje_confirmar_accesorios(nombre, pendientes)
                wizard["_mensaje_menu_actual"] = mensaje
                return _con_avisos(avisos, mensaje), wizard, False

            continue

        paso = _siguiente_paso_wizard(wizard)

        if paso == "elegir_activo":
            opciones = _menu_paso_activo()
            if not opciones:
                return "Por ahora no tengo activos cargados en el sistema. Contacta al equipo de la CPA.", wizard, True
            wizard["opciones_actuales"] = opciones
            wizard["paso"] = "elegir_activo"
            mensaje = _formatear_menu_activo(opciones)
            wizard["_mensaje_menu_actual"] = mensaje
            return _con_avisos(avisos, mensaje), wizard, False

        if paso == "elegir_marca":
            marcas = _marcas_de_categoria(wizard["categoria"])
            if len(marcas) == 1:
                wizard["marca"] = marcas[0]
                avisos.append(f"Como solo tenemos una marca de {wizard['categoria']} ({marcas[0]}), sigo con esa.")
                continue
            opciones = [(m, m) for m in marcas]
            wizard["opciones_actuales"] = opciones
            wizard["paso"] = "elegir_marca"
            mensaje = _formatear_menu(f"¿Qué marca de {wizard['categoria']} necesitas?", opciones)
            wizard["_mensaje_menu_actual"] = mensaje
            return _con_avisos(avisos, mensaje), wizard, False

        if paso == "elegir_modelo":
            grupos = _modelos_de_marca(wizard["categoria"], wizard["marca"])
            if len(grupos) == 1:
                nombre, representante = grupos[0]
                wizard["nombre_recurso"] = nombre
                etiqueta = representante.modelo or nombre
                avisos.append(f"Solo tenemos un modelo de {wizard['marca']} en {wizard['categoria']} ({etiqueta}), sigo con ese.")
                continue
            opciones = [(nombre, representante.modelo or nombre) for nombre, representante in grupos]
            wizard["opciones_actuales"] = opciones
            wizard["paso"] = "elegir_modelo"
            mensaje = _formatear_menu(f"¿Qué modelo de {wizard['marca']} necesitas?", opciones)
            wizard["_mensaje_menu_actual"] = mensaje
            return _con_avisos(avisos, mensaje), wizard, False

        if paso == "fecha":
            ahora_local = datetime.now(timezone.utc).astimezone(ZONA_HORARIA_CPA)
            wizard["opciones_actuales"] = None
            wizard["paso"] = "fecha"
            nombres_carrito = ", ".join(f"'{item['nombre_recurso']}'" for item in wizard["carrito"])
            mensaje = (
                f"Hoy es {_DIAS_ES[ahora_local.weekday()]} {ahora_local.strftime('%d/%m/%Y')}. "
                f"¿Qué fecha necesitas {nombres_carrito}? "
                "Escríbela en formato dd/mm/aaaa, por ejemplo 22/11/2026."
            )
            return _con_avisos(avisos, mensaje), wizard, False

        if paso == "hora":
            fecha_legible = date.fromisoformat(wizard["fecha"]).strftime("%d/%m/%Y")
            wizard["opciones_actuales"] = None
            wizard["paso"] = "hora"
            mensaje = (
                f"¿De qué hora a qué hora, el {fecha_legible}? "
                "Formato hh:mm am/pm, por ejemplo: 3:00pm a 5:00pm."
            )
            return _con_avisos(avisos, mensaje), wizard, False

        if paso == "confirmar":
            wizard["opciones_actuales"] = OPCIONES_CONFIRMAR
            wizard["paso"] = "confirmar"
            mensaje = _mensaje_resumen(wizard, usuario)
            wizard["_mensaje_menu_actual"] = mensaje
            return _con_avisos(avisos, mensaje), wizard, False

        return "", wizard, True  # listo -- el usuario ya eligió "confirmar"


_DIVISOR_FACTURA = "――――――――――――――――――"


def _formatear_item_factura(nombre: str, es_hijo_auto: bool, sufijo: str = "") -> str:
    """Una línea de activo para los mensajes de resumen/confirmación, estilo
    factura: el activo que el usuario pidió va en negrita al margen; un
    accesorio que se incluyó solo por venir en bundle (`es_hijo_auto`) va
    sangrado justo debajo, sin repetir el nombre del padre -- el wizard ya
    garantiza que un padre y sus accesorios quedan contiguos en el carrito
    (ver `_mover_item_resuelto_al_carrito` / paso "confirmar_accesorios"),
    así que la sangría alcanza para que se entienda a cuál pertenecen."""
    if es_hijo_auto:
        return f"    • {nombre} _(incluido)_{sufijo}"
    return f"*{nombre}*{sufijo}"


def _mensaje_resumen(wizard: dict, usuario: Usuario) -> str:
    inicio_utc = datetime.fromisoformat(wizard["inicio"])
    fin_utc = datetime.fromisoformat(wizard["fin"])
    inicio_local = inicio_utc.astimezone(ZONA_HORARIA_CPA)
    fin_local = fin_utc.astimezone(ZONA_HORARIA_CPA)
    lineas_activos = "\n".join(
        _formatear_item_factura(item["nombre_recurso"], item["auto"]) for item in wizard["carrito"]
    )
    resumen = (
        "*RESUMEN DE RESERVA*\n"
        f"{_DIVISOR_FACTURA}\n"
        f"{lineas_activos}\n"
        f"{_DIVISOR_FACTURA}\n"
        f"Reservado por: {usuario.nombre}\n"
        f"Inicio: {_formatear_fecha_hora(inicio_local)}\n"
        f"Fin: {_formatear_fecha_hora(fin_local)}\n"
        f"{_DIVISOR_FACTURA}"
    )
    return resumen + "\n\n" + _formatear_menu("¿Qué quieres hacer?", OPCIONES_CONFIRMAR)


def _con_avisos(avisos: list[str], mensaje: str) -> str:
    if not avisos:
        return mensaje
    return "\n".join(avisos) + ("\n\n" + mensaje if mensaje else "")


OPCIONES_NO_DISPONIBLE = [
    ("quitar", "Quitar esos activos y continuar con el resto de la reserva"),
    ("cancelar_reserva", "Cancelar esta reserva y elegir otro activo"),
]


def _mensaje_no_disponibles(no_disponibles: list[str]) -> str:
    """Le dice al usuario exactamente cuáles activos (padre o hijo, da
    igual) ya no están disponibles en el horario elegido, y le pregunta qué
    hacer -- en vez de decidir en silencio (ver `_resolver_disponibilidad_carrito`
    y el paso "resolver_disponibilidad" en `_continuar_reserva`)."""
    lineas = "\n".join(f"    • {n}" for n in no_disponibles)
    verbo = "No está disponible" if len(no_disponibles) == 1 else "No están disponibles"
    mensaje = (
        "*DISPONIBILIDAD*\n"
        f"{_DIVISOR_FACTURA}\n"
        f"{verbo} en ese horario:\n"
        f"{lineas}\n"
        f"{_DIVISOR_FACTURA}"
    )
    return mensaje + "\n\n" + _formatear_menu("¿Qué quieres hacer?", OPCIONES_NO_DISPONIBLE)


def _pausar_por_no_disponibles(wizard: dict, sesion: dict, no_disponibles: list[str]) -> tuple[str, dict]:
    wizard["no_disponibles"] = no_disponibles
    wizard["opciones_actuales"] = OPCIONES_NO_DISPONIBLE
    wizard["paso"] = "resolver_disponibilidad"
    mensaje = _mensaje_no_disponibles(no_disponibles)
    wizard["_mensaje_menu_actual"] = mensaje
    return mensaje, {**sesion, "reserva_wizard": wizard}


def _crear_reserva_desde_wizard(wizard: dict, usuario: Usuario, sesion: dict) -> tuple[str, dict]:
    """Se llama solo cuando el usuario eligió "Confirmar" en el paso final
    -- aquí sí se escribe de verdad en `reservas`, todo lo anterior solo
    preparaba los datos. Todo el carrito se crea junto o no se crea nada
    (ver `crear_reservas_grupo`)."""
    inicio_utc = datetime.fromisoformat(wizard["inicio"])
    fin_utc = datetime.fromisoformat(wizard["fin"])
    ahora_utc = datetime.now(timezone.utc)

    if inicio_utc < ahora_utc - MARGEN_INICIO_PASADO:
        return (
            "Esa fecha/hora ya pasó mientras confirmabas. Intenta reservar de nuevo con una fecha futura."
        ), {**sesion, "reserva_wizard": None}

    # Última verificación antes de escribir en la base -- puede haber
    # cambiado desde que se eligió la hora (ej. otra persona reservó algo
    # justo mientras este usuario confirmaba).
    carrito_final, unidades, no_disponibles = _resolver_disponibilidad_carrito(
        wizard["carrito"], inicio_utc, fin_utc
    )

    if no_disponibles:
        return _pausar_por_no_disponibles(wizard, sesion, no_disponibles)

    sesion_limpia = {**sesion, "reserva_wizard": None}

    # Puede haber más de una unidad libre por modelo; la garantía real
    # contra choques es el EXCLUDE de Postgres que atrapa
    # crear_reservas_grupo -- esto solo evita el caso común (nadie más
    # reservó nada en el instante entre el chequeo y el commit).
    reservas, error = crear_reservas_grupo(usuario.id, unidades, inicio_utc, fin_utc)
    if error is not None:
        return (
            "Uno de los activos se acaba de reservar justo mientras confirmabas. Intenta reservar de nuevo."
        ), sesion_limpia

    inicio_local = inicio_utc.astimezone(ZONA_HORARIA_CPA)
    fin_local = fin_utc.astimezone(ZONA_HORARIA_CPA)
    lineas = "\n".join(
        _formatear_item_factura(unidad.nombre, item["auto"])
        for item, unidad in zip(carrito_final, unidades)
    )
    notas_seguro = [_nota_seguro(unidad) for unidad in unidades if unidad.requiere_seguro]
    mensaje = (
        "*RESERVA CONFIRMADA*\n"
        f"{_DIVISOR_FACTURA}\n"
        f"{lineas}\n"
        f"{_DIVISOR_FACTURA}\n"
        f"Reservado por: {usuario.nombre}\n"
        f"Inicio: {_formatear_fecha_hora(inicio_local)}\n"
        f"Fin: {_formatear_hora(fin_local)}\n"
        f"{_DIVISOR_FACTURA}"
    )
    if notas_seguro:
        mensaje += "\n" + "\n".join(notas_seguro)
    return mensaje, sesion_limpia


def _aplicar_fecha_valida(wizard: dict, usuario: Usuario, sesion: dict, fecha: date) -> tuple[str, dict]:
    """Guarda una fecha ya aceptada (venga del formato exacto o de una
    interpretación que el usuario confirmó) y avanza el wizard."""
    wizard["fecha"] = fecha.isoformat()
    wizard["opciones_actuales"] = None
    mensaje, wizard, listo = _avanzar_wizard(wizard, usuario)
    if listo:
        return _crear_reserva_desde_wizard(wizard, usuario, sesion)
    return mensaje, {**sesion, "reserva_wizard": wizard}


def _aplicar_hora_valida(
    wizard: dict, usuario: Usuario, sesion: dict, inicio_str: str, fin_str: str
) -> tuple[str, dict]:
    """Guarda un rango de hora ya aceptado (formato exacto o interpretación
    confirmada) y avanza el wizard. Si falla la validación (ya pasó, choca
    con otra reserva), regresa al paso "hora" para que el usuario reintente
    en vez de dejarlo en un paso de confirmación muerto."""
    inicio_utc, fin_utc, error = _parsear_fecha_hora(inicio_str, fin_str, datetime.now(timezone.utc))
    if error:
        wizard["paso"] = "hora"
        wizard["opciones_actuales"] = None
        return error, {**sesion, "reserva_wizard": wizard}

    wizard["inicio"] = inicio_utc.isoformat()
    wizard["fin"] = fin_utc.isoformat()
    wizard["opciones_actuales"] = None

    _, _, no_disponibles = _resolver_disponibilidad_carrito(wizard["carrito"], inicio_utc, fin_utc)
    if no_disponibles:
        # Se le dice al usuario cuál activo (padre o hijo, da igual) no
        # está disponible y se le pregunta qué hacer, en vez de bloquear
        # todo el horario o descartar accesorios en silencio (ver
        # `_pausar_por_no_disponibles`).
        return _pausar_por_no_disponibles(wizard, sesion, no_disponibles)

    mensaje, wizard, listo = _avanzar_wizard(wizard, usuario)
    if listo:
        return _crear_reserva_desde_wizard(wizard, usuario, sesion)
    return mensaje, {**sesion, "reserva_wizard": wizard}


def _iniciar_reserva(texto_usuario: str, usuario: Usuario, sesion: dict) -> tuple[str, dict]:
    """Primer mensaje de un intento de reserva. Usa el LLM solo para
    identificar el ACTIVO (si el mensaje ya lo deja claro, ej. "cámara
    sony", ese paso del wizard se salta solo -- ver _avanzar_wizard). La
    fecha y hora nunca se le piden al LLM: se preguntan siempre con los
    pasos de formato fijo (dd/mm/aaaa y hh:mm) para no arriesgarse a que
    el modelo "invente" una hora que el usuario nunca dio (ver
    PROMPT_EXTRACCION -- en la práctica no siempre respeta el "null si no
    hay información suficiente")."""
    ahora_utc = datetime.now(timezone.utc)
    ahora_local = ahora_utc.astimezone(ZONA_HORARIA_CPA)
    nombres = listar_nombres_disponibles()

    if not nombres:
        return "Por ahora no tengo activos cargados en el sistema. Contacta al equipo de la CPA.", sesion

    extraido = llm_client.extraer_datos_reserva(texto_usuario, nombres, ahora_local) or {}
    recurso, candidatos, _ = _resolver_recurso_para_reserva(texto_usuario, extraido)

    wizard: dict = {
        "categoria": None,
        "marca": None,
        "nombre_recurso": None,
        "carrito": [],
        "resolviendo_item": True,
        "fecha": None,
        "inicio": None,
        "fin": None,
        "confirmado": False,
        "opciones_actuales": None,
        "paso": None,
    }

    if recurso is not None:
        wizard["nombre_recurso"] = recurso.nombre
    elif candidatos:
        # el texto mencionaba algo genérico (ej. "una cámara") que matcheó
        # varias unidades de la misma categoría -- nos saltamos el primer
        # paso (tipo) y arrancamos directo en "qué marca"
        categorias_candidatos = {c.categoria for c in candidatos if c.categoria}
        if len(categorias_candidatos) == 1:
            wizard["categoria"] = next(iter(categorias_candidatos))

    mensaje, wizard, listo = _avanzar_wizard(wizard, usuario)
    if listo:
        return _crear_reserva_desde_wizard(wizard, usuario, sesion)
    return mensaje, {**sesion, "reserva_wizard": wizard}


def _continuar_reserva(texto_usuario: str, usuario: Usuario, sesion: dict, wizard: dict) -> tuple[str, dict]:
    """Siguiente mensaje de un intento de reserva ya en curso -- se
    interpreta según el paso en el que quedó el wizard, nunca como un
    mensaje nuevo y suelto."""
    paso = wizard.get("paso")

    if paso in ("elegir_activo", "elegir_marca", "elegir_modelo"):
        error = _aplicar_respuesta_menu(wizard, texto_usuario)
        if error:
            return error, {**sesion, "reserva_wizard": wizard}
        mensaje, wizard, listo = _avanzar_wizard(wizard, usuario)
        if listo:
            return _crear_reserva_desde_wizard(wizard, usuario, sesion)
        return mensaje, {**sesion, "reserva_wizard": wizard}

    if paso == "fecha":
        ahora_local = datetime.now(timezone.utc).astimezone(ZONA_HORARIA_CPA)
        texto = texto_usuario.strip()

        fecha, error = _parsear_fecha_dd_mm_aaaa(texto, ahora_local)
        if fecha is not None:
            return _aplicar_fecha_valida(wizard, usuario, sesion, fecha)

        # No vino en dd/mm/aaaa -- se intenta reconocer en lenguaje natural
        # ("mañana", "el viernes", "22 de noviembre"), pero nunca se acepta
        # directo: siempre se le muestra al usuario qué fecha entendió el
        # bot y se le pide confirmarla antes de seguir (ver
        # `_interpretar_fecha_natural`).
        fecha_interpretada = _interpretar_fecha_natural(texto, ahora_local)
        if fecha_interpretada is not None:
            if fecha_interpretada < ahora_local.date():
                return (
                    "Esa fecha ya pasó. Dime una fecha de hoy en adelante, en formato dd/mm/aaaa."
                ), {**sesion, "reserva_wizard": wizard}
            fecha_legible = f"{_DIAS_ES[fecha_interpretada.weekday()]} {fecha_interpretada.strftime('%d/%m/%Y')}"
            wizard["fecha_pendiente"] = fecha_interpretada.isoformat()
            wizard["paso"] = "confirmar_fecha_interpretada"
            wizard["opciones_actuales"] = OPCIONES_CONFIRMAR_FECHA
            mensaje = _formatear_menu(f"Entendí {fecha_legible}. ¿Es correcto?", OPCIONES_CONFIRMAR_FECHA)
            wizard["_mensaje_menu_actual"] = mensaje
            return mensaje, {**sesion, "reserva_wizard": wizard}

        return error, {**sesion, "reserva_wizard": wizard}

    if paso == "confirmar_fecha_interpretada":
        opciones = wizard.get("opciones_actuales") or OPCIONES_CONFIRMAR_FECHA
        valor = _interpretar_opcion(texto_usuario, opciones)
        if valor is None:
            return "No reconocí esa opción.\n\n" + (wizard.get("_mensaje_menu_actual") or ""), {
                **sesion,
                "reserva_wizard": wizard,
            }
        if valor == "no":
            wizard["fecha_pendiente"] = None
            wizard["paso"] = "fecha"
            wizard["opciones_actuales"] = None
            return (
                "Está bien, escribe la fecha en formato dd/mm/aaaa, por ejemplo 22/11/2026."
            ), {**sesion, "reserva_wizard": wizard}
        fecha = date.fromisoformat(wizard.pop("fecha_pendiente"))
        return _aplicar_fecha_valida(wizard, usuario, sesion, fecha)

    if paso == "hora":
        texto = texto_usuario.strip()
        fecha = wizard["fecha"]

        rango = _parsear_rango_horario(texto)
        if rango is not None:
            (h1, m1), (h2, m2) = rango
            inicio_str = f"{fecha} {h1:02d}:{m1:02d}"
            fin_str = f"{fecha} {h2:02d}:{m2:02d}"
            return _aplicar_hora_valida(wizard, usuario, sesion, inicio_str, fin_str)

        # Igual que con la fecha: si no vino en el formato exacto pero es
        # reconocible con variaciones comunes (ej. "de 2pm a 7pm"), se
        # interpreta pero se le pide confirmar antes de aceptarla.
        rango_relajado = _interpretar_hora_relajada(texto)
        if rango_relajado is not None:
            (h1, m1), (h2, m2) = rango_relajado
            inicio_str = f"{fecha} {h1:02d}:{m1:02d}"
            fin_str = f"{fecha} {h2:02d}:{m2:02d}"
            inicio_legible = _formatear_hora(
                datetime.strptime(inicio_str, "%Y-%m-%d %H:%M").replace(tzinfo=ZONA_HORARIA_CPA)
            )
            fin_legible = _formatear_hora(
                datetime.strptime(fin_str, "%Y-%m-%d %H:%M").replace(tzinfo=ZONA_HORARIA_CPA)
            )
            wizard["hora_pendiente_inicio"] = inicio_str
            wizard["hora_pendiente_fin"] = fin_str
            wizard["paso"] = "confirmar_hora_interpretada"
            wizard["opciones_actuales"] = OPCIONES_CONFIRMAR_HORA
            mensaje = _formatear_menu(
                f"Entendí de {inicio_legible} a {fin_legible} ¿Es correcto?", OPCIONES_CONFIRMAR_HORA
            )
            wizard["_mensaje_menu_actual"] = mensaje
            return mensaje, {**sesion, "reserva_wizard": wizard}

        return (
            "No reconocí ese formato de hora. Escribe algo como '3:00pm a 5:00pm' o '15:00 a 17:00'."
        ), {**sesion, "reserva_wizard": wizard}

    if paso == "confirmar_hora_interpretada":
        opciones = wizard.get("opciones_actuales") or OPCIONES_CONFIRMAR_HORA
        valor = _interpretar_opcion(texto_usuario, opciones)
        if valor is None:
            return "No reconocí esa opción.\n\n" + (wizard.get("_mensaje_menu_actual") or ""), {
                **sesion,
                "reserva_wizard": wizard,
            }
        if valor == "no":
            wizard["hora_pendiente_inicio"] = None
            wizard["hora_pendiente_fin"] = None
            wizard["paso"] = "hora"
            wizard["opciones_actuales"] = None
            return (
                "Está bien, escribe la hora en formato hh:mm am/pm, por ejemplo 3:00pm a 5:00pm."
            ), {**sesion, "reserva_wizard": wizard}
        inicio_str = wizard.pop("hora_pendiente_inicio")
        fin_str = wizard.pop("hora_pendiente_fin")
        return _aplicar_hora_valida(wizard, usuario, sesion, inicio_str, fin_str)

    if paso == "confirmar":
        opciones = wizard.get("opciones_actuales") or OPCIONES_CONFIRMAR
        valor = _interpretar_opcion(texto_usuario, opciones)
        if valor is None:
            return "No reconocí esa opción.\n\n" + (wizard.get("_mensaje_menu_actual") or ""), {
                **sesion,
                "reserva_wizard": wizard,
            }

        if valor == "cancelar":
            return (
                "Reserva cancelada, no se guardó nada. Si quieres reservar otra cosa, dime qué necesitas."
            ), {**sesion, "reserva_wizard": None}

        if valor == "confirmar":
            wizard["confirmado"] = True
            return _crear_reserva_desde_wizard(wizard, usuario, sesion)

        if valor == "agregar_item":
            wizard.update(categoria=None, marca=None, nombre_recurso=None, resolviendo_item=True)
        elif valor == "quitar_item":
            return _iniciar_quitar_item(wizard, sesion)
        elif valor == "cambiar_fecha":
            wizard.update(fecha=None, inicio=None, fin=None)
        elif valor == "cambiar_hora":
            wizard.update(inicio=None, fin=None)

        mensaje, wizard, listo = _avanzar_wizard(wizard, usuario)
        if listo:
            return _crear_reserva_desde_wizard(wizard, usuario, sesion)
        return mensaje, {**sesion, "reserva_wizard": wizard}

    if paso == "confirmar_accesorios":
        opciones = wizard.get("opciones_actuales") or OPCIONES_ACCESORIOS
        valor = _interpretar_opcion(texto_usuario, opciones)
        if valor is None:
            return "No reconocí esa opción.\n\n" + (wizard.get("_mensaje_menu_actual") or ""), {
                **sesion,
                "reserva_wizard": wizard,
            }

        pendientes = wizard.pop("accesorios_pendientes", [])
        nombre_padre = wizard.pop("item_padre_pendiente", None)
        if valor == "incluir":
            for nombre_accesorio in pendientes:
                wizard["carrito"].append({"nombre_recurso": nombre_accesorio, "auto": True, "por": nombre_padre})
            # No hace falta revisar disponibilidad acá -- ya se revisa en el
            # paso "hora" (o al confirmar, si la hora ya estaba puesta por
            # venir de "agregar otro activo"), ver `_pausar_por_no_disponibles`.

        wizard["opciones_actuales"] = None
        mensaje, wizard, listo = _avanzar_wizard(wizard, usuario)
        if listo:
            return _crear_reserva_desde_wizard(wizard, usuario, sesion)
        return mensaje, {**sesion, "reserva_wizard": wizard}

    if paso == "resolver_disponibilidad":
        opciones = wizard.get("opciones_actuales") or OPCIONES_NO_DISPONIBLE
        valor = _interpretar_opcion(texto_usuario, opciones)
        if valor is None:
            return "No reconocí esa opción.\n\n" + (wizard.get("_mensaje_menu_actual") or ""), {
                **sesion,
                "reserva_wizard": wizard,
            }

        no_disponibles = wizard.pop("no_disponibles", [])

        if valor == "cancelar_reserva":
            return (
                "Reserva cancelada, no se guardó nada. Si quieres reservar otra cosa, dime qué necesitas."
            ), {**sesion, "reserva_wizard": None}

        # "quitar" -- se quitan los activos no disponibles y, si alguno era
        # padre de un accesorio incluido por defecto, también ese accesorio
        # (mismo criterio de arrastre que "Quitar un activo de la reserva",
        # ver paso "quitar_item_menu").
        wizard["carrito"] = [
            item
            for item in wizard["carrito"]
            if item["nombre_recurso"] not in no_disponibles and item.get("por") not in no_disponibles
        ]
        if not wizard["carrito"]:
            wizard["resolviendo_item"] = True

        wizard["opciones_actuales"] = None
        mensaje, wizard, listo = _avanzar_wizard(wizard, usuario)
        if listo:
            return _crear_reserva_desde_wizard(wizard, usuario, sesion)
        return mensaje, {**sesion, "reserva_wizard": wizard}

    if paso == "quitar_item_menu":
        opciones = wizard.get("opciones_actuales") or []
        valor = _interpretar_opcion(texto_usuario, opciones)
        if valor is None:
            return "No reconocí esa opción.\n\n" + (wizard.get("_mensaje_menu_actual") or ""), {
                **sesion,
                "reserva_wizard": wizard,
            }

        nombre_a_quitar = valor
        wizard["carrito"] = [
            item
            for item in wizard["carrito"]
            if item["nombre_recurso"] != nombre_a_quitar and item.get("por") != nombre_a_quitar
        ]
        if not wizard["carrito"]:
            # Se quitó el único activo -- hay que elegir otro para poder
            # seguir con la reserva.
            wizard["resolviendo_item"] = True

        mensaje, wizard, listo = _avanzar_wizard(wizard, usuario)
        if listo:
            return _crear_reserva_desde_wizard(wizard, usuario, sesion)
        return mensaje, {**sesion, "reserva_wizard": wizard}

    # Estado inesperado (no debería pasar) -- reinicia el wizard en vez de
    # dejar al usuario sin salida.
    return _iniciar_reserva(texto_usuario, usuario, {**sesion, "reserva_wizard": None})


def gestionar_reserva(texto_usuario: str, usuario: Usuario, sesion: dict) -> tuple[str, dict]:
    """Punto de entrada para el intent 'reservar'. Arranca o continúa el
    wizard paso a paso (ver bloque de arriba), incluyendo la confirmación
    final -- ya no existe un mecanismo aparte de "reserva_pendiente" con
    solo sí/no, el último paso del wizard ("confirmar") es un menú tipo
    carrito de compras que también permite agregar o quitar activos, o
    cambiar la fecha/hora, sin reiniciar todo. Mientras `sesion` tenga
    `reserva_wizard` distinto de None,
    webhook.py manda la respuesta del usuario directo aquí en vez de pasar
    por clasificación de intent (una respuesta corta como "2" no se
    reconoce de forma confiable como intent "reservar" y quedaría
    bloqueada por la whitelist)."""
    wizard = sesion.get("reserva_wizard")

    # En los pasos de "¿es correcto lo que entendí?" (fecha/hora
    # interpretadas), "no" significa "esa interpretación está mal, deja que
    # te la escriba en el formato exacto" -- no "cancela toda la reserva".
    # Ahí solo las palabras explícitas de cancelar/cerrar deben cortar el
    # flujo. "cerrar" es sinónimo de "cancelar" pensado para el caso de "ya
    # no quiero seguir, corta esto ya" en cualquier paso del wizard -- este
    # chequeo corre antes de despachar al paso actual, así que funciona sin
    # importar en qué parte del flujo esté el usuario. Solo se borra
    # `reserva_wizard` de la sesión (nada se reserva hasta el paso
    # "confirmar", así que no queda ningún activo/espacio a medio reservar);
    # el historial de la interacción (`guardrails.registrar_interaccion`)
    # no se toca.
    # Mismo motivo para "confirmar_accesorios": su opción "No, no los
    # necesito" también empieza por "no", que ahí significa "excluye esos
    # accesorios", no "cancela toda la reserva".
    palabras_cancelar = PALABRAS_CANCELAR
    if wizard is not None and wizard.get("paso") in (
        "confirmar_fecha_interpretada",
        "confirmar_hora_interpretada",
        "confirmar_accesorios",
    ):
        palabras_cancelar = {"cancelar", "cancela", "cerrar"}

    if wizard is not None and set(_normalizar(texto_usuario).split()) & palabras_cancelar:
        return (
            "Reserva cancelada, no se guardó nada. Si quieres reservar otra cosa, dime qué necesitas."
        ), {**sesion, "reserva_wizard": None}

    if wizard is None:
        return _iniciar_reserva(texto_usuario, usuario, sesion)
    return _continuar_reserva(texto_usuario, usuario, sesion, wizard)
