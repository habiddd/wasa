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
# Único estado de `recursos.estado` que se puede reservar -- 'en_mantenimiento'
# y 'no_disponible' quedan fuera del catálogo que ve el bot, igual que antes
# se hacía con `activo=False` (ver models.py y postgres/init/02-schema.sql).
ESTADO_DISPONIBLE = "disponible"
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
    confiarlo a ciegas (ver `_resolver_recurso_para_reserva`).

    Los matches son por palabra completa (`\\b`), no substring suelto: sin
    eso, "Cámara Canon EOS R" -- que es un prefijo literal de "Cámara Canon
    EOS R6" -- puntuaría igual de alto contra un mensaje que en realidad
    dice "...eos r6", empatando dos modelos que el texto sí distingue."""
    nombre_norm = _normalizar(nombre)
    palabras = [p for p in nombre_norm.split() if len(p) >= LONGITUD_MINIMA_PALABRA_CLAVE]
    score = sum(1 for p in palabras if re.search(rf"\b{re.escape(p)}\b", texto_norm))
    if re.search(rf"\b{re.escape(nombre_norm)}\b", texto_norm):
        score += 10
    return score


def _grupos_activos() -> list[tuple[str, Recurso]]:
    """(nombre, unidad_representante) por cada modelo distinto entre los
    recursos activos, ordenados por tipo y nombre. Varias filas de
    `recursos` pueden compartir nombre/marca/modelo (una por placa) --
    esto colapsa esas filas a una sola entrada por modelo."""
    recursos = Recurso.query.filter_by(estado=ESTADO_DISPONIBLE).order_by(Recurso.tipo, Recurso.nombre, Recurso.id).all()
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
    return Recurso.query.filter_by(estado=ESTADO_DISPONIBLE, nombre=nombre).order_by(Recurso.id).all()


def _accesorios_por_defecto(nombre_padre: str) -> list[str]:
    """Nombres (modelo) de los accesorios que se agregan automáticamente al
    reservar `nombre_padre` -- todo hijo activo (de cualquier unidad del
    grupo, no de una placa específica: el wizard ya trata todo por modelo y
    solo baja a una unidad concreta al crear la reserva, ver
    `_unidad_libre_en_rango`) va incluido por defecto; el usuario lo quita de
    la selección si no lo quiere (ver paso "confirmar_accesorios" en
    `_avanzar_wizard`). Deduplicado por nombre."""
    ids_padres = [u.id for u in _grupo(nombre_padre)]
    if not ids_padres:
        return []
    hijos = (
        Recurso.query.filter_by(estado=ESTADO_DISPONIBLE)
        .filter(Recurso.padre_id.in_(ids_padres))
        .order_by(Recurso.id)
        .all()
    )
    vistos: list[str] = []
    for h in hijos:
        if h.nombre not in vistos:
            vistos.append(h.nombre)
    return vistos


def _contenidos_de_espacio(nombre_espacio: str) -> list[str]:
    """Nombres (modelo) de los equipos activos que viven dentro de un lugar
    (`recursos.espacio_id`) -- jerarquía DISTINTA e independiente de los
    accesorios por `padre_id` (ver `_accesorios_por_defecto` arriba y el
    comentario sobre `espacio_id` en `models.Recurso`).

    ASIMETRÍA A PROPÓSITO respecto a los accesorios: estos NO se agregan
    solos al carrito, solo se muestran (ver paso "elegir_contenidos_espacio"
    en `_avanzar_wizard`/`_continuar_reserva`), y el usuario elige cuáles
    sumar -- por defecto no se suma ninguno. Motivo: reservar un lugar (ej.
    "Sala de postproducción 4") no puede reservar de una sus 5 computadores,
    porque dejaría sin máquinas a cualquier otra persona que también los
    necesite (agotar las unidades de un modelo hace que el siguiente
    usuario sea rechazado). Si el día de mañana se quiere invertir esto a
    opt-out (incluidos por defecto, se quitan los que sobren), el cambio es
    en el fold-in de `_avanzar_wizard`: agregarlos al carrito de una con
    `auto=True` igual que ya se hace con `_accesorios_por_defecto`, y reusar
    `_mensaje_confirmar_accesorios` en vez de un mensaje propio.

    Solo cuenta como RAÍZ un activo cuyo padre (por `padre_id`) NO esté
    también en este mismo espacio -- si el padre sí está, el hijo no se
    lista suelto porque ya aparece anidado bajo su padre (ver
    `_mensaje_elegir_contenidos_espacio`, que a su vez reusa
    `_accesorios_por_defecto` para esos hijos). Si el padre no tiene lugar
    o vive en OTRO lugar, el hijo SÍ cuenta como raíz de este espacio. En la
    práctica esto casi no debería pasar porque mover de lugar a un padre
    arrastra a sus hijos (ver "Regla de propagación" en
    docs/ARQUITECTURA.md), pero un hijo reubicado a mano queda cubierto
    igual.

    Excluye espacios anidados dentro de este espacio (ej. un edificio que
    contiene salas) -- por ahora esta función solo ofrece equipos
    (`tipo='equipo'`).

    Deduplicado por nombre, igual que `_grupos_activos`/`_accesorios_por_defecto`:
    varias unidades del mismo modelo en el mismo lugar (ej. 3 iMac) cuentan
    como una sola opción."""
    ids_espacio = [u.id for u in _grupo(nombre_espacio)]
    if not ids_espacio:
        return []
    contenidos = (
        Recurso.query.filter_by(estado=ESTADO_DISPONIBLE, tipo="equipo")
        .filter(Recurso.espacio_id.in_(ids_espacio))
        .order_by(Recurso.id)
        .all()
    )
    ids_contenidos = {r.id for r in contenidos}
    vistos: list[str] = []
    for r in contenidos:
        if r.padre_id in ids_contenidos:
            continue  # es hijo de otro activo de este mismo espacio -- ya aparece anidado bajo ese padre, no es raíz
        if r.nombre not in vistos:
            vistos.append(r.nombre)
    return vistos


def listar_nombres_disponibles() -> list[str]:
    return [nombre for nombre, _ in _grupos_activos()]


def listar_terminos_catalogo() -> list[str]:
    """Vocabulario real del catálogo: nombres de modelo, subcategorías,
    categorías y marcas de los activos disponibles. `routes/webhook.py` se
    lo pasa a `llm_client.clasificar_intent` como señal de dominio -- así
    el bot reconoce un pedido que nombra un equipo sin usar ningún verbo de
    reserva ("¿y el trípode Manfrotto?").

    Vive aquí y no en `llm_client` porque este módulo ya importa
    `llm_client`: al revés sería un import circular. Deduplicado
    conservando el orden, para que el resultado sea estable entre llamadas
    y fácil de leer en un log."""
    terminos: list[str] = []
    for nombre, representante in _grupos_activos():
        for valor in (
            nombre,
            representante.subcategoria,
            representante.categoria,
            representante.marca,
        ):
            if valor and valor not in terminos:
                terminos.append(valor)
    return terminos


def _subcategorias_activas() -> list[str]:
    """Subcategorías distintas entre los equipos activos (ej. 'camara',
    'celular', 'dron'), en el orden en que aparecen al agrupar por modelo.
    Permite resolver "qué cámaras tenemos" aunque "cámaras" no coincida
    con ningún nombre de modelo -- ver `_detectar_subcategoria_en_texto`."""
    vistas: list[str] = []
    for _, representante in _grupos_activos():
        cat = representante.subcategoria
        if cat and cat not in vistas:
            vistas.append(cat)
    return vistas


def _grupos_por_subcategoria(subcategoria: str) -> list[tuple[str, Recurso]]:
    """(nombre, representante) de cada modelo distinto dentro de una
    subcategoría (comparación normalizada, sin tildes/mayúsculas)."""
    cat_norm = _normalizar(subcategoria)
    return [
        (nombre, representante)
        for nombre, representante in _grupos_activos()
        if representante.subcategoria and _normalizar(representante.subcategoria) == cat_norm
    ]


def _detectar_subcategoria_en_texto(texto: str) -> str | None:
    """Busca si el texto del usuario menciona una subcategoría conocida (ej.
    "cámaras", "celulares") aunque no mencione ningún modelo o marca
    específicos. La comparación por substring cubre el plural regular en
    español ("camara" está contenido en "camaras") sin necesitar una
    lista de sinónimos aparte."""
    texto_norm = _normalizar(texto)
    for subcategoria in _subcategorias_activas():
        if _normalizar(subcategoria) in texto_norm:
            return subcategoria
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
    # `Reserva.inicio`/`.fin` vienen de una columna DateTime(timezone=True):
    # SQLAlchemy las devuelve en UTC, así que hay que pasarlas a hora de
    # Bogotá antes de formatear -- si no, se le muestra al usuario la hora
    # cruda de la base (ej. 19:00 UTC en vez de 2:00 p.m. Colombia).
    en_curso = _reserva_en_curso(recurso.id, ahora)
    if en_curso:
        fin_local = en_curso.fin.astimezone(ZONA_HORARIA_CPA)
        return f"en uso ahora mismo, libre a las {_formatear_hora(fin_local)}"

    proxima = _proxima_reserva(recurso.id, ahora)
    if proxima:
        inicio_local = proxima.inicio.astimezone(ZONA_HORARIA_CPA)
        fin_local = proxima.fin.astimezone(ZONA_HORARIA_CPA)
        inicio = f"{inicio_local.strftime('%d/%m')} {_formatear_hora(inicio_local)}"
        fin = _formatear_hora(fin_local)
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

    # Igual que en `_estado_recurso`: `.fin` viene de la base en UTC, se
    # convierte a hora de Bogotá antes de mostrarla.
    fin_mas_cercano = min(_reserva_en_curso(u.id, ahora).fin for u in unidades)
    fin_local = fin_mas_cercano.astimezone(ZONA_HORARIA_CPA)
    return (
        f"las {len(unidades)} unidades están en uso ahora mismo, "
        f"la primera se libera a las {_formatear_hora(fin_local)}"
    )


def evaluar_consulta_disponibilidad(texto_usuario: str) -> str:
    """Punto de entrada para el intent 'consultar_disponibilidad'. A
    diferencia de reservar, aquí el usuario quiere saber qué hay y qué no
    -- así que ante varias coincidencias (o ninguna) se listan todas con
    su estado, en vez de preguntar cuál de todas necesita."""
    ahora = datetime.now(timezone.utc)
    candidatos, es_match_debil = buscar_recursos_en_texto(texto_usuario)

    if not candidatos:
        subcategoria = _detectar_subcategoria_en_texto(texto_usuario)
        if subcategoria:
            grupos_subcategoria = _grupos_por_subcategoria(subcategoria)
            if grupos_subcategoria:
                lineas = "\n".join(
                    f"- {_describir_modelo(nombre, rep)}: {_estado_grupo(nombre, ahora)}"
                    for nombre, rep in grupos_subcategoria
                )
                return f"Esto es lo que tenemos en '{subcategoria}':\n{lineas}"

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
    # Solo tiene sentido pedirle una respuesta al LLM cuando el matcheo por
    # palabra clave no encontró NADA -- si ya hay uno o varios candidatos,
    # mandan ellos. Antes, si había más de un candidato (empate por
    # palabra clave) se dejaba que el LLM "desempatara" solo -- eso
    # causaba que "cámara canon" (empata entre EOS R y EOS R6, ninguna
    # palabra del mensaje distingue cuál) devolviera un modelo inventado
    # en vez de preguntarle al usuario cuál quería, porque el LLM no tenía
    # de dónde sacar esa información y aun así "elegía" uno.
    if nombre_llm and not candidatos:
        candidato_llm = next(
            (r for r in Recurso.query.filter_by(estado=ESTADO_DISPONIBLE).all() if r.nombre.strip().lower() == nombre_llm),
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

    if recurso_llm:
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
_PATRON_RANGO_FECHA = re.compile(
    r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*a\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$", re.IGNORECASE
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


def _parsear_rango_fecha_dd_mm_aaaa(
    texto: str, ahora_local: datetime
) -> tuple[date | None, date | None, str | None]:
    """Exige el formato "dd/mm/aaaa a dd/mm/aaaa" para una reserva de varios
    días (ej. "22/11/2026 a 25/11/2026") -- mismo criterio que
    `_parsear_fecha_dd_mm_aaaa`: nada de lenguaje natural para un rango,
    determinista. Devuelve (fecha_inicio, fecha_fin, None) o
    (None, None, mensaje_de_error). (None, None, None) si el texto ni
    siquiera matchea el patrón de rango (no es un error -- el llamador debe
    intentar con una fecha única en ese caso)."""
    m = _PATRON_RANGO_FECHA.match(texto)
    if not m:
        return None, None, None

    d1, m1, a1, d2, m2, a2 = (int(g) for g in m.groups())
    try:
        fecha_inicio = date(a1, m1, d1)
        fecha_fin = date(a2, m2, d2)
    except ValueError:
        return None, None, "Esa fecha no existe. Escríbela como dd/mm/aaaa, por ejemplo 22/11/2026."

    if fecha_inicio < ahora_local.date():
        return None, None, "Esa fecha ya pasó. Dime una fecha de hoy en adelante, en formato dd/mm/aaaa."

    if fecha_fin < fecha_inicio:
        return None, None, "La fecha de fin debe ser igual o después de la fecha de inicio. ¿Puedes revisar el rango?"

    return fecha_inicio, fecha_fin, None


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


_PATRONES_RANGO_FECHA_NATURAL = [
    re.compile(r"^desde\s+(.+?)\s+hasta\s+(.+)$"),
    re.compile(r"^entre\s+(.+?)\s+y\s+(.+)$"),
    re.compile(r"^del\s+(.+?)\s+al\s+(.+)$"),
    re.compile(r"^de\s+(.+?)\s+a\s+(.+)$"),
]


def _interpretar_rango_fecha_natural(texto: str, ahora_local: datetime) -> tuple[date, date] | None:
    """Segundo intento para un RANGO de fechas cuando no vino en formato
    "dd/mm/aaaa a dd/mm/aaaa" (ej. "desde mañana hasta el viernes", "del
    lunes al viernes", "entre el 3 y el 5 de octubre"). Reconoce el
    conector y le pasa cada lado a `_parsear_fecha_dd_mm_aaaa` /
    `_interpretar_fecha_natural` -- si cualquiera de los dos lados no se
    reconoce, no es un rango válido y se devuelve None (el llamador prueba
    otra cosa, ej. una fecha única suelta). Antes de este intento, una
    fecha única con "el viernes" hacía que la frase completa se
    interpretara como esa sola fecha, ignorando el "desde mañana" -- por
    eso este chequeo va primero. Igual que `_interpretar_fecha_natural`,
    es una interpretación: quien llama debe pedir confirmación antes de
    aceptarla (ver `_continuar_reserva`, paso "fecha")."""
    texto_norm = _normalizar(texto.strip())

    for patron in _PATRONES_RANGO_FECHA_NATURAL:
        m = patron.match(texto_norm)
        if not m:
            continue
        parte_inicio, parte_fin = m.group(1).strip(), m.group(2).strip()

        fecha_inicio, _ = _parsear_fecha_dd_mm_aaaa(parte_inicio, ahora_local)
        if fecha_inicio is None:
            fecha_inicio = _interpretar_fecha_natural(parte_inicio, ahora_local)

        fecha_fin, _ = _parsear_fecha_dd_mm_aaaa(parte_fin, ahora_local)
        if fecha_fin is None:
            fecha_fin = _interpretar_fecha_natural(parte_fin, ahora_local)

        if fecha_inicio is not None and fecha_fin is not None:
            return fecha_inicio, fecha_fin

    return None


def _normalizar_ampm(valor: str | None) -> str | None:
    if not valor:
        return None
    return "pm" if valor.lower().replace(".", "").replace(" ", "") == "pm" else "am"


def _hora_a_24h(hora: int, periodo: str | None) -> int | None:
    if periodo is None:
        return hora if 0 <= hora <= 23 else None  # ya viene en 24h
    if not (1 <= hora <= 12):
        return None
    hora = 0 if hora == 12 else hora
    return hora + 12 if periodo == "pm" else hora


_PATRON_HORA_INDIVIDUAL = re.compile(
    r"^\s*(\d{1,2})(?::(\d{2}))?\s*(a\.?\s*m\.?|p\.?\s*m\.?)?\s*$", re.IGNORECASE
)


def _parsear_hora_individual(texto: str) -> tuple[int, int] | None:
    """Interpreta UNA sola hora (no un rango) -- ej. '3:00pm', '3pm',
    '15:00'. Se pide la hora de inicio y la de fin en mensajes separados
    (ver pasos "hora_inicio"/"hora_fin"), no un rango en un solo mensaje,
    para no confundir al usuario con dos datos a la vez. Devuelve
    (hora, minuto) en 24h, o None si el texto no matchea ese patrón."""
    m = _PATRON_HORA_INDIVIDUAL.match(texto)
    if not m:
        return None
    h, mnt, ampm = m.groups()
    h, mnt = int(h), int(mnt or 0)
    if not (0 <= mnt <= 59):
        return None
    h_24 = _hora_a_24h(h, _normalizar_ampm(ampm))
    return (h_24, mnt) if h_24 is not None else None


def _interpretar_hora_individual_relajada(texto: str) -> tuple[int, int] | None:
    """Segundo intento cuando la hora no vino en el formato exacto pero es
    reconocible con variaciones comunes (ej. "a las 3pm", "las 15 horas").
    Igual que `_interpretar_fecha_natural`, es una interpretación -- el
    llamador debe confirmarla con el usuario antes de darla por buena."""
    texto_limpio = re.sub(r"^\s*a\s+las\s+", "", texto, flags=re.IGNORECASE)
    texto_limpio = re.sub(r"^\s*(a|las)\s+", "", texto_limpio, flags=re.IGNORECASE)
    texto_limpio = re.sub(r"\bhrs?\.?\b|\bhoras?\b", "", texto_limpio, flags=re.IGNORECASE)
    texto_limpio = re.sub(r"\s+", " ", texto_limpio).strip()
    return _parsear_hora_individual(texto_limpio)


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
    """Opciones del primer paso: una por cada subcategoría de equipo (ej.
    'camara') y una por cada espacio individual (un espacio no tiene
    subcategoría/marca/modelo, así que se resuelve directo en este paso)."""
    opciones = []
    for subcategoria in _subcategorias_activas():
        opciones.append((f"subcategoria:{subcategoria}", subcategoria.capitalize()))
    for nombre, representante in _grupos_activos():
        if representante.tipo == "espacio":
            opciones.append((f"nombre:{nombre}", nombre))
    return opciones


def _formatear_menu_activo(opciones: list[tuple[str, str]]) -> str:
    equipos = [(i, etiqueta) for i, (valor, etiqueta) in enumerate(opciones, start=1) if valor.startswith("subcategoria:")]
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


def _marcas_de_subcategoria(subcategoria: str) -> list[str]:
    vistas: list[str] = []
    for _, representante in _grupos_por_subcategoria(subcategoria):
        if representante.marca and representante.marca not in vistas:
            vistas.append(representante.marca)
    return vistas


def _modelos_de_marca(subcategoria: str, marca: str) -> list[tuple[str, Recurso]]:
    return [
        (nombre, representante)
        for nombre, representante in _grupos_por_subcategoria(subcategoria)
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
        if tipo_valor == "subcategoria":
            wizard["subcategoria"] = resto
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
    wizard["subcategoria"] = None
    wizard["marca"] = None
    wizard["resolviendo_item"] = False
    return nombre


def _mensaje_confirmar_accesorios(nombre_padre: str, incluidos: list[str]) -> str:
    """Mensaje tipo factura, separado del resumen final, que confirma el
    activo recién elegido y sus accesorios por defecto -- que YA quedaron
    incluidos en el carrito, no hace falta pedirlo (ver el fold-in en
    `_avanzar_wizard`) -- y solo le pregunta al usuario si quiere QUITAR
    alguno, uno o varios, no una decisión de todo o nada. ANTES de pedir
    fecha/hora, no junto con esa pregunta (ver `_interpretar_seleccion_multiple`)."""
    lineas = "\n".join(f"{i}. {n}" for i, n in enumerate(incluidos, start=1))
    numero_ninguno = len(incluidos) + 1
    mensaje = (
        "*ACTIVO SELECCIONADO*\n"
        f"{_DIVISOR_FACTURA}\n"
        f"*{nombre_padre}*\n"
        "Incluye por defecto:\n"
        f"{lineas}\n"
        f"{numero_ninguno}. Ninguno (dejar todos incluidos)\n"
        f"{_DIVISOR_FACTURA}\n"
        "¿Quieres quitar alguno? Responde con los números separados por "
        f"coma (ej. \"1,2\"), o {numero_ninguno} para no quitar ninguno."
    )
    return mensaje


def _mensaje_elegir_contenidos_espacio(nombre_espacio: str, raices: list[str]) -> str:
    """Análogo a `_mensaje_confirmar_accesorios` pero para lo que contiene
    un LUGAR (`_contenidos_de_espacio`), no para accesorios en bundle. Dos
    diferencias clave con ese mensaje:
    - Muestra el anidado: cada activo raíz y, debajo e indentado, sus
      propios hijos por `padre_id` (reusando `_accesorios_por_defecto`) --
      así el usuario ve qué se lleva si elige ese activo (ej. la Cámara
      con sus 3 lentes debajo). Solo las raíces se numeran/eligen; los
      hijos van pegados a su padre, no son una opción aparte.
    - Nada viene incluido por defecto (ver la asimetría explicada en
      `_contenidos_de_espacio`): la pregunta es qué AGREGAR, no qué quitar."""
    lineas: list[str] = []
    for i, nombre in enumerate(raices, start=1):
        lineas.append(f"{i}. {nombre}")
        for hijo in _accesorios_por_defecto(nombre):
            lineas.append(f"    • {hijo}")
    numero_ninguno = len(raices) + 1
    lineas.append(f"{numero_ninguno}. Ninguno (solo reservar el lugar)")
    mensaje = (
        "*ESTE LUGAR INCLUYE*\n"
        f"{_DIVISOR_FACTURA}\n"
        f"*{nombre_espacio}*\n"
        "Contiene:\n"
        f"{chr(10).join(lineas)}\n"
        f"{_DIVISOR_FACTURA}\n"
        "¿Quieres agregar alguno a la misma reserva? Responde con los "
        f"números separados por coma (ej. \"1,2\"), o {numero_ninguno} si "
        "no quieres agregar ninguno."
    )
    return mensaje


def _interpretar_seleccion_multiple(texto: str, cantidad: int) -> list[int] | None:
    """Interpreta la respuesta a un menú donde se puede elegir más de una
    opción a la vez por número (ej. qué accesorios por defecto quitar):
    "1", "2", "1,2", "1 y 2" -- devuelve los índices (0-based) elegidos.
    El número (cantidad + 1) es siempre la última línea del menú ("Ninguno"
    -- ver `_mensaje_confirmar_accesorios`), así que también se puede
    responder solo con números en vez de tener que escribir la palabra; si
    aparece junto con otros números, gana "ninguno" (no tendría sentido
    combinarlo). "todos"/"ninguno" en texto (y sinónimos, incluido el
    clásico sí/no) se aceptan igual como atajo. None si no se reconoció
    nada."""
    texto_norm = _normalizar(texto.strip())

    if texto_norm in ("todos", "todas", "ambos", "ambas", "todo", "si", "sí"):
        return list(range(cantidad))
    if texto_norm in ("ninguno", "ninguna", "ningun", "ningún", "no"):
        return []

    numeros = re.findall(r"\d+", texto_norm)
    if not numeros:
        return None

    numeros_int = [int(n) - 1 for n in numeros]
    if cantidad in numeros_int:  # 0-based: la opción "Ninguno" es cantidad + 1
        return []

    indices = []
    for idx in numeros_int:
        if 0 <= idx < cantidad and idx not in indices:
            indices.append(idx)
    return indices if indices else None


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
            if not wizard.get("subcategoria"):
                return "elegir_activo"
            if not wizard.get("marca"):
                return "elegir_marca"
            return "elegir_modelo"
        # nombre_recurso ya se resolvió pero todavía no se encajó en el
        # carrito -- eso lo maneja _avanzar_wizard antes de llegar aquí
        # (ver el chequeo al tope de su while), así que no debería pasar.
    if not wizard.get("fecha"):
        return "fecha"
    # La hora de inicio y la de fin se piden en pasos (mensajes) separados,
    # no juntas en un solo mensaje tipo "3pm a 5pm" -- para no confundir al
    # usuario con dos datos a la vez, sea la reserva de un día o de varios
    # (ver pasos "hora_inicio"/"hora_fin" en `_avanzar_wizard`).
    if not wizard.get("inicio"):
        return "hora_inicio"
    if not wizard.get("fin"):
        return "hora_fin"
    if not wizard.get("confirmado"):
        return "confirmar"
    return "listo"


def _avanzar_wizard(wizard: dict, usuario: Usuario) -> tuple[str, dict, bool]:
    """Avanza el wizard todo lo que se pueda sin pedirle nada al usuario
    (si un paso solo tiene una opción posible, ej. una sola marca en una
    subcategoría, se salta solo y sigue; y si un ítem recién se resolvió, se
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
            nuevos = [a for a in _accesorios_por_defecto(nombre) if a not in ya_en_carrito]

            if nuevos:
                # Se incluyen de una vez -- por defecto van, no hay que
                # pedirle al usuario que los adicione -- y se le muestra un
                # mensaje aparte (antes de pedirle fecha/hora) por si quiere
                # QUITAR alguno (ver `_mensaje_confirmar_accesorios` / paso
                # "confirmar_accesorios").
                for nombre_accesorio in nuevos:
                    wizard["carrito"].append({"nombre_recurso": nombre_accesorio, "auto": True, "por": nombre})
                wizard["accesorios_incluidos"] = nuevos
                wizard["item_padre_pendiente"] = nombre
                wizard["opciones_actuales"] = None
                wizard["paso"] = "confirmar_accesorios"
                mensaje = _mensaje_confirmar_accesorios(nombre, nuevos)
                wizard["_mensaje_menu_actual"] = mensaje
                return _con_avisos(avisos, mensaje), wizard, False

            # Si el ítem recién resuelto es un LUGAR (tipo='espacio'), se
            # muestra lo que contiene (jerarquía por `espacio_id`, ver
            # `_contenidos_de_espacio`) para que el usuario elija qué sumar
            # a la misma reserva -- a diferencia de los accesorios de
            # arriba, acá NO se agrega nada solo (ver la asimetría explicada
            # en `_contenidos_de_espacio`). Si el lugar no tiene contenidos
            # (o ya están todos en el carrito), sigue de largo igual que
            # hoy, directo a pedir fecha.
            unidad_item = _grupo(nombre)
            if unidad_item and unidad_item[0].tipo == "espacio":
                raices = [r for r in _contenidos_de_espacio(nombre) if r not in ya_en_carrito]
                if raices:
                    wizard["contenidos_espacio_disponibles"] = raices
                    wizard["espacio_padre_pendiente"] = nombre
                    wizard["opciones_actuales"] = None
                    wizard["paso"] = "elegir_contenidos_espacio"
                    mensaje = _mensaje_elegir_contenidos_espacio(nombre, raices)
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
            marcas = _marcas_de_subcategoria(wizard["subcategoria"])
            if len(marcas) == 1:
                wizard["marca"] = marcas[0]
                avisos.append(f"Como solo tenemos una marca de {wizard['subcategoria']} ({marcas[0]}), sigo con esa.")
                continue
            opciones = [(m, m) for m in marcas]
            wizard["opciones_actuales"] = opciones
            wizard["paso"] = "elegir_marca"
            mensaje = _formatear_menu(f"¿Qué marca de {wizard['subcategoria']} necesitas?", opciones)
            wizard["_mensaje_menu_actual"] = mensaje
            return _con_avisos(avisos, mensaje), wizard, False

        if paso == "elegir_modelo":
            grupos = _modelos_de_marca(wizard["subcategoria"], wizard["marca"])
            if len(grupos) == 1:
                nombre, representante = grupos[0]
                wizard["nombre_recurso"] = nombre
                etiqueta = representante.modelo or nombre
                avisos.append(f"Solo tenemos un modelo de {wizard['marca']} en {wizard['subcategoria']} ({etiqueta}), sigo con ese.")
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
            lineas_activos = "\n".join(
                _formatear_item_factura(item["nombre_recurso"], item["auto"]) for item in wizard["carrito"]
            )
            mensaje = (
                "*FECHA DE RESERVA*\n"
                f"{_DIVISOR_FACTURA}\n"
                f"{lineas_activos}\n"
                f"{_DIVISOR_FACTURA}\n"
                f"Hoy es {_DIAS_ES[ahora_local.weekday()]} {ahora_local.strftime('%d/%m/%Y')}.\n"
                "¿Qué fecha necesitas? Un solo día: dd/mm/aaaa (ej. 22/11/2026). "
                "Varios días: dd/mm/aaaa a dd/mm/aaaa (ej. 22/11/2026 a 25/11/2026)."
            )
            return _con_avisos(avisos, mensaje), wizard, False

        if paso == "hora_inicio":
            fecha_inicio = date.fromisoformat(wizard["fecha"])
            wizard["opciones_actuales"] = None
            wizard["paso"] = "hora_inicio"
            lineas_activos = "\n".join(
                _formatear_item_factura(item["nombre_recurso"], item["auto"]) for item in wizard["carrito"]
            )
            mensaje = (
                "*HORA DE INICIO*\n"
                f"{_DIVISOR_FACTURA}\n"
                f"{lineas_activos}\n"
                f"{_DIVISOR_FACTURA}\n"
                f"Fecha de inicio: {fecha_inicio.strftime('%d/%m/%Y')}\n"
                "¿A qué hora empieza? Formato hh:mm am/pm, por ejemplo: 3:00pm."
            )
            return _con_avisos(avisos, mensaje), wizard, False

        if paso == "hora_fin":
            fecha_fin = date.fromisoformat(wizard["fecha_fin"])
            inicio_local = datetime.fromisoformat(wizard["inicio"]).astimezone(ZONA_HORARIA_CPA)
            wizard["opciones_actuales"] = None
            wizard["paso"] = "hora_fin"
            lineas_activos = "\n".join(
                _formatear_item_factura(item["nombre_recurso"], item["auto"]) for item in wizard["carrito"]
            )
            mensaje = (
                "*HORA DE FIN*\n"
                f"{_DIVISOR_FACTURA}\n"
                f"{lineas_activos}\n"
                f"{_DIVISOR_FACTURA}\n"
                f"Inicio: {_formatear_fecha_hora(inicio_local)}\n"
                f"Fecha de fin: {fecha_fin.strftime('%d/%m/%Y')}\n"
                "¿A qué hora termina? Formato hh:mm am/pm, por ejemplo: 5:00pm."
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
        f"Fin: {_formatear_fecha_hora(fin_local)}\n"
        f"{_DIVISOR_FACTURA}"
    )
    if notas_seguro:
        mensaje += "\n" + "\n".join(notas_seguro)
    return mensaje, sesion_limpia


def _aplicar_fecha_valida(
    wizard: dict, usuario: Usuario, sesion: dict, fecha: date, fecha_fin: date | None = None
) -> tuple[str, dict]:
    """Guarda una fecha ya aceptada (venga del formato exacto o de una
    interpretación que el usuario confirmó) y avanza el wizard.
    `fecha_fin` permite una reserva de varios días (ver
    `_parsear_rango_fecha_dd_mm_aaaa`); si no se da, la reserva es de un
    solo día (`fecha_fin = fecha`) -- los pasos "hora_inicio"/"hora_fin"
    arman el inicio con la hora que dé el usuario sobre `fecha` y el fin
    con la hora sobre `fecha_fin`, en mensajes separados."""
    wizard["fecha"] = fecha.isoformat()
    wizard["fecha_fin"] = (fecha_fin or fecha).isoformat()
    wizard["opciones_actuales"] = None
    mensaje, wizard, listo = _avanzar_wizard(wizard, usuario)
    if listo:
        return _crear_reserva_desde_wizard(wizard, usuario, sesion)
    return mensaje, {**sesion, "reserva_wizard": wizard}


def _aplicar_hora_inicio_valida(wizard: dict, usuario: Usuario, sesion: dict, hora: tuple[int, int]) -> tuple[str, dict]:
    """Guarda la hora de inicio ya aceptada (formato exacto o interpretación
    confirmada) sobre `wizard["fecha"]` y avanza el wizard -- que a
    continuación pide la hora de fin en un mensaje aparte (paso
    "hora_fin"), nunca junto con la de inicio. Si ya pasó, regresa al paso
    "hora_inicio" para que el usuario reintente."""
    h, m = hora
    inicio_local = datetime.strptime(f"{wizard['fecha']} {h:02d}:{m:02d}", "%Y-%m-%d %H:%M").replace(
        tzinfo=ZONA_HORARIA_CPA
    )
    inicio_utc = inicio_local.astimezone(timezone.utc)

    if inicio_utc < datetime.now(timezone.utc) - MARGEN_INICIO_PASADO:
        wizard["paso"] = "hora_inicio"
        wizard["opciones_actuales"] = None
        return "Esa hora ya pasó. Dime una hora de inicio futura.", {**sesion, "reserva_wizard": wizard}

    wizard["inicio"] = inicio_utc.isoformat()
    wizard["opciones_actuales"] = None
    mensaje, wizard, listo = _avanzar_wizard(wizard, usuario)
    if listo:
        return _crear_reserva_desde_wizard(wizard, usuario, sesion)
    return mensaje, {**sesion, "reserva_wizard": wizard}


def _aplicar_hora_fin_valida(wizard: dict, usuario: Usuario, sesion: dict, hora: tuple[int, int]) -> tuple[str, dict]:
    """Guarda la hora de fin ya aceptada sobre `wizard["fecha_fin"]` (para
    un solo día, es la misma fecha que el inicio) y avanza el wizard. Si
    falla la validación (antes del inicio, choca con otra reserva), regresa
    al paso "hora_fin" para que el usuario reintente en vez de dejarlo en
    un paso de confirmación muerto."""
    h, m = hora
    fin_local = datetime.strptime(f"{wizard['fecha_fin']} {h:02d}:{m:02d}", "%Y-%m-%d %H:%M").replace(
        tzinfo=ZONA_HORARIA_CPA
    )
    fin_utc = fin_local.astimezone(timezone.utc)
    inicio_utc = datetime.fromisoformat(wizard["inicio"])

    if fin_utc <= inicio_utc:
        wizard["paso"] = "hora_fin"
        wizard["opciones_actuales"] = None
        return (
            "La hora de fin debe ser después de la hora de inicio. ¿Puedes revisar la hora?"
        ), {**sesion, "reserva_wizard": wizard}

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
    """Primer mensaje de un intento de reserva. El activo se identifica por
    palabra clave (`buscar_recursos_en_texto`): si el mensaje ya lo deja
    claro (ej. "cámara sony"), ese paso del wizard se salta solo -- ver
    `_avanzar_wizard`. La fecha y hora nunca salen del LLM: se preguntan
    siempre con los pasos de formato fijo (dd/mm/aaaa y hh:mm) para no
    arriesgarse a que el modelo "invente" una hora que el usuario nunca dio.

    Aquí ya no se llama a `llm_client.extraer_datos_reserva`. Se quitó
    porque no podía cambiar el resultado y costaba carísimo:

    - De los tres campos que devuelve, `inicio` y `fin` nunca se usaron (la
      fecha y la hora se preguntan aparte, por lo dicho arriba). Y menos mal:
      medido con `lfm2.5-2.6b`, "resérvame el dron mañana" devolvió
      2026-10-10 estando a 9 de septiembre -- un mes de más.
    - `recurso` sí se usaba, pero por una rama inalcanzable.
      `_resolver_recurso_para_reserva` solo mira el nombre del LLM cuando
      `buscar_recursos_en_texto` no encontró NINGÚN candidato, y además le
      exige `_puntaje_nombre_en_texto(...) > 0`. Como `candidatos` es
      justamente el conjunto de nombres con puntaje > 0, "sin candidatos"
      implica puntaje 0 para todos: las dos condiciones no pueden darse a
      la vez, así que ese `if` nunca se cumplía.
    - El costo era todo el retraso del wizard: 54 a 78 segundos por
      mensaje, medidos contra LM Studio.

    `extraer_datos_reserva` sigue existiendo en `llm_client` (y su prompt
    también), por si más adelante se quiere usar de verdad; simplemente ya
    no está en el camino crítico de la conversación."""
    nombres = listar_nombres_disponibles()

    if not nombres:
        return "Por ahora no tengo activos cargados en el sistema. Contacta al equipo de la CPA.", sesion

    recurso, candidatos, _ = _resolver_recurso_para_reserva(texto_usuario, {})

    wizard: dict = {
        "subcategoria": None,
        "marca": None,
        "nombre_recurso": None,
        "carrito": [],
        "resolviendo_item": True,
        "fecha": None,
        "fecha_fin": None,
        "inicio": None,
        "fin": None,
        "confirmado": False,
        "opciones_actuales": None,
        "paso": None,
    }

    if recurso is not None:
        wizard["nombre_recurso"] = recurso.nombre
    elif candidatos:
        # el texto mencionaba algo genérico (ej. "una cámara") o una marca
        # sin modelo (ej. "cámara canon", ambiguo entre EOS R y EOS R6) que
        # matcheó varias unidades -- nos saltamos los pasos que ya están
        # claros en el texto (tipo, y marca si todos los candidatos
        # comparten una) y arrancamos directo en el primero que sí hace
        # falta preguntar.
        subcategorias_candidatos = {c.subcategoria for c in candidatos if c.subcategoria}
        if len(subcategorias_candidatos) == 1:
            wizard["subcategoria"] = next(iter(subcategorias_candidatos))
            marcas_candidatos = {c.marca for c in candidatos if c.marca}
            if len(marcas_candidatos) == 1:
                wizard["marca"] = next(iter(marcas_candidatos))

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

        # Reserva de varios días: "dd/mm/aaaa a dd/mm/aaaa" -- se prueba
        # primero porque es más específico que una fecha única; si el texto
        # no matchea ese patrón, devuelve (None, None, None) y se sigue de
        # largo al caso de un solo día (ver `_parsear_rango_fecha_dd_mm_aaaa`).
        fecha_inicio_rango, fecha_fin_rango, error_rango = _parsear_rango_fecha_dd_mm_aaaa(texto, ahora_local)
        if error_rango:
            return error_rango, {**sesion, "reserva_wizard": wizard}
        if fecha_inicio_rango is not None:
            return _aplicar_fecha_valida(wizard, usuario, sesion, fecha_inicio_rango, fecha_fin_rango)

        fecha, error = _parsear_fecha_dd_mm_aaaa(texto, ahora_local)
        if fecha is not None:
            return _aplicar_fecha_valida(wizard, usuario, sesion, fecha)

        # No vino en dd/mm/aaaa -- se intenta reconocer en lenguaje natural,
        # primero un RANGO ("desde mañana hasta el viernes", "del lunes al
        # viernes") antes que una fecha única, porque una fecha única
        # buscando "viernes" dentro de esa misma frase la interpretaría
        # completa como esa sola fecha, perdiendo el "desde mañana" (ver
        # `_interpretar_rango_fecha_natural`). Nunca se acepta directo:
        # siempre se le muestra al usuario qué entendió el bot y se le pide
        # confirmar antes de seguir.
        rango_interpretado = _interpretar_rango_fecha_natural(texto, ahora_local)
        if rango_interpretado is not None:
            fecha_inicio_interp, fecha_fin_interp = rango_interpretado
            if fecha_inicio_interp < ahora_local.date():
                return (
                    "Esa fecha ya pasó. Dime una fecha de hoy en adelante, en formato dd/mm/aaaa."
                ), {**sesion, "reserva_wizard": wizard}
            if fecha_fin_interp < fecha_inicio_interp:
                return (
                    "La fecha de fin debe ser igual o después de la fecha de inicio. ¿Puedes revisar el rango?"
                ), {**sesion, "reserva_wizard": wizard}
            inicio_legible = f"{_DIAS_ES[fecha_inicio_interp.weekday()]} {fecha_inicio_interp.strftime('%d/%m/%Y')}"
            fin_legible = f"{_DIAS_ES[fecha_fin_interp.weekday()]} {fecha_fin_interp.strftime('%d/%m/%Y')}"
            wizard["fecha_pendiente"] = fecha_inicio_interp.isoformat()
            wizard["fecha_fin_pendiente"] = fecha_fin_interp.isoformat()
            wizard["paso"] = "confirmar_fecha_interpretada"
            wizard["opciones_actuales"] = OPCIONES_CONFIRMAR_FECHA
            mensaje = _formatear_menu(f"Entendí del {inicio_legible} al {fin_legible}. ¿Es correcto?", OPCIONES_CONFIRMAR_FECHA)
            wizard["_mensaje_menu_actual"] = mensaje
            return mensaje, {**sesion, "reserva_wizard": wizard}

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
            # No respondió "1"/"2" -- puede que haya vuelto a escribir la
            # fecha en vez de responder el menú (ej. repetir "mañana" en
            # lugar de escribir "1"). Se reintenta con el mismo criterio
            # del paso "fecha": si da la MISMA fecha/rango ya pendiente, se
            # toma como confirmación implícita (repetirla es reafirmarla);
            # si da una fecha distinta, se actualiza lo pendiente y se
            # vuelve a pedir confirmar -- nunca se acepta sin pasar por
            # este menú.
            texto = texto_usuario.strip()
            ahora_local = datetime.now(timezone.utc).astimezone(ZONA_HORARIA_CPA)
            nueva_inicio, nueva_fin = None, None

            r_inicio, r_fin, r_error = _parsear_rango_fecha_dd_mm_aaaa(texto, ahora_local)
            if r_inicio is not None:
                nueva_inicio, nueva_fin = r_inicio, r_fin
            elif r_error is None:
                f, _ = _parsear_fecha_dd_mm_aaaa(texto, ahora_local)
                if f is not None:
                    nueva_inicio = f
                else:
                    rango = _interpretar_rango_fecha_natural(texto, ahora_local)
                    if rango is not None:
                        nueva_inicio, nueva_fin = rango
                    else:
                        nueva_inicio = _interpretar_fecha_natural(texto, ahora_local)

            if nueva_inicio is None:
                return "No reconocí esa opción.\n\n" + (wizard.get("_mensaje_menu_actual") or ""), {
                    **sesion,
                    "reserva_wizard": wizard,
                }
            if nueva_inicio < ahora_local.date():
                return (
                    "Esa fecha ya pasó. Dime una fecha de hoy en adelante, en formato dd/mm/aaaa."
                ), {**sesion, "reserva_wizard": wizard}
            if nueva_fin is not None and nueva_fin < nueva_inicio:
                return (
                    "La fecha de fin debe ser igual o después de la fecha de inicio. ¿Puedes revisar el rango?"
                ), {**sesion, "reserva_wizard": wizard}

            pendiente_inicio = date.fromisoformat(wizard["fecha_pendiente"])
            pendiente_fin_str = wizard.get("fecha_fin_pendiente")
            pendiente_fin = date.fromisoformat(pendiente_fin_str) if pendiente_fin_str else None

            if nueva_inicio == pendiente_inicio and nueva_fin == pendiente_fin:
                valor = "si"
            else:
                wizard["fecha_pendiente"] = nueva_inicio.isoformat()
                wizard["fecha_fin_pendiente"] = nueva_fin.isoformat() if nueva_fin else None
                if nueva_fin is not None:
                    inicio_legible = f"{_DIAS_ES[nueva_inicio.weekday()]} {nueva_inicio.strftime('%d/%m/%Y')}"
                    fin_legible = f"{_DIAS_ES[nueva_fin.weekday()]} {nueva_fin.strftime('%d/%m/%Y')}"
                    pregunta = f"Entendí del {inicio_legible} al {fin_legible}. ¿Es correcto?"
                else:
                    fecha_legible = f"{_DIAS_ES[nueva_inicio.weekday()]} {nueva_inicio.strftime('%d/%m/%Y')}"
                    pregunta = f"Entendí {fecha_legible}. ¿Es correcto?"
                mensaje = _formatear_menu(pregunta, OPCIONES_CONFIRMAR_FECHA)
                wizard["_mensaje_menu_actual"] = mensaje
                return mensaje, {**sesion, "reserva_wizard": wizard}

        if valor == "no":
            wizard["fecha_pendiente"] = None
            wizard["fecha_fin_pendiente"] = None
            wizard["paso"] = "fecha"
            wizard["opciones_actuales"] = None
            return (
                "Está bien, escribe la fecha en formato dd/mm/aaaa, por ejemplo 22/11/2026."
            ), {**sesion, "reserva_wizard": wizard}
        fecha = date.fromisoformat(wizard.pop("fecha_pendiente"))
        fecha_fin_str = wizard.pop("fecha_fin_pendiente", None)
        fecha_fin = date.fromisoformat(fecha_fin_str) if fecha_fin_str else None
        return _aplicar_fecha_valida(wizard, usuario, sesion, fecha, fecha_fin)

    if paso == "hora_inicio":
        texto = texto_usuario.strip()

        hora = _parsear_hora_individual(texto)
        if hora is not None:
            return _aplicar_hora_inicio_valida(wizard, usuario, sesion, hora)

        # Igual que con la fecha: si no vino en el formato exacto pero es
        # reconocible con variaciones comunes (ej. "a las 3pm"), se
        # interpreta pero se le pide confirmar antes de aceptarla.
        hora_relajada = _interpretar_hora_individual_relajada(texto)
        if hora_relajada is not None:
            h, m = hora_relajada
            hora_legible = _formatear_hora(datetime(2000, 1, 1, h, m))
            wizard["hora_inicio_pendiente"] = f"{h:02d}:{m:02d}"
            wizard["paso"] = "confirmar_hora_inicio_interpretada"
            wizard["opciones_actuales"] = OPCIONES_CONFIRMAR_HORA
            mensaje = _formatear_menu(f"Entendí {hora_legible} ¿Es correcto?", OPCIONES_CONFIRMAR_HORA)
            wizard["_mensaje_menu_actual"] = mensaje
            return mensaje, {**sesion, "reserva_wizard": wizard}

        return (
            "No reconocí ese formato de hora. Escribe algo como '3:00pm' o '15:00'."
        ), {**sesion, "reserva_wizard": wizard}

    if paso == "confirmar_hora_inicio_interpretada":
        opciones = wizard.get("opciones_actuales") or OPCIONES_CONFIRMAR_HORA
        valor = _interpretar_opcion(texto_usuario, opciones)

        if valor is None:
            # Mismo criterio que en "confirmar_fecha_interpretada": si
            # volvió a escribir la hora en vez de responder "1"/"2" y da la
            # MISMA hora ya pendiente, se toma como confirmación implícita;
            # si da una hora distinta, se actualiza y se vuelve a preguntar.
            texto = texto_usuario.strip()
            nueva_hora = _parsear_hora_individual(texto) or _interpretar_hora_individual_relajada(texto)
            if nueva_hora is None:
                return "No reconocí esa opción.\n\n" + (wizard.get("_mensaje_menu_actual") or ""), {
                    **sesion,
                    "reserva_wizard": wizard,
                }
            h, m = nueva_hora
            ph, pm = (int(x) for x in wizard["hora_inicio_pendiente"].split(":"))
            if (h, m) == (ph, pm):
                valor = "si"
            else:
                wizard["hora_inicio_pendiente"] = f"{h:02d}:{m:02d}"
                hora_legible = _formatear_hora(datetime(2000, 1, 1, h, m))
                mensaje = _formatear_menu(f"Entendí {hora_legible} ¿Es correcto?", OPCIONES_CONFIRMAR_HORA)
                wizard["_mensaje_menu_actual"] = mensaje
                return mensaje, {**sesion, "reserva_wizard": wizard}

        if valor == "no":
            wizard["hora_inicio_pendiente"] = None
            wizard["paso"] = "hora_inicio"
            wizard["opciones_actuales"] = None
            return (
                "Está bien, escribe la hora de inicio en formato hh:mm am/pm, por ejemplo 3:00pm."
            ), {**sesion, "reserva_wizard": wizard}
        h, m = (int(x) for x in wizard.pop("hora_inicio_pendiente").split(":"))
        return _aplicar_hora_inicio_valida(wizard, usuario, sesion, (h, m))

    if paso == "hora_fin":
        texto = texto_usuario.strip()

        hora = _parsear_hora_individual(texto)
        if hora is not None:
            return _aplicar_hora_fin_valida(wizard, usuario, sesion, hora)

        hora_relajada = _interpretar_hora_individual_relajada(texto)
        if hora_relajada is not None:
            h, m = hora_relajada
            hora_legible = _formatear_hora(datetime(2000, 1, 1, h, m))
            wizard["hora_fin_pendiente"] = f"{h:02d}:{m:02d}"
            wizard["paso"] = "confirmar_hora_fin_interpretada"
            wizard["opciones_actuales"] = OPCIONES_CONFIRMAR_HORA
            mensaje = _formatear_menu(f"Entendí {hora_legible} ¿Es correcto?", OPCIONES_CONFIRMAR_HORA)
            wizard["_mensaje_menu_actual"] = mensaje
            return mensaje, {**sesion, "reserva_wizard": wizard}

        return (
            "No reconocí ese formato de hora. Escribe algo como '5:00pm' o '17:00'."
        ), {**sesion, "reserva_wizard": wizard}

    if paso == "confirmar_hora_fin_interpretada":
        opciones = wizard.get("opciones_actuales") or OPCIONES_CONFIRMAR_HORA
        valor = _interpretar_opcion(texto_usuario, opciones)

        if valor is None:
            # Mismo criterio que en "confirmar_hora_inicio_interpretada".
            texto = texto_usuario.strip()
            nueva_hora = _parsear_hora_individual(texto) or _interpretar_hora_individual_relajada(texto)
            if nueva_hora is None:
                return "No reconocí esa opción.\n\n" + (wizard.get("_mensaje_menu_actual") or ""), {
                    **sesion,
                    "reserva_wizard": wizard,
                }
            h, m = nueva_hora
            ph, pm = (int(x) for x in wizard["hora_fin_pendiente"].split(":"))
            if (h, m) == (ph, pm):
                valor = "si"
            else:
                wizard["hora_fin_pendiente"] = f"{h:02d}:{m:02d}"
                hora_legible = _formatear_hora(datetime(2000, 1, 1, h, m))
                mensaje = _formatear_menu(f"Entendí {hora_legible} ¿Es correcto?", OPCIONES_CONFIRMAR_HORA)
                wizard["_mensaje_menu_actual"] = mensaje
                return mensaje, {**sesion, "reserva_wizard": wizard}

        if valor == "no":
            wizard["hora_fin_pendiente"] = None
            wizard["paso"] = "hora_fin"
            wizard["opciones_actuales"] = None
            return (
                "Está bien, escribe la hora de fin en formato hh:mm am/pm, por ejemplo 5:00pm."
            ), {**sesion, "reserva_wizard": wizard}
        h, m = (int(x) for x in wizard.pop("hora_fin_pendiente").split(":"))
        return _aplicar_hora_fin_valida(wizard, usuario, sesion, (h, m))

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
            wizard.update(subcategoria=None, marca=None, nombre_recurso=None, resolviendo_item=True)
        elif valor == "quitar_item":
            return _iniciar_quitar_item(wizard, sesion)
        elif valor == "cambiar_fecha":
            wizard.update(fecha=None, fecha_fin=None, inicio=None, fin=None)
        elif valor == "cambiar_hora":
            wizard.update(inicio=None, fin=None)

        mensaje, wizard, listo = _avanzar_wizard(wizard, usuario)
        if listo:
            return _crear_reserva_desde_wizard(wizard, usuario, sesion)
        return mensaje, {**sesion, "reserva_wizard": wizard}

    if paso == "confirmar_accesorios":
        incluidos = wizard.get("accesorios_incluidos") or []
        indices = _interpretar_seleccion_multiple(texto_usuario, len(incluidos))
        if indices is None:
            return "No reconocí esa opción.\n\n" + (wizard.get("_mensaje_menu_actual") or ""), {
                **sesion,
                "reserva_wizard": wizard,
            }

        wizard.pop("accesorios_incluidos", None)
        wizard.pop("item_padre_pendiente", None)
        # Los accesorios ya estaban incluidos por defecto (ver el fold-in
        # en `_avanzar_wizard`) -- los índices que el usuario eligió acá son
        # los que quiere QUITAR, uno, varios o ninguno, no una lista de qué
        # incluir (ver `_interpretar_seleccion_multiple`).
        a_quitar = {incluidos[i] for i in indices}
        if a_quitar:
            wizard["carrito"] = [item for item in wizard["carrito"] if item["nombre_recurso"] not in a_quitar]
        # No hace falta revisar disponibilidad acá -- ya se revisa en el
        # paso "hora_fin" (o al confirmar, si la hora ya estaba puesta por
        # venir de "agregar otro activo"), ver `_pausar_por_no_disponibles`.

        wizard["opciones_actuales"] = None
        mensaje, wizard, listo = _avanzar_wizard(wizard, usuario)
        if listo:
            return _crear_reserva_desde_wizard(wizard, usuario, sesion)
        return mensaje, {**sesion, "reserva_wizard": wizard}

    if paso == "elegir_contenidos_espacio":
        disponibles = wizard.get("contenidos_espacio_disponibles") or []
        indices = _interpretar_seleccion_multiple(texto_usuario, len(disponibles))
        if indices is None:
            return "No reconocí esa opción.\n\n" + (wizard.get("_mensaje_menu_actual") or ""), {
                **sesion,
                "reserva_wizard": wizard,
            }

        wizard.pop("contenidos_espacio_disponibles", None)
        wizard.pop("espacio_padre_pendiente", None)
        # OJO, semántica invertida respecto a "confirmar_accesorios" arriba:
        # ahí nada venía incluido y los índices eran qué QUITAR de lo ya
        # incluido; acá nada viene incluido y los índices son qué AGREGAR
        # (ver la asimetría explicada en `_contenidos_de_espacio`).
        a_agregar = [disponibles[i] for i in indices]
        ya_en_carrito = {item["nombre_recurso"] for item in wizard["carrito"]}
        for nombre_elegido in a_agregar:
            if nombre_elegido in ya_en_carrito:
                continue
            wizard["carrito"].append({"nombre_recurso": nombre_elegido, "auto": False, "por": None})
            ya_en_carrito.add(nombre_elegido)
            # Cada activo del lugar que el usuario decide sumar puede a su
            # vez tener sus propios hijos por `padre_id` (ej. la Cámara y
            # sus lentes) -- esos sí se arrastran automáticos, reusando el
            # mismo mecanismo que los accesorios en bundle normales.
            for hijo in _accesorios_por_defecto(nombre_elegido):
                if hijo in ya_en_carrito:
                    continue
                wizard["carrito"].append({"nombre_recurso": hijo, "auto": True, "por": nombre_elegido})
                ya_en_carrito.add(hijo)
        # No hace falta revisar disponibilidad acá -- ya se revisa en el
        # paso "hora_fin" (o al confirmar), ver `_pausar_por_no_disponibles`.

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
    # accesorios", no "cancela toda la reserva". Mismo motivo, en espejo,
    # para "elegir_contenidos_espacio": ahí su opción "Ninguno" también se
    # puede responder como "no" (ver `_interpretar_seleccion_multiple`), y
    # significa "no agregues nada de lo que contiene el lugar", no "cancela
    # toda la reserva" -- si se olvida acá, "ninguno"/"no" cancelaría todo
    # el wizard en vez de simplemente seguir a pedir fecha.
    palabras_cancelar = PALABRAS_CANCELAR
    if wizard is not None and wizard.get("paso") in (
        "confirmar_fecha_interpretada",
        "confirmar_hora_inicio_interpretada",
        "confirmar_hora_fin_interpretada",
        "confirmar_accesorios",
        "elegir_contenidos_espacio",
    ):
        palabras_cancelar = {"cancelar", "cancela", "cerrar"}

    if wizard is not None and set(_normalizar(texto_usuario).split()) & palabras_cancelar:
        return (
            "Reserva cancelada, no se guardó nada. Si quieres reservar otra cosa, dime qué necesitas."
        ), {**sesion, "reserva_wizard": None}

    if wizard is None:
        return _iniciar_reserva(texto_usuario, usuario, sesion)
    return _continuar_reserva(texto_usuario, usuario, sesion, wizard)


# --- Cancelación de una reserva ya confirmada ------------------------------
#
# Distinto del "cerrar"/PALABRAS_CANCELAR de arriba, que corta un wizard de
# reserva EN CURSO antes de que se escriba nada en la base. Esto es para el
# intent 'cancelar' de webhook.py: el usuario ya tiene una reserva
# confirmada y quiere anularla.
#
# La única garantía de que nadie cancele la reserva de otro usuario es que
# `usuario` llega a estas funciones ya resuelto por recursos_service a
# partir del número de WhatsApp que mandó el mensaje (ver
# `usuarios_service`/webhook.py) -- nunca de un id, nombre o placa que el
# texto del chat pueda afirmar. Por eso toda consulta de acá filtra siempre
# por `Reserva.usuario_id == usuario.id`, tanto al listar qué se puede
# cancelar como al ejecutar la cancelación -- ese segundo filtro es el que
# de verdad importa (el primero solo evita mostrarle a alguien una lista que
# ni es suya).


def _reservas_cancelables_de(usuario_id: int) -> list[list[Reserva]]:
    """Reservas confirmadas y no terminadas (en curso o futuras) del usuario
    dado, agrupadas por `grupo_id` -- un padre y sus accesorios se cancelan
    juntos, como se reservaron juntos. Una reserva de antes de que existiera
    el carrito (`grupo_id IS NULL`) se trata como su propio bundle de 1.
    Ordenadas por fecha de inicio, para que la más próxima sea la opción 1."""
    ahora = datetime.now(timezone.utc)
    filas = (
        Reserva.query.filter(
            Reserva.usuario_id == usuario_id,
            Reserva.estado == "confirmada",
            Reserva.fin > ahora,
        )
        .order_by(Reserva.inicio)
        .all()
    )

    return _agrupar_por_reserva_grupo(filas)


def _agrupar_por_reserva_grupo(filas: list[Reserva]) -> list[list[Reserva]]:
    """Junta una lista plana de `Reserva` en bundles por `grupo_id` (un
    padre y sus accesorios quedan juntos, como se reservaron); una reserva
    sin grupo (de antes del carrito) es su propio bundle de 1. Ordenados
    por fecha de inicio -- se usa tanto para armar el menú inicial
    (`_reservas_cancelables_de`) como para reagrupar lo que el usuario
    seleccionó (ver paso "elegir_reserva" más abajo)."""
    grupos: dict[int, list[Reserva]] = {}
    bundles: list[list[Reserva]] = []
    for r in filas:
        if r.grupo_id:
            grupos.setdefault(r.grupo_id, []).append(r)
        else:
            bundles.append([r])
    bundles.extend(grupos.values())
    bundles.sort(key=lambda b: b[0].inicio)
    return bundles


def _mensaje_elegir_reserva_a_cancelar(bundles: list[list[Reserva]]) -> str:
    bloques = []
    for i, bundle in enumerate(bundles, start=1):
        nombres = "\n".join(f"    • {r.recurso.nombre}" for r in bundle)
        inicio_local = bundle[0].inicio.astimezone(ZONA_HORARIA_CPA)
        fin_local = bundle[0].fin.astimezone(ZONA_HORARIA_CPA)
        bloques.append(
            f"*{i}.*\n{nombres}\n"
            f"    Inicio: {_formatear_fecha_hora(inicio_local)}\n"
            f"    Fin: {_formatear_fecha_hora(fin_local)}"
        )
    cuerpo = "\n\n".join(bloques)
    return (
        "*TUS RESERVAS ACTIVAS*\n"
        f"{_DIVISOR_FACTURA}\n"
        f"{cuerpo}\n"
        f"{_DIVISOR_FACTURA}\n"
        "¿Cuáles quieres cancelar? Responde con el número, con varios separados "
        'por coma (ej. "1,2"), "todos" para cancelarlas todas, o "cerrar" para '
        "no cancelar nada."
    )


OPCIONES_CONFIRMAR_CANCELACION = [
    ("si", "Sí, cancelar"),
    ("no", "No, dejar la reserva como está"),
]


def _mensaje_confirmar_cancelacion(bundles: list[list[Reserva]]) -> str:
    bloques = []
    for bundle in bundles:
        nombres = "\n".join(f"    • {r.recurso.nombre}" for r in bundle)
        inicio_local = bundle[0].inicio.astimezone(ZONA_HORARIA_CPA)
        fin_local = bundle[0].fin.astimezone(ZONA_HORARIA_CPA)
        bloques.append(
            f"{nombres}\n"
            f"    Inicio: {_formatear_fecha_hora(inicio_local)}\n"
            f"    Fin: {_formatear_fecha_hora(fin_local)}"
        )
    cuerpo = "\n\n".join(bloques)
    verbo = "esta reserva" if len(bundles) == 1 else f"estas {len(bundles)} reservas"
    encabezado = (
        "*CONFIRMAR CANCELACIÓN*\n"
        f"{_DIVISOR_FACTURA}\n"
        f"{cuerpo}\n"
        f"{_DIVISOR_FACTURA}"
    )
    return encabezado + "\n\n" + _formatear_menu(
        f"¿Confirmas que quieres cancelar {verbo}?", OPCIONES_CONFIRMAR_CANCELACION
    )


def _iniciar_cancelacion(texto_usuario: str, usuario: Usuario, sesion: dict) -> tuple[str, dict]:
    bundles = _reservas_cancelables_de(usuario.id)
    if not bundles:
        return (
            "No tienes reservas activas para cancelar."
        ), {**sesion, "cancelacion_wizard": None}

    wizard = {"paso": "elegir_reserva", "bundles_ids": [[r.id for r in b] for b in bundles]}
    return _mensaje_elegir_reserva_a_cancelar(bundles), {**sesion, "cancelacion_wizard": wizard}


def _continuar_cancelacion(
    texto_usuario: str, usuario: Usuario, sesion: dict, wizard: dict
) -> tuple[str, dict]:
    paso = wizard["paso"]

    if paso == "elegir_reserva":
        bundles_ids = wizard["bundles_ids"]
        indices = _interpretar_seleccion_multiple(texto_usuario, len(bundles_ids))
        if indices is None:
            return (
                "No reconocí esa opción. Responde con el número (o varios separados "
                'por coma) de las reservas que quieres cancelar, "todos" para '
                'cancelarlas todas, o "cerrar" para no cancelar nada.'
            ), {**sesion, "cancelacion_wizard": wizard}
        if not indices:
            return "No cancelé ninguna reserva.", {**sesion, "cancelacion_wizard": None}

        # Se relee de la base (no se confía en lo que se armó para el menú
        # anterior) filtrando otra vez por usuario_id -- por si algo cambió
        # entre que se mostró la lista y esta respuesta.
        ids_elegidos = [rid for i in indices for rid in bundles_ids[i]]
        filas = Reserva.query.filter(
            Reserva.id.in_(ids_elegidos),
            Reserva.usuario_id == usuario.id,
            Reserva.estado == "confirmada",
        ).all()
        if not filas:
            return (
                "Esas reservas ya no están disponibles para cancelar (puede que ya se "
                'hayan cancelado). Escribe "cancelar" de nuevo para ver tus reservas activas.'
            ), {**sesion, "cancelacion_wizard": None}

        bundles_elegidos = _agrupar_por_reserva_grupo(filas)
        mensaje = _mensaje_confirmar_cancelacion(bundles_elegidos)
        wizard["paso"] = "confirmar_cancelacion"
        wizard["reserva_ids"] = [r.id for r in filas]
        wizard["cantidad_reservas"] = len(bundles_elegidos)
        wizard["opciones_actuales"] = OPCIONES_CONFIRMAR_CANCELACION
        wizard["_mensaje_menu_actual"] = mensaje
        return mensaje, {**sesion, "cancelacion_wizard": wizard}

    if paso == "confirmar_cancelacion":
        valor = _interpretar_opcion(texto_usuario, OPCIONES_CONFIRMAR_CANCELACION)
        if valor is None:
            return (
                "No reconocí esa opción.\n\n" + (wizard.get("_mensaje_menu_actual") or "")
            ), {**sesion, "cancelacion_wizard": wizard}
        if valor == "no":
            return "No cancelé ninguna reserva.", {**sesion, "cancelacion_wizard": None}

        # El filtro por usuario_id acá (no solo al armar el menú) es la
        # verificación que de verdad importa: es la que ejecuta el cambio en
        # la base, así que es la que tiene que ser imposible de saltarse.
        actualizadas = Reserva.query.filter(
            Reserva.id.in_(wizard["reserva_ids"]),
            Reserva.usuario_id == usuario.id,
            Reserva.estado == "confirmada",
        ).update({"estado": "cancelada"}, synchronize_session=False)
        db.session.commit()

        if not actualizadas:
            return (
                "Esas reservas ya no estaban activas, no se canceló nada."
            ), {**sesion, "cancelacion_wizard": None}

        cantidad = wizard.get("cantidad_reservas", 1)
        verbo = "esa reserva" if cantidad == 1 else f"esas {cantidad} reservas"
        return f"Listo, cancelé {verbo}.", {**sesion, "cancelacion_wizard": None}

    return _iniciar_cancelacion(texto_usuario, usuario, {**sesion, "cancelacion_wizard": None})


def gestionar_cancelacion(texto_usuario: str, usuario: Usuario, sesion: dict) -> tuple[str, dict]:
    """Punto de entrada para el intent 'cancelar' (anular una reserva que ya
    quedó confirmada -- para cortar un wizard de reserva que todavía está en
    curso, ver PALABRAS_CANCELAR dentro de `gestionar_reserva`). Mientras
    `sesion` tenga `cancelacion_wizard` distinto de None, webhook.py manda
    la respuesta directo aquí en vez de pasar por clasificación de intent,
    mismo motivo que `reserva_wizard` (ver `gestionar_reserva`)."""
    wizard = sesion.get("cancelacion_wizard")

    if wizard is not None and set(_normalizar(texto_usuario).split()) & PALABRAS_CANCELAR:
        return "No cancelé ninguna reserva.", {**sesion, "cancelacion_wizard": None}

    if wizard is None:
        return _iniciar_cancelacion(texto_usuario, usuario, sesion)
    return _continuar_cancelacion(texto_usuario, usuario, sesion, wizard)
