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


PALABRAS_CANCELAR = {"no", "cancelar", "cancela"}

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


OPCIONES_CONFIRMAR = [
    ("confirmar", "Confirmar"),
    ("cambiar_activo", "Cambiar activo"),
    ("cambiar_fecha", "Cambiar fecha"),
    ("cambiar_hora", "Cambiar hora"),
    ("cancelar", "Cancelar"),
]


def _siguiente_paso_wizard(wizard: dict) -> str:
    if not wizard.get("nombre_recurso"):
        if not wizard.get("categoria"):
            return "elegir_activo"
        if not wizard.get("marca"):
            return "elegir_marca"
        return "elegir_modelo"
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
    categoría, se salta solo y sigue) y se detiene en el primer paso que sí
    necesita una respuesta, o cuando el usuario ya confirmó todo
    (`listo=True`, momento en el que el llamador crea la reserva de
    verdad). Devuelve (mensaje_para_el_usuario, wizard_actualizado, listo)."""
    avisos: list[str] = []

    while True:
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
            mensaje = (
                f"Hoy es {_DIAS_ES[ahora_local.weekday()]} {ahora_local.strftime('%d/%m/%Y')}. "
                f"¿Qué fecha necesitas '{wizard['nombre_recurso']}'? "
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


def _mensaje_resumen(wizard: dict, usuario: Usuario) -> str:
    inicio_utc = datetime.fromisoformat(wizard["inicio"])
    fin_utc = datetime.fromisoformat(wizard["fin"])
    inicio_local = inicio_utc.astimezone(ZONA_HORARIA_CPA)
    fin_local = fin_utc.astimezone(ZONA_HORARIA_CPA)
    resumen = (
        "Resumen de tu reserva:\n"
        f"- Activo: {wizard['nombre_recurso']}\n"
        f"- Reservado por: {usuario.nombre}\n"
        f"- Inicio: {_formatear_fecha_hora(inicio_local)}\n"
        f"- Fin: {_formatear_fecha_hora(fin_local)}\n"
    )
    return resumen + "\n" + _formatear_menu("¿Qué quieres hacer?", OPCIONES_CONFIRMAR)


def _con_avisos(avisos: list[str], mensaje: str) -> str:
    if not avisos:
        return mensaje
    return "\n".join(avisos) + ("\n\n" + mensaje if mensaje else "")


def _crear_reserva_desde_wizard(wizard: dict, usuario: Usuario, sesion: dict) -> tuple[str, dict]:
    """Se llama solo cuando el usuario eligió "Confirmar" en el paso final
    -- aquí sí se escribe de verdad en `reservas`, todo lo anterior solo
    preparaba los datos."""
    nombre = wizard["nombre_recurso"]
    sesion_limpia = {**sesion, "reserva_wizard": None}

    grupo = _grupo(nombre)
    if not grupo:
        return "Ese activo ya no está disponible. Si quieres, intenta reservar de nuevo.", sesion_limpia

    inicio_utc = datetime.fromisoformat(wizard["inicio"])
    fin_utc = datetime.fromisoformat(wizard["fin"])
    ahora_utc = datetime.now(timezone.utc)

    if inicio_utc < ahora_utc - MARGEN_INICIO_PASADO:
        return (
            "Esa fecha/hora ya pasó mientras confirmabas. Intenta reservar de nuevo con una fecha futura."
        ), sesion_limpia

    if _unidad_libre_en_rango(nombre, inicio_utc, fin_utc) is None:
        return (
            f"'{nombre}' se acaba de reservar (todas sus unidades) mientras confirmabas, y se cruza "
            "con tu horario. Prueba con otro horario."
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
            # _formatear_hora ya termina en "a.m."/"p.m." (con punto), así
            # que no se agrega otro punto aparte antes de _nota_seguro.
            return (
                f"Listo, reservé '{nombre}' -- {_formatear_fecha_hora(inicio_local)} "
                f"a {_formatear_hora(fin_local)}{_nota_seguro(candidata)}"
            ), sesion_limpia

    return f"'{nombre}' ya no está disponible en ese horario. Prueba con otro horario.", sesion_limpia


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
        fecha, error = _parsear_fecha_dd_mm_aaaa(texto_usuario.strip(), ahora_local)
        if error:
            return error, {**sesion, "reserva_wizard": wizard}
        wizard["fecha"] = fecha.isoformat()
        mensaje, wizard, listo = _avanzar_wizard(wizard, usuario)
        if listo:
            return _crear_reserva_desde_wizard(wizard, usuario, sesion)
        return mensaje, {**sesion, "reserva_wizard": wizard}

    if paso == "hora":
        rango = _parsear_rango_horario(texto_usuario.strip())
        if rango is None:
            return (
                "No reconocí ese formato de hora. Escribe algo como '3:00pm a 5:00pm' o '15:00 a 17:00'."
            ), {**sesion, "reserva_wizard": wizard}
        (h1, m1), (h2, m2) = rango
        fecha = wizard["fecha"]
        inicio_str = f"{fecha} {h1:02d}:{m1:02d}"
        fin_str = f"{fecha} {h2:02d}:{m2:02d}"
        inicio_utc, fin_utc, error = _parsear_fecha_hora(inicio_str, fin_str, datetime.now(timezone.utc))
        if error:
            return error, {**sesion, "reserva_wizard": wizard}
        if _unidad_libre_en_rango(wizard["nombre_recurso"], inicio_utc, fin_utc) is None:
            return (
                f"'{wizard['nombre_recurso']}' ya está reservado (todas sus unidades) en ese horario. "
                "Prueba con otro horario."
            ), {**sesion, "reserva_wizard": wizard}
        wizard["inicio"] = inicio_utc.isoformat()
        wizard["fin"] = fin_utc.isoformat()
        mensaje, wizard, listo = _avanzar_wizard(wizard, usuario)
        if listo:
            return _crear_reserva_desde_wizard(wizard, usuario, sesion)
        return mensaje, {**sesion, "reserva_wizard": wizard}

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

        if valor == "cambiar_activo":
            wizard.update(categoria=None, marca=None, nombre_recurso=None)
        elif valor == "cambiar_fecha":
            wizard.update(fecha=None, inicio=None, fin=None)
        elif valor == "cambiar_hora":
            wizard.update(inicio=None, fin=None)

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
    solo sí/no, el último paso del wizard ("confirmar") es un menú que
    también permite cambiar el activo, la fecha o la hora sin reiniciar
    todo. Mientras `sesion` tenga `reserva_wizard` distinto de None,
    webhook.py manda la respuesta del usuario directo aquí en vez de pasar
    por clasificación de intent (una respuesta corta como "2" no se
    reconoce de forma confiable como intent "reservar" y quedaría
    bloqueada por la whitelist)."""
    wizard = sesion.get("reserva_wizard")

    if wizard is not None and set(_normalizar(texto_usuario).split()) & PALABRAS_CANCELAR:
        return (
            "Reserva cancelada, no se guardó nada. Si quieres reservar otra cosa, dime qué necesitas."
        ), {**sesion, "reserva_wizard": None}

    if wizard is None:
        return _iniciar_reserva(texto_usuario, usuario, sesion)
    return _continuar_reserva(texto_usuario, usuario, sesion, wizard)
