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

from flask import current_app
from sqlalchemy.exc import IntegrityError

from app import llm_client
from app.extensions import db
from app.models import Recurso, Reserva, ReservaGrupo, Usuario
from app.textutils import normalizar as _normalizar

LONGITUD_MINIMA_PALABRA_CLAVE = 4
# Bono cuando el texto trae el `modelo` completo tal cual (ej. "eos r6"), ver
# `_puntaje_recurso_en_texto`. Menor que el +10 del nombre completo: ese
# sigue siendo la señal más fuerte.
BONO_MODELO_COMPLETO = 5
# En la descripción sí cuentan palabras de 3 letras ("mac", "usb", "led",
# "voz"): ahí suelen estar justo los sinónimos cortos con que un estudiante
# pide un equipo. El ruido que eso trae (artículos, preposiciones) lo filtra
# PALABRAS_VACIAS_DESCRIPCION. En el nombre se mantiene el mínimo de 4 para no
# alterar los puntajes de siempre ("kit", "pro", "eos").
LONGITUD_MINIMA_PALABRA_DESCRIPCION = 3
# Si la búsqueda por descripción empata más modelos que esto, las palabras que
# coincidieron eran genéricas: listarlos todos sería volver al volcado del
# catálogo que se quiso evitar, así que se trata como "no identifiqué nada".
MAX_MODELOS_POR_DESCRIPCION = 6
# Ya normalizadas (sin tildes). Se descartan de la DESCRIPCIÓN antes de
# buscarlas en el texto: sin esto, "para" o "con" de una descripción
# coincidirían con casi cualquier mensaje ("tienen un mac para editar?").
PALABRAS_VACIAS_DESCRIPCION = frozenset({
    # artículos, preposiciones, conjunciones, pronombres
    "el", "la", "lo", "los", "las", "un", "una", "uno", "unos", "unas",
    "al", "del", "de", "en", "con", "sin", "por", "para", "que", "como",
    "entre", "sobre", "bajo", "desde", "hasta", "hacia", "segun", "tras",
    "y", "o", "u", "e", "ni", "pero", "mas", "muy", "tan", "ya",
    "se", "su", "sus", "mi", "mis", "tu", "tus", "le", "les", "nos",
    "este", "esta", "esto", "estos", "estas", "ese", "esa", "eso", "esos", "esas",
    "otro", "otra", "otros", "otras", "todo", "toda", "todos", "todas",
    "cada", "cual", "cuales", "donde", "cuando",
    # relleno típico de una pregunta o de una ficha de inventario
    "es", "son", "hay", "tiene", "tienen", "tengo", "tenemos", "incluye",
    "trae", "sirve", "sirven", "usar", "uso", "ideal", "tipo",
    "algo", "algun", "alguno", "alguna", "algunos", "algunas",
    "necesito", "quiero", "quisiera", "busco", "puedo",
    "reservar", "reserva", "prestar", "prestamo",
    "disponible", "disponibles", "libre", "libres",
    "equipo", "equipos", "activo", "activos", "cosa", "cosas",
    "gran", "buen", "buena", "bueno",
})
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

    Solo mira nombre y modelo. La descripción es un nivel aparte y de menor
    prioridad (`buscar_recursos_por_descripcion`), así que no altera ni los
    candidatos ni `es_match_debil` de esta función.
    """
    texto_norm = _normalizar(texto)
    puntuados: list[tuple[int, Recurso]] = []

    for _, representante in _grupos_activos():
        score = _puntaje_recurso_en_texto(representante, texto_norm)
        if score > 0:
            puntuados.append((score, representante))

    if not puntuados:
        return [], False

    mejor_score = max(score for score, _ in puntuados)
    candidatos = [recurso for score, recurso in puntuados if score == mejor_score]
    return candidatos, mejor_score == 1


def _regex_palabra(palabra: str) -> str:
    """Patrón para buscar `palabra` (ya normalizada) como palabra completa,
    tolerando el plural regular: "imac" coincide con "imacs", "camara" con
    "camaras", "dron" con "drones". El `\\b` a ambos lados se conserva a
    propósito (ver `_puntaje_nombre_en_texto`): lo único que se relaja es una
    "s"/"es" final opcional, así que "r" sigue sin coincidir con "r6". No
    cubre plurales irregulares (luz -> luces, interfaz -> interfaces)."""
    return rf"\b{re.escape(palabra)}(?:s|es)?\b"


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
    dice "...eos r6", empatando dos modelos que el texto sí distingue.

    Cada palabra tolera plural (`_regex_palabra`): antes "Tienen imacs?" no
    coincidía con "iMac 24 M3" y la consulta terminaba volcando todo el
    catálogo. El nombre completo se sigue exigiendo tal cual."""
    nombre_norm = _normalizar(nombre)
    palabras = [p for p in nombre_norm.split() if len(p) >= LONGITUD_MINIMA_PALABRA_CLAVE]
    score = sum(1 for p in palabras if re.search(_regex_palabra(p), texto_norm))
    if re.search(rf"\b{re.escape(nombre_norm)}\b", texto_norm):
        score += 10
    return score


def _puntaje_recurso_en_texto(recurso: Recurso, texto_norm: str) -> int:
    """`_puntaje_nombre_en_texto` + BONO_MODELO_COMPLETO si el texto trae el
    `modelo` completo (ej. "eos r6"). Hace falta porque lo que distingue un
    modelo de otro suele ser corto ("eos", "r6" no llegan a
    LONGITUD_MINIMA_PALABRA_CLAVE): sin esto "tienen la canon eos r6?"
    empataba las dos cámaras Canon y sus 5 lentes solo por "canon". El match
    es estricto (`\\b`, sin plural) para que "eos r" no coincida con "eos r6",
    mismo motivo que el nombre completo. Se exigen 4+ caracteres para que un
    modelo genérico ("Pro") no sume por una palabra suelta. Aparte de
    `_puntaje_nombre_en_texto` porque esa también valida nombres sueltos del
    LLM (`_resolver_recurso_para_reserva`), sin un Recurso a mano."""
    score = _puntaje_nombre_en_texto(recurso.nombre, texto_norm)
    modelo_norm = _normalizar(recurso.modelo or "").strip()
    if len(modelo_norm) >= LONGITUD_MINIMA_PALABRA_CLAVE and re.search(
        rf"\b{re.escape(modelo_norm)}\b", texto_norm
    ):
        score += BONO_MODELO_COMPLETO
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


def _palabras_clave_descripcion(descripcion: str | None) -> list[str]:
    """Palabras de una descripción que sirven para buscar: normalizadas, de
    LONGITUD_MINIMA_PALABRA_DESCRIPCION+ letras y sin palabras vacías. Se
    parte por todo lo que no sea letra o dígito (no por espacios, como el
    nombre) porque las descripciones traen puntuación ("Apple (Mac, laptop)")
    que dejaría "(mac," pegado. Sin repetidos, para no puntuar doble."""
    if not descripcion:
        return []
    palabras: list[str] = []
    for p in re.findall(r"[a-z0-9]+", _normalizar(descripcion)):
        if (
            len(p) >= LONGITUD_MINIMA_PALABRA_DESCRIPCION
            and p not in PALABRAS_VACIAS_DESCRIPCION
            and p not in palabras
        ):
            palabras.append(p)
    return palabras


def _descripciones_por_nombre() -> dict[str, str]:
    """Descripción de cada modelo activo (por `nombre`), juntando las de
    todas sus unidades. Cada placa es una fila con su propia `descripcion` y
    desde el dashboard es fácil llenarla en una unidad y no en las demás --
    mirando solo al representante de `_grupos_activos` (el de menor id), esa
    descripción podría no contar."""
    textos: dict[str, list[str]] = {}
    for r in Recurso.query.filter_by(estado=ESTADO_DISPONIBLE).order_by(Recurso.id).all():
        descripcion = (r.descripcion or "").strip()
        if not descripcion:
            continue
        lista = textos.setdefault(r.nombre, [])
        if descripcion not in lista:
            lista.append(descripcion)
    return {nombre: " ".join(lista) for nombre, lista in textos.items()}


def buscar_recursos_por_descripcion(texto: str) -> list[Recurso]:
    """Representantes de los modelos cuya `descripcion` mejor coincide con el
    texto -- mismo esquema que `buscar_recursos_en_texto`: gana el mejor
    puntaje (palabras clave de la descripción presentes en el texto, con
    tolerancia a plural) y los empates se devuelven todos.

    Es el ÚLTIMO nivel de búsqueda: los llamadores solo lo usan cuando ni el
    nombre ni la subcategoría encontraron nada. Por qué niveles y no un
    puntaje mezclado: la descripción es texto libre que se carga desde el
    dashboard y trae palabras genéricas. Sumada al puntaje del nombre,
    "¿tienen cámaras?" empataría el "Dron DJI Mavic" ("Dron con cámara 4K")
    con las cámaras de verdad, y cambiaría `es_match_debil` de respuestas que
    hoy salen bien. Por niveles, todo lo que se resuelve por nombre queda
    idéntico y la descripción solo rescata lo que antes no se encontraba
    ("¿tienen un mac para editar?" -> iMac y MacBook).

    Devuelve [] si empatan más de MAX_MODELOS_POR_DESCRIPCION modelos (las
    palabras que coincidieron eran demasiado genéricas)."""
    texto_norm = _normalizar(texto)
    descripciones = _descripciones_por_nombre()
    puntuados: list[tuple[int, Recurso]] = []
    for nombre, representante in _grupos_activos():
        score = sum(
            1
            for p in _palabras_clave_descripcion(descripciones.get(nombre))
            if re.search(_regex_palabra(p), texto_norm)
        )
        if score > 0:
            puntuados.append((score, representante))

    if not puntuados:
        return []
    mejor_score = max(score for score, _ in puntuados)
    candidatos = [recurso for score, recurso in puntuados if score == mejor_score]
    return candidatos if len(candidatos) <= MAX_MODELOS_POR_DESCRIPCION else []


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


def _contenidos_de_espacio(nombre_espacio: str) -> list[dict]:
    """Árbol de los equipos activos que viven dentro de un lugar
    (`recursos.espacio_id`) -- jerarquía DISTINTA e independiente de los
    accesorios por `padre_id` (ver `_accesorios_por_defecto` arriba y el
    comentario sobre `espacio_id` en `models.Recurso`). Cada nodo es
    `{"nombre", "unidades": [ids], "hijos": [nodos]}`, donde `hijos` son los
    hijos por `padre_id` de esas unidades.

    Se incluyen TODOS por defecto (ver paso "confirmar_contenidos_espacio"
    en `_avanzar_wizard`/`_continuar_reserva`), igual que los accesorios en
    bundle: quien reserva un lugar normalmente lo quiere con lo que tiene
    adentro, y si solo necesita el lugar, quita lo que le sobre.

    A diferencia de los accesorios, acá se guardan las UNIDADES concretas
    (`unidades`), no solo el modelo: "la sala con sus iMac" son las 3 iMac
    que están en esa sala, no una iMac cualquiera del inventario. Por eso
    varias unidades del mismo modelo se juntan en un solo nodo (se muestra
    como "iMac 24 M3 ×3") en vez de colapsar a una sola.

    Solo cuenta como RAÍZ un activo cuyo padre (por `padre_id`) NO esté
    también en este mismo espacio -- si el padre sí está, el hijo no se
    lista suelto porque ya aparece anidado bajo su padre. Si el padre no
    tiene lugar o vive en OTRO lugar, el hijo SÍ cuenta como raíz de este
    espacio. En la práctica esto casi no debería pasar porque mover de
    lugar a un padre arrastra a sus hijos (ver "Regla de propagación" en
    docs/ARQUITECTURA.md), pero un hijo reubicado a mano queda cubierto
    igual.

    Solo equipos (`tipo='equipo'`): un lugar dentro de otro lugar no se
    ofrece, y es a propósito -- no es un caso de uso de la CPA (decisión
    del 10/09/2026, ver docs/SESION_2026-09-10.md)."""
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
    raices = [r for r in contenidos if r.padre_id not in ids_contenidos]
    return _nodos_por_nombre(raices, set())


def _nodos_por_nombre(recursos: list[Recurso], vistos: set[int]) -> list[dict]:
    """Junta `recursos` en nodos por nombre (varias placas del mismo modelo
    = un nodo con varias `unidades`) y baja recursivamente a los hijos por
    `padre_id` de esas unidades. `vistos` evita repetir una unidad (y
    corta cualquier ciclo de `padre_id` mal cargado)."""
    nodos: dict[str, dict] = {}
    for r in recursos:
        if r.id in vistos:
            continue
        vistos.add(r.id)
        nodos.setdefault(r.nombre, {"nombre": r.nombre, "unidades": [], "hijos": []})["unidades"].append(r.id)

    for nodo in nodos.values():
        hijos = (
            Recurso.query.filter_by(estado=ESTADO_DISPONIBLE)
            .filter(Recurso.padre_id.in_(nodo["unidades"]))
            .order_by(Recurso.id)
            .all()
        )
        nodo["hijos"] = _nodos_por_nombre(hijos, vistos)
    return list(nodos.values())


def _items_de_contenidos(nodos: list[dict], por: str, ya_en_carrito: set[str]) -> list[dict]:
    """Aplana el árbol de `_contenidos_de_espacio` a ítems de carrito, en
    orden (cada padre seguido de sus hijos). `por` enlaza cada ítem con
    quien lo trajo -- el lugar para las raíces, el activo padre para sus
    hijos -- y es lo que usan la sangría (`_niveles`) y el borrado en
    cascada (`_quitar_del_carrito`). Se salta lo que ya esté en el carrito
    (y agrega a `ya_en_carrito` lo que sí suma)."""
    items: list[dict] = []
    for nodo in nodos:
        if nodo["nombre"] not in ya_en_carrito:
            items.append({"nombre_recurso": nodo["nombre"], "auto": True, "por": por, "unidades": nodo["unidades"]})
            ya_en_carrito.add(nodo["nombre"])
        items.extend(_items_de_contenidos(nodo["hijos"], nodo["nombre"], ya_en_carrito))
    return items


def listar_nombres_disponibles() -> list[str]:
    return [nombre for nombre, _ in _grupos_activos()]


def listar_terminos_catalogo() -> list[str]:
    """Vocabulario real del catálogo: nombres de modelo, subcategorías,
    categorías y marcas de los activos disponibles, y palabras clave de las
    descripciones. `routes/webhook.py` se lo pasa a `llm_client.clasificar_intent`
    como señal de dominio -- así el bot reconoce un pedido que nombra un
    equipo sin usar ningún verbo de reserva ("¿y el trípode Manfrotto?").

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

    # Palabras clave de las descripciones (ya sin palabras vacías, ver
    # `_palabras_clave_descripcion`): así el guard de dominio de
    # `llm_client._validar_intent_de_accion` también reconoce un pedido que
    # solo usa vocabulario de la descripción ("¿tienen un mac para editar?").
    # Sin esto, aunque el LLM lo clasifique bien, el guard no ve ninguna
    # palabra "de la CPA" y lo manda al menú de fallback. Solo las de
    # LONGITUD_MINIMA_PALABRA_CLAVE+ letras: `llm_client` descarta las más
    # cortas de todos modos. Van normalizadas, que es como las compara.
    for descripcion in _descripciones_por_nombre().values():
        for palabra in _palabras_clave_descripcion(descripcion):
            if len(palabra) >= LONGITUD_MINIMA_PALABRA_CLAVE and palabra not in terminos:
                terminos.append(palabra)
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


def _mensaje_catalogo(catalogo_url: str) -> str:
    """Respuesta cuando una consulta de disponibilidad no identifica ningún
    activo y hay `CATALOGO_URL`. A propósito NO dice "no encontré": casi
    siempre el usuario preguntó algo general o por un equipo que no sabemos
    nombrar, y lo útil es mostrarle dónde ver todo y cómo seguir. El enlace va
    solo en su línea para que WhatsApp lo convierta en link. `webhook.py` le
    pega después la oferta de reservar (`menu_service.activar_oferta`), así
    que el mensaje cierra con dos salidas claras: decir un nombre, o
    responder "sí" para arrancar una reserva."""
    return (
        "Te comparto el *catálogo completo* de equipos y espacios de la CPA, "
        "ahí puedes ver todo lo que tenemos:\n"
        f"{catalogo_url}\n\n"
        "Si ya sabes cuál te interesa, escríbeme su nombre (ej. \"cámara Sony\" "
        "o \"sala de edición\") y te digo si está libre."
    )


def evaluar_consulta_disponibilidad(texto_usuario: str) -> str:
    """Punto de entrada para el intent 'consultar_disponibilidad'. A
    diferencia de reservar, aquí el usuario quiere saber qué hay y qué no --
    así que ante varias coincidencias se listan todas con su estado, en vez
    de preguntar cuál de todas necesita.

    Busca por niveles, de la señal más confiable a la más ruidosa, y se queda
    con el primero que encuentre algo: nombre (+ modelo) -> subcategoría ->
    descripción. Cada nivel solo se mira si el anterior no dio nada, así que
    la descripción no cambia ninguna respuesta que ya salía por nombre (ver
    `buscar_recursos_por_descripcion`).

    Si ningún nivel identifica nada, en vez de volcar el estado de todo el
    inventario (en WhatsApp, un muro de texto) se manda el enlace al catálogo
    web (`CATALOGO_URL`, ver `_mensaje_catalogo`). Si esa variable está vacía
    -- todavía no hay página -- se conserva el listado completo de antes: sin
    enlace, es la única forma de que el usuario sepa qué existe."""
    ahora = datetime.now(timezone.utc)
    candidatos, es_match_debil = buscar_recursos_en_texto(texto_usuario)

    if candidatos:
        lineas = "\n".join(f"- {r.nombre}: {_estado_grupo(r.nombre, ahora)}" for r in candidatos)
        salvedad = "\n(si buscabas otro activo, dime el nombre completo)" if es_match_debil else ""
        return f"Esto es lo que tengo:\n{lineas}{salvedad}"

    subcategoria = _detectar_subcategoria_en_texto(texto_usuario)
    if subcategoria:
        grupos_subcategoria = _grupos_por_subcategoria(subcategoria)
        if grupos_subcategoria:
            lineas = "\n".join(
                f"- {_describir_modelo(nombre, rep)}: {_estado_grupo(nombre, ahora)}"
                for nombre, rep in grupos_subcategoria
            )
            return f"Esto es lo que tenemos en '{subcategoria}':\n{lineas}"

    por_descripcion = buscar_recursos_por_descripcion(texto_usuario)
    if por_descripcion:
        # Coincidir por descripción es más indirecto que por nombre, así que
        # siempre va con salvedad, igual que un match débil por nombre.
        lineas = "\n".join(f"- {r.nombre}: {_estado_grupo(r.nombre, ahora)}" for r in por_descripcion)
        return (
            f"Por lo que me cuentas, esto te puede servir:\n{lineas}\n"
            "(si buscabas otra cosa, dime el nombre del equipo o espacio)"
        )

    grupos = _grupos_activos()
    if not grupos:
        return "Por ahora no tengo activos cargados en el sistema. Contacta al equipo de la CPA."

    catalogo_url = (current_app.config.get("CATALOGO_URL") or "").strip()
    if catalogo_url:
        return _mensaje_catalogo(catalogo_url)

    lineas = "\n".join(f"- {nombre}: {_estado_grupo(nombre, ahora)}" for nombre, _ in grupos)
    return f"No identifiqué un activo específico, así que aquí tienes el estado de todo:\n{lineas}"


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


def _unidades_fijas_libres(ids: list[int], inicio: datetime, fin: datetime) -> list[Recurso]:
    """De las unidades concretas `ids` (ítems con `unidades`, ver
    `_items_de_contenidos`), las que siguen disponibles en inventario y sin
    choque de horario. A diferencia de `_unidad_libre_en_rango`, no se
    busca reemplazo en otra unidad del mismo modelo: el usuario pidió
    justo esas (las que están en el lugar que reservó)."""
    unidades = (
        Recurso.query.filter(Recurso.id.in_(ids), Recurso.estado == ESTADO_DISPONIBLE)
        .order_by(Recurso.id)
        .all()
    )
    return [u for u in unidades if _reserva_solapada(u.id, inicio, fin) is None]


def _resolver_disponibilidad_carrito(
    carrito: list[dict], inicio: datetime, fin: datetime
) -> tuple[list[dict], list[Recurso], list[dict]]:
    """Revisa la disponibilidad de cada ítem del carrito en el rango dado.
    Ya no distingue accesorio automático de elegido a mano: cualquier ítem
    sin unidad libre en ese horario -- sea el activo que el usuario pidió o
    un hijo que se incluyó por defecto -- se reporta en `no_disponibles`
    para que el llamador se lo diga al usuario y le pregunte qué hacer (ver
    paso "resolver_disponibilidad" en `_continuar_reserva`), en vez de
    decidir en silencio por él.

    Un ítem con `unidades` fijas (contenidos de un lugar) está disponible
    solo si lo están TODAS; si no, se reporta con cuáles sí quedan libres
    (`libres`), para que "quitar lo no disponible" deje las libres en vez
    de descartar el modelo completo. Cada entrada de `no_disponibles` es
    `{"nombre", "total", "libres": [ids]}`.

    Cuando `no_disponibles` viene vacío, `unidades` trae todas las unidades
    a reservar (una por ítem, o varias si el ítem tiene `unidades` fijas)."""
    disponibles: list[dict] = []
    unidades: list[Recurso] = []
    no_disponibles: list[dict] = []

    for item in carrito:
        fijas = item.get("unidades")
        if fijas:
            libres = _unidades_fijas_libres(fijas, inicio, fin)
            if len(libres) == len(fijas):
                disponibles.append(item)
                unidades.extend(libres)
            else:
                no_disponibles.append(
                    {"nombre": item["nombre_recurso"], "total": len(fijas), "libres": [u.id for u in libres]}
                )
            continue

        unidad = _unidad_libre_en_rango(item["nombre_recurso"], inicio, fin)
        if unidad is not None:
            disponibles.append(item)
            unidades.append(unidad)
        else:
            no_disponibles.append({"nombre": item["nombre_recurso"], "total": 1, "libres": []})

    return disponibles, unidades, no_disponibles


def _alternativas_disponibles(
    nombre_quitado: str, carrito: list[dict], inicio: datetime, fin: datetime
) -> list[str]:
    """Modelos de la MISMA clase que `nombre_quitado` (activo que quedó
    fuera del carrito por no estar disponible) que sí tienen una unidad
    libre en ese horario -- para ofrecérselos como reemplazo (ver
    `_ofrecer_alternativas`).

    "Misma clase" es categoria + subcategoria, no solo subcategoria: un Rode
    NTG3 (subcategoria 'microfono', categoria 'grabación audiovisual') y un
    Shure SM7B (subcategoria 'microfono', categoria 'producción de audio')
    son equipos distintos aunque compartan subcategoría -- uno es para
    cámara, el otro para cabina. Incluir `tipo` en la clase de paso le da a
    un ESPACIO (categoria/subcategoria en NULL) otros espacios como
    alternativa, sin necesitar un caso aparte.

    Solo se revisa el activo principal acá, no sus accesorios: si el
    reemplazo trae accesorios distintos (ej. otro cuerpo de cámara con otros
    lentes), esos se ofrecen y resuelven igual que cualquier activo nuevo
    (fold-in de `_avanzar_wizard`, paso "confirmar_accesorios") y su
    disponibilidad se revisa de nuevo en la pausa de "Confirmar" -- la
    garantía real contra choques sigue siendo el EXCLUDE de Postgres en
    `crear_reservas_grupo`, esto es solo para no ofrecer algo obviamente
    ocupado.

    El orden de salida es el de `_grupos_activos` (por tipo y nombre), no
    alfabético ni por disponibilidad."""
    # Sin filtrar por estado: si lo pasaron a mantenimiento, igual sirve
    # para saber su clase (categoria/subcategoria/tipo).
    referencia = Recurso.query.filter_by(nombre=nombre_quitado).order_by(Recurso.id).first()
    if referencia is None:
        return []
    clase = (referencia.tipo, _normalizar(referencia.categoria or ""), _normalizar(referencia.subcategoria or ""))
    en_carrito = {item["nombre_recurso"] for item in carrito}
    return [
        nombre
        for nombre, rep in _grupos_activos()
        if nombre != nombre_quitado
        and nombre not in en_carrito
        and (rep.tipo, _normalizar(rep.categoria or ""), _normalizar(rep.subcategoria or "")) == clase
        and _unidad_libre_en_rango(nombre, inicio, fin) is not None
    ]


def _quitar_del_carrito(carrito: list[dict], nombres: set[str]) -> list[dict]:
    """Quita del carrito los ítems nombrados y, en cascada, todo lo que
    colgaba de ellos por `por`: los accesorios de un equipo, los contenidos
    de un lugar y los hijos de esos contenidos."""
    quitados = set(nombres)
    hubo_cambio = True
    while hubo_cambio:
        hubo_cambio = False
        for item in carrito:
            if item["nombre_recurso"] not in quitados and item.get("por") in quitados:
                quitados.add(item["nombre_recurso"])
                hubo_cambio = True
    return [item for item in carrito if item["nombre_recurso"] not in quitados]


def crear_reservas_grupo(
    usuario_id: int,
    recursos: list[Recurso],
    inicio: datetime,
    fin: datetime,
    tipo_uso: str = "particular",
    nrc: str | None = None,
) -> tuple[list[Reserva] | None, str | None]:
    """Inserta una reserva por cada recurso en `recursos`, todas ligadas al
    mismo ReservaGrupo y en una sola transacción -- si alguna choca al
    confirmar (la restricción EXCLUDE de Postgres sobre (recurso_id, rango
    horario) para estado='confirmada', ver postgres/init/02-schema.sql), se
    revierte todo el grupo en vez de dejar una reserva parcial del carrito.
    El chequeo de disponibilidad antes de llamar a esta función (ver
    `_resolver_disponibilidad_carrito` / `_unidad_libre_en_rango`) es solo
    para dar un mensaje más claro, no la garantía de consistencia.

    `tipo_uso`/`nrc` van en el grupo, no en cada reserva por separado: es un
    dato de la reserva completa (particular o de una asignatura), no de
    cada activo dentro de ella."""
    grupo = ReservaGrupo(usuario_id=usuario_id, tipo_uso=tipo_uso, nrc=nrc if tipo_uso == "asignatura" else None)
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
    (ver los otros dos valores para construir ese mensaje). `candidatos`
    puede venir del nivel descripción (ver más abajo); en ese caso
    `recurso_resuelto` es siempre None."""
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

    if not candidatos:
        # Mismo tercer nivel que `evaluar_consulta_disponibilidad`: si el nombre
        # no dio nada, se intenta por la descripción ("quiero reservar un mac"
        # -> iMac y MacBook). Nunca se devuelve como recurso resuelto, aunque
        # sea uno solo: una palabra de la descripción es un indicio, no el
        # activo que el usuario nombró. Solo sirve para que `_iniciar_reserva`
        # pre-llene subcategoría/marca y el wizard muestre el menú de modelos
        # (si ahí queda un único modelo, `_avanzar_wizard` lo toma, pero
        # avisándolo: "Solo tenemos un modelo de ..., sigo con ese").
        por_descripcion = buscar_recursos_por_descripcion(texto_usuario)
        if por_descripcion:
            return None, por_descripcion, True

    if len(candidatos) == 1:
        return candidatos[0], candidatos, es_match_debil

    return None, candidatos, es_match_debil


def _nota_requiere_confirmacion(recurso: Recurso) -> str:
    """Línea aparte (no inline) para el mensaje de confirmación -- lleva el
    nombre del activo porque puede haber más de uno en el carrito que
    requiera confirmación, y sin el nombre no se sabría a cuál se refiere."""
    return (
        f"⚠ {recurso.nombre} requiere confirmación -- el equipo de la CPA te contactará para confirmar su préstamo."
        if recurso.requiere_confirmacion
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
        f"{numero_ninguno}. Dejar todos incluidos\n"
        f"{_DIVISOR_FACTURA}\n"
        "¿Quieres quitar alguno? Responde con los números separados por "
        f"coma (ej. \"1,2\"), o {numero_ninguno} para no quitar ninguno."
    )
    return mensaje


def _mensaje_confirmar_contenidos_espacio(nombre_espacio: str, incluidos: list[dict]) -> str:
    """Análogo a `_mensaje_confirmar_accesorios` pero para lo que contiene
    un LUGAR (`_contenidos_de_espacio`): todo ya quedó incluido en el
    carrito y se pregunta qué QUITAR. Diferencia con ese mensaje: muestra
    el anidado -- cada hijo sangrado debajo de su padre -- y numera TODAS
    las líneas, padres e hijos, para que se pueda quitar solo un hijo (ej.
    el brazo del micrófono) o un padre con todo lo que lleva debajo."""
    niveles = _niveles(incluidos)
    lineas = [
        f"{'    ' * nivel}{i}. {item['nombre_recurso']}{_sufijo_cantidad(item)}"
        for i, (item, nivel) in enumerate(zip(incluidos, niveles), start=1)
    ]
    numero_ninguno = len(incluidos) + 1
    lineas.append(f"{numero_ninguno}. Dejar todos incluidos")
    aviso_cascada = "\nSi quitas un activo, también se quita lo que va debajo de él." if any(niveles) else ""
    return (
        "*ESTE LUGAR INCLUYE*\n"
        f"{_DIVISOR_FACTURA}\n"
        f"*{nombre_espacio}*\n"
        "Incluye por defecto:\n"
        f"{chr(10).join(lineas)}\n"
        f"{_DIVISOR_FACTURA}\n"
        "¿Quieres quitar alguno? Responde con los números separados por "
        f"coma (ej. \"1,2\"), {numero_ninguno} para no quitar ninguno, o "
        "\"todos\" si solo necesitas el lugar."
        f"{aviso_cascada}"
    )


def _interpretar_seleccion_multiple(texto: str, cantidad: int) -> list[int] | None:
    """Interpreta la respuesta a un menú donde se puede elegir más de una
    opción a la vez por número (ej. qué accesorios por defecto quitar):
    "1", "2", "1,2", "1 y 2" -- devuelve los índices (0-based) elegidos.
    El número (cantidad + 1) es siempre la última línea del menú ("Dejar
    todos incluidos" -- ver `_mensaje_confirmar_accesorios`; antes decía
    "Ninguno", y se quitó porque confundía: parecía "no quiero ninguno de
    estos accesorios", lo contrario de lo que hace), así que también se puede
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
    if cantidad in numeros_int:  # 0-based: "Dejar todos incluidos" es cantidad + 1
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
            item["nombre_recurso"] + _sufijo_cantidad(item) + (" (incluido por defecto)" if item["auto"] else ""),
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
    ("cambiar_uso", "Cambiar particular / asignatura (NRC)"),
    ("cancelar", "Cancelar"),
]

OPCIONES_TIPO_USO = [
    ("particular", "Particular (uso personal)"),
    ("asignatura", "Para una asignatura (con NRC)"),
]

# El NRC siempre son exactamente 4 dígitos, que no formen parte de un número
# más largo (evita capturar, por ejemplo, parte de una fecha escrita junto al
# NRC). Mismo formato que exige el CHECK reserva_grupos_nrc_formato en
# postgres/init/02-schema.sql.
NRC_PATRON = re.compile(r"(?<!\d)(\d{4})(?!\d)")


def _extraer_nrc(texto: str) -> str | None:
    """Busca un NRC (4 dígitos) en el texto del usuario. None si no aparece
    ninguno."""
    coincidencia = NRC_PATRON.search(texto)
    return coincidencia.group(1) if coincidencia else None


_MENSAJE_PEDIR_NRC = (
    "¿Cuál es el NRC de la asignatura? Son 4 dígitos, ej. 1234. Si no "
    "es para una asignatura, escribe *particular*."
)
_MENSAJE_NRC_INVALIDO = (
    "No reconocí ese NRC. Son 4 dígitos, ej. 1234. Si no es para una "
    "asignatura, escribe *particular*."
)

OPCIONES_CONFIRMAR_FECHA = [
    ("si", "Sí, esa fecha está bien"),
    ("no", "No, quiero escribirla en formato dd/mm/aaaa"),
]

OPCIONES_CONFIRMAR_HORA = [
    ("si", "Sí, esa hora está bien"),
    ("no", "No, quiero escribirla en formato hh:mm am/pm"),
]


def _opciones_confirmar(wizard: dict) -> list[tuple[str, str]]:
    """OPCIONES_CONFIRMAR + una opción "Ver alternativas a X" por cada
    activo raíz que quedó fuera del carrito al resolver disponibilidad (ver
    `wizard["alternativas_para"]` y `_ofrecer_alternativas`). Se insertan
    justo después de "Agregar otro activo o espacio" -- son otra forma de
    agregar un activo, no de cambiar lo que ya está en el carrito. Sin `*`
    en la etiqueta: `_interpretar_opcion` matchea el texto literal, y un
    `*` de énfasis ahí rompería esa comparación."""
    opciones = list(OPCIONES_CONFIRMAR)
    extra = [(f"alternativas:{n}", f"Ver alternativas a {n}") for n in (wizard.get("alternativas_para") or [])]
    i = next(i for i, (v, _) in enumerate(opciones) if v == "agregar_item") + 1
    return opciones[:i] + extra + opciones[i:]


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
        # Primera pregunta de toda reserva nueva: particular o para una
        # asignatura (y, en ese caso, el NRC) -- va antes que el activo
        # porque no depende de qué se esté reservando. Si el texto inicial
        # ya traía un activo (ej. "reservar cámara sony"), `nombre_recurso`
        # se conserva en el wizard y sigue resolviéndose después de esto.
        if not wizard.get("tipo_uso"):
            wizard["opciones_actuales"] = OPCIONES_TIPO_USO
            wizard["paso"] = "tipo_uso"
            mensaje = _formatear_menu("¿Esta reserva es particular o para una asignatura?", OPCIONES_TIPO_USO)
            wizard["_mensaje_menu_actual"] = mensaje
            return _con_avisos(avisos, mensaje), wizard, False

        if wizard["tipo_uso"] == "asignatura" and not wizard.get("nrc"):
            wizard["opciones_actuales"] = None
            wizard["paso"] = "nrc"
            wizard["_mensaje_menu_actual"] = _MENSAJE_PEDIR_NRC
            return _con_avisos(avisos, _MENSAJE_PEDIR_NRC), wizard, False

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

            # Si el ítem recién resuelto es un LUGAR (tipo='espacio'), lo
            # que contiene (jerarquía por `espacio_id`, con los hijos por
            # `padre_id` de cada activo, ver `_contenidos_de_espacio`) se
            # incluye de una vez, igual que los accesorios de arriba, y se
            # le muestra al usuario por si quiere quitar algo -- p. ej. si
            # solo necesita el lugar. Si el lugar no tiene contenidos (o ya
            # están todos en el carrito), sigue de largo a pedir fecha.
            unidad_item = _grupo(nombre)
            if unidad_item and unidad_item[0].tipo == "espacio":
                contenidos = _items_de_contenidos(_contenidos_de_espacio(nombre), nombre, set(ya_en_carrito))
                if contenidos:
                    wizard["carrito"].extend(contenidos)
                    wizard["contenidos_incluidos"] = contenidos
                    wizard["opciones_actuales"] = None
                    wizard["paso"] = "confirmar_contenidos_espacio"
                    mensaje = _mensaje_confirmar_contenidos_espacio(nombre, contenidos)
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
            lineas_activos = _lineas_carrito(wizard["carrito"])
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
            lineas_activos = _lineas_carrito(wizard["carrito"])
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
            lineas_activos = _lineas_carrito(wizard["carrito"])
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
            opciones = _opciones_confirmar(wizard)
            wizard["opciones_actuales"] = opciones
            wizard["paso"] = "confirmar"
            mensaje = _mensaje_resumen(wizard, usuario, opciones)
            wizard["_mensaje_menu_actual"] = mensaje
            return _con_avisos(avisos, mensaje), wizard, False

        return "", wizard, True  # listo -- el usuario ya eligió "confirmar"


_DIVISOR_FACTURA = "――――――――――――――――――"


def _sufijo_cantidad(item: dict) -> str:
    """" ×3" cuando el ítem lleva varias unidades fijas (ej. las 3 iMac de
    una sala, ver `_items_de_contenidos`); vacío si es una sola."""
    cantidad = len(item.get("unidades") or [])
    return f" ×{cantidad}" if cantidad > 1 else ""


def _niveles(items: list[dict]) -> list[int]:
    """Profundidad de cada ítem dentro de `items` según la cadena de `por`:
    0 si nadie de la lista lo trajo, 1 si lo trajo otro ítem de la lista
    (accesorio de un equipo, activo de un lugar), 2 si lo trajo uno de
    nivel 1 (el brazo del micrófono de una cabina), etc."""
    por_de = {item["nombre_recurso"]: item.get("por") for item in items}
    niveles: list[int] = []
    for item in items:
        nivel, por, vistos = 0, item.get("por"), set()
        while por in por_de and por not in vistos:
            vistos.add(por)
            nivel += 1
            por = por_de[por]
        niveles.append(nivel)
    return niveles


def _formatear_item_factura(item: dict, nivel: int) -> str:
    """Una línea de activo para los mensajes de resumen/confirmación, estilo
    factura: el activo que el usuario pidió va en negrita al margen; lo que
    vino incluido con él (accesorios en bundle, activos de un lugar y sus
    hijos) va sangrado debajo, un nivel por generación, sin repetir el
    nombre del padre -- el wizard ya garantiza que un padre y lo que trae
    quedan contiguos en el carrito (ver `_mover_item_resuelto_al_carrito`,
    pasos "confirmar_accesorios" / "confirmar_contenidos_espacio"), así
    que la sangría alcanza para que se entienda a cuál pertenecen."""
    nombre = item["nombre_recurso"] + _sufijo_cantidad(item)
    if nivel == 0:
        return f"*{nombre}*"
    vineta = "•" if nivel == 1 else "◦"
    incluido = " _(incluido)_" if item["auto"] else ""
    return f"{'    ' * nivel}{vineta} {nombre}{incluido}"


def _lineas_carrito(carrito: list[dict]) -> str:
    return "\n".join(
        _formatear_item_factura(item, nivel) for item, nivel in zip(carrito, _niveles(carrito))
    )


def _linea_tipo_uso(wizard: dict) -> str:
    """Línea "Uso: ..." que acompaña a "Reservado por" tanto en el resumen
    como en la factura final -- ver `_mensaje_resumen` y
    `_crear_reserva_desde_wizard`."""
    if wizard.get("tipo_uso") == "asignatura":
        return f"Uso: Asignatura (NRC {wizard['nrc']})"
    return "Uso: Particular"


def _mensaje_resumen(wizard: dict, usuario: Usuario, opciones: list[tuple[str, str]]) -> str:
    inicio_utc = datetime.fromisoformat(wizard["inicio"])
    fin_utc = datetime.fromisoformat(wizard["fin"])
    inicio_local = inicio_utc.astimezone(ZONA_HORARIA_CPA)
    fin_local = fin_utc.astimezone(ZONA_HORARIA_CPA)
    lineas_activos = _lineas_carrito(wizard["carrito"])
    resumen = (
        "*RESUMEN DE RESERVA*\n"
        f"{_DIVISOR_FACTURA}\n"
        f"{lineas_activos}\n"
        f"{_DIVISOR_FACTURA}\n"
        f"Reservado por: {usuario.nombre}\n"
        f"{_linea_tipo_uso(wizard)}\n"
        f"Inicio: {_formatear_fecha_hora(inicio_local)}\n"
        f"Fin: {_formatear_fecha_hora(fin_local)}\n"
        f"{_DIVISOR_FACTURA}"
    )
    return resumen + "\n\n" + _formatear_menu("¿Qué quieres hacer?", opciones)


def _con_avisos(avisos: list[str], mensaje: str) -> str:
    if not avisos:
        return mensaje
    return "\n".join(avisos) + ("\n\n" + mensaje if mensaje else "")


OPCIONES_NO_DISPONIBLE = [
    ("quitar", "Quitar esos activos y volver al resumen"),
    ("cancelar_reserva", "Cancelar toda la reserva"),
]
# Misma acción, pero cuando quitar deja el carrito vacío: no hay resumen al que volver.
OPCIONES_NO_DISPONIBLE_CARRITO_VACIO = [
    ("quitar", "Quitar esos activos y elegir otro"),
    ("cancelar_reserva", "Cancelar toda la reserva"),
]


def _mensaje_no_disponibles(no_disponibles: list[dict], opciones: list[tuple[str, str]]) -> str:
    """Le dice al usuario exactamente cuáles activos (padre o hijo, da
    igual) ya no están disponibles en el horario elegido, y le pregunta qué
    hacer -- en vez de decidir en silencio (ver `_resolver_disponibilidad_carrito`
    y el paso "resolver_disponibilidad" en `_continuar_reserva`). Si solo
    parte de las unidades fijas de un ítem está ocupada, dice cuántas."""
    lineas = "\n".join(
        f"    • {nd['nombre']}"
        + (f" ({nd['total'] - len(nd['libres'])} de {nd['total']} unidades)" if nd["total"] > 1 else "")
        for nd in no_disponibles
    )
    verbo = "No está disponible" if len(no_disponibles) == 1 else "No están disponibles"
    mensaje = (
        "*DISPONIBILIDAD*\n"
        f"{_DIVISOR_FACTURA}\n"
        f"{verbo} en ese horario:\n"
        f"{lineas}\n"
        f"{_DIVISOR_FACTURA}"
    )
    return mensaje + "\n\n" + _formatear_menu("¿Qué quieres hacer?", opciones)


def _pausar_por_no_disponibles(wizard: dict, sesion: dict, no_disponibles: list[dict]) -> tuple[str, dict]:
    # Cualquier pausa por disponibilidad invalida un "Confirmar" anterior: el
    # carrito confirmado ya no se puede reservar tal cual, así que el usuario
    # tiene que volver a ver el resumen (y confirmar de nuevo) antes de que se
    # cree la reserva -- ver el bug original en docs/SESION_2026-09-10.md.
    wizard["confirmado"] = False

    # Se simula el retiro (sin mutar el carrito todavía, ver `_quitar_del_carrito`)
    # solo para decidir qué lista de opciones mostrar: si quitar lo no
    # disponible deja el carrito vacío, no tiene sentido ofrecer "volver al
    # resumen" -- no hay resumen al que volver.
    sin_libres = {nd["nombre"] for nd in no_disponibles if not nd["libres"]}
    opciones = (
        OPCIONES_NO_DISPONIBLE
        if _quitar_del_carrito(wizard["carrito"], sin_libres)
        else OPCIONES_NO_DISPONIBLE_CARRITO_VACIO
    )

    wizard["no_disponibles"] = no_disponibles
    wizard["opciones_actuales"] = opciones
    wizard["paso"] = "resolver_disponibilidad"
    mensaje = _mensaje_no_disponibles(no_disponibles, opciones)
    wizard["_mensaje_menu_actual"] = mensaje
    return mensaje, {**sesion, "reserva_wizard": wizard}


def _mensaje_alternativas(nombre_quitado: str, wizard: dict, opciones: list[tuple[str, str]]) -> str:
    inicio_local = datetime.fromisoformat(wizard["inicio"]).astimezone(ZONA_HORARIA_CPA)
    fin_local = datetime.fromisoformat(wizard["fin"]).astimezone(ZONA_HORARIA_CPA)
    mensaje = (
        "*ALTERNATIVAS DISPONIBLES*\n"
        f"{_DIVISOR_FACTURA}\n"
        f"En vez de: *{nombre_quitado}*\n"
        f"Inicio: {_formatear_fecha_hora(inicio_local)}\n"
        f"Fin: {_formatear_fecha_hora(fin_local)}\n"
        f"{_DIVISOR_FACTURA}"
    )
    return mensaje + "\n\n" + _formatear_menu("Estos están libres en ese horario. ¿Cuál quieres agregar a la reserva?", opciones)


def _ofrecer_alternativas(
    wizard: dict, usuario: Usuario, sesion: dict, nombre_quitado: str, avisos: list[str]
) -> tuple[str, dict]:
    """Busca reemplazos para `nombre_quitado` (ver `_alternativas_disponibles`)
    y arma el paso "elegir_alternativa", o si no hay ninguno, sigue el wizard
    normalmente (con el carrito como quedó) -- avisando en cualquier caso.

    Si no hay alternativas Y el carrito quedó vacío, se prueba con la
    siguiente raíz pendiente (`alternativas_para`) en vez de cortar de una:
    puede que quitar el trípode no tenga reemplazo pero sí la cámara. Si ya
    no queda ninguna raíz pendiente, se vuelve a pedir un activo desde cero
    (`resolviendo_item = True`), igual que cuando el carrito queda vacío en
    cualquier otro punto del wizard."""
    alternativas = _alternativas_disponibles(
        nombre_quitado, wizard["carrito"], datetime.fromisoformat(wizard["inicio"]), datetime.fromisoformat(wizard["fin"])
    )
    if not alternativas:
        pendientes = [n for n in (wizard.get("alternativas_para") or []) if n != nombre_quitado]
        wizard["alternativas_para"] = pendientes
        avisos.append(f"No encontré otro activo del mismo tipo que {nombre_quitado} libre en ese horario.")
        if not wizard["carrito"]:
            if pendientes:  # carrito vacío con varias raíces quitadas: probar la siguiente
                return _ofrecer_alternativas(wizard, usuario, sesion, pendientes[0], avisos)
            wizard.update(subcategoria=None, marca=None, nombre_recurso=None, resolviendo_item=True)
        wizard["opciones_actuales"] = None
        mensaje, wizard, listo = _avanzar_wizard(wizard, usuario)
        if listo:
            return _crear_reserva_desde_wizard(wizard, usuario, sesion)
        return _con_avisos(avisos, mensaje), {**sesion, "reserva_wizard": wizard}

    # Con carrito vacío no hay "resumen" al que volver -- se ofrece elegir
    # otro tipo de activo en su lugar (mismo criterio que
    # OPCIONES_NO_DISPONIBLE_CARRITO_VACIO).
    salida = ("volver", "Volver al resumen") if wizard["carrito"] else ("otro_tipo", "Elegir otro tipo de activo")
    opciones = [(n, n) for n in alternativas] + [salida]
    wizard["alternativa_de"] = nombre_quitado
    wizard["opciones_actuales"] = opciones
    wizard["paso"] = "elegir_alternativa"
    mensaje = _mensaje_alternativas(nombre_quitado, wizard, opciones)
    wizard["_mensaje_menu_actual"] = mensaje  # sin avisos, para re-mostrar ante opción inválida
    return _con_avisos(avisos, mensaje), {**sesion, "reserva_wizard": wizard}


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
    reservas, error = crear_reservas_grupo(
        usuario.id, unidades, inicio_utc, fin_utc,
        tipo_uso=wizard.get("tipo_uso") or "particular", nrc=wizard.get("nrc"),
    )
    if error is not None:
        return (
            "Uno de los activos se acaba de reservar justo mientras confirmabas. Intenta reservar de nuevo."
        ), sesion_limpia

    inicio_local = inicio_utc.astimezone(ZONA_HORARIA_CPA)
    fin_local = fin_utc.astimezone(ZONA_HORARIA_CPA)
    # dict.fromkeys: una sola nota por modelo aunque se reserven varias
    # unidades fijas del mismo (ej. las 3 iMac de una sala).
    notas_confirmacion = list(dict.fromkeys(_nota_requiere_confirmacion(unidad) for unidad in unidades if unidad.requiere_confirmacion))
    mensaje = (
        "*RESERVA CONFIRMADA*\n"
        f"{_DIVISOR_FACTURA}\n"
        f"{_lineas_carrito(carrito_final)}\n"
        f"{_DIVISOR_FACTURA}\n"
        f"Reservado por: {usuario.nombre}\n"
        f"{_linea_tipo_uso(wizard)}\n"
        f"Inicio: {_formatear_fecha_hora(inicio_local)}\n"
        f"Fin: {_formatear_fecha_hora(fin_local)}\n"
        f"{_DIVISOR_FACTURA}"
    )
    if notas_confirmacion:
        mensaje += "\n" + "\n".join(notas_confirmacion)
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
        "tipo_uso": None,
        "nrc": None,
        "fecha": None,
        "fecha_fin": None,
        "inicio": None,
        "fin": None,
        "confirmado": False,
        "opciones_actuales": None,
        "paso": None,
        # Raíces quitadas por no estar disponibles a las que todavía se les
        # puede ofrecer alternativa (ver `_ofrecer_alternativas`). Toda
        # lectura usa `wizard.get("alternativas_para") or []`, porque una
        # sesión vieja en Redis (de antes de este campo) no lo trae.
        "alternativas_para": [],
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
        # Solo si TODOS los candidatos tienen subcategoría: un espacio no
        # tiene, y antes simplemente se ignoraba al armar el set -- "quiero
        # reservar audio" (empata "Interfaz de audio Focusrite" con "Cabina de
        # audio 2") pre-llenaba "interfaz" y descartaba la cabina en silencio.
        # Con la búsqueda por descripción esa mezcla es más fácil ("algo para
        # podcast"), así que en ese caso se arranca en el menú general.
        if len(subcategorias_candidatos) == 1 and all(c.subcategoria for c in candidatos):
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

    if paso == "tipo_uso":
        nrc = _extraer_nrc(texto_usuario)
        if nrc:
            wizard["tipo_uso"] = "asignatura"
            wizard["nrc"] = nrc
        else:
            valor = _interpretar_opcion(texto_usuario, OPCIONES_TIPO_USO)
            if valor is None:
                return "No reconocí esa opción.\n\n" + (wizard.get("_mensaje_menu_actual") or ""), {
                    **sesion,
                    "reserva_wizard": wizard,
                }
            wizard["tipo_uso"] = valor
            if valor == "particular":
                wizard["nrc"] = None
        wizard["opciones_actuales"] = None
        mensaje, wizard, listo = _avanzar_wizard(wizard, usuario)
        if listo:
            return _crear_reserva_desde_wizard(wizard, usuario, sesion)
        return mensaje, {**sesion, "reserva_wizard": wizard}

    if paso == "nrc":
        if "particular" in _normalizar(texto_usuario):
            wizard["tipo_uso"] = "particular"
            wizard["nrc"] = None
        else:
            nrc = _extraer_nrc(texto_usuario)
            if nrc is None:
                return _MENSAJE_NRC_INVALIDO, {**sesion, "reserva_wizard": wizard}
            wizard["nrc"] = nrc
        mensaje, wizard, listo = _avanzar_wizard(wizard, usuario)
        if listo:
            return _crear_reserva_desde_wizard(wizard, usuario, sesion)
        return mensaje, {**sesion, "reserva_wizard": wizard}

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
            wizard["alternativas_para"] = []  # vio la oferta y eligió confirmar sin usarla
            wizard["confirmado"] = True
            return _crear_reserva_desde_wizard(wizard, usuario, sesion)

        if valor.startswith("alternativas:"):
            return _ofrecer_alternativas(wizard, usuario, sesion, valor.partition(":")[2], [])

        if valor == "agregar_item":
            # Nuevo activo desde cero: las alternativas ofrecidas eran para
            # lo que se quitó, no para esto que el usuario está agregando
            # aparte.
            wizard.update(subcategoria=None, marca=None, nombre_recurso=None, resolviendo_item=True, alternativas_para=[])
        elif valor == "quitar_item":
            return _iniciar_quitar_item(wizard, sesion)  # conserva alternativas_para
        elif valor == "cambiar_fecha":
            # Cambia el horario: las alternativas se calcularon para el
            # horario anterior, ya no valen.
            wizard.update(fecha=None, fecha_fin=None, inicio=None, fin=None, alternativas_para=[])
        elif valor == "cambiar_hora":
            wizard.update(inicio=None, fin=None, alternativas_para=[])
        elif valor == "cambiar_uso":
            wizard.update(tipo_uso=None, nrc=None)

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
        wizard["carrito"] = _quitar_del_carrito(wizard["carrito"], {incluidos[i] for i in indices})
        # No hace falta revisar disponibilidad acá -- ya se revisa en el
        # paso "hora_fin" (o al confirmar, si la hora ya estaba puesta por
        # venir de "agregar otro activo"), ver `_pausar_por_no_disponibles`.

        wizard["opciones_actuales"] = None
        mensaje, wizard, listo = _avanzar_wizard(wizard, usuario)
        if listo:
            return _crear_reserva_desde_wizard(wizard, usuario, sesion)
        return mensaje, {**sesion, "reserva_wizard": wizard}

    if paso == "confirmar_contenidos_espacio":
        incluidos = [item["nombre_recurso"] for item in wizard.get("contenidos_incluidos") or []]
        # La pregunta es "¿quieres quitar alguno?": un "sí" a secas no dice
        # cuáles. `_interpretar_seleccion_multiple` lo tomaría como "todos"
        # y dejaría solo el lugar, que no es lo que el usuario dijo.
        if _normalizar(texto_usuario.strip()) == "si":
            return "Dime cuáles, con sus números.\n\n" + (wizard.get("_mensaje_menu_actual") or ""), {
                **sesion,
                "reserva_wizard": wizard,
            }
        indices = _interpretar_seleccion_multiple(texto_usuario, len(incluidos))
        if indices is None:
            return "No reconocí esa opción.\n\n" + (wizard.get("_mensaje_menu_actual") or ""), {
                **sesion,
                "reserva_wizard": wizard,
            }

        wizard.pop("contenidos_incluidos", None)
        # Todo ya estaba en el carrito (ver el fold-in en `_avanzar_wizard`);
        # los índices son qué QUITAR, igual que en "confirmar_accesorios".
        # Quitar un activo arrastra lo que va debajo de él (sus hijos).
        wizard["carrito"] = _quitar_del_carrito(wizard["carrito"], {incluidos[i] for i in indices})
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

        # Defensa extra: una sesión pausada en Redis desde antes de este
        # cambio puede seguir con confirmado=True (ver `_pausar_por_no_disponibles`,
        # que ya lo pone en False para las nuevas).
        wizard["confirmado"] = False

        # "quitar" -- se quitan los activos no disponibles y, en cascada, lo
        # que colgaba de ellos (mismo criterio de arrastre que "Quitar un
        # activo de la reserva", ver paso "quitar_item_menu"). Un ítem con
        # unidades fijas que quedó ocupado solo en parte (ej. 1 de las 3
        # iMac de la sala) no se quita: se queda con las unidades libres.
        a_quitar: set[str] = set()
        for item in wizard["carrito"]:
            nd = next((nd for nd in no_disponibles if nd["nombre"] == item["nombre_recurso"]), None)
            if nd is None:
                continue
            if nd["libres"]:
                item["unidades"] = nd["libres"]
            else:
                a_quitar.add(item["nombre_recurso"])
        # Solo las RAÍCES (lo que el usuario eligió directamente, `por` es
        # None) reciben oferta de alternativa -- un lente Sony no le sirve a
        # un cuerpo Canon, y un reemplazo de un accesorio/contenido trae sus
        # propios accesorios/contenidos igual que cualquier activo nuevo, así
        # que no tiene sentido ofrecerlo suelto. Se ASIGNA la lista (no se
        # combina con la anterior): son las raíces quitadas en ESTA pausa.
        quitados_raiz = [it["nombre_recurso"] for it in wizard["carrito"] if it["nombre_recurso"] in a_quitar and not it.get("por")]
        wizard["carrito"] = _quitar_del_carrito(wizard["carrito"], a_quitar)
        wizard["alternativas_para"] = quitados_raiz
        avisos = ["Listo, quité de la reserva lo que no estaba disponible en ese horario."]

        wizard["opciones_actuales"] = None
        if not wizard["carrito"]:
            # Los ítems con unidades fijas (parciales) que se quedaron con
            # `unidades` libres no cuentan como carrito vacío -- ya se
            # filtraron arriba, esto solo se cumple si todo se quitó.
            if quitados_raiz:
                return _ofrecer_alternativas(wizard, usuario, sesion, quitados_raiz[0], avisos)
            wizard["resolviendo_item"] = True
        mensaje, wizard, listo = _avanzar_wizard(wizard, usuario)
        if listo:
            return _crear_reserva_desde_wizard(wizard, usuario, sesion)
        return _con_avisos(avisos, mensaje), {**sesion, "reserva_wizard": wizard}

    if paso == "elegir_alternativa":
        valor = _interpretar_opcion(texto_usuario, wizard.get("opciones_actuales") or [])
        if valor is None:
            return "No reconocí esa opción.\n\n" + (wizard.get("_mensaje_menu_actual") or ""), {
                **sesion,
                "reserva_wizard": wizard,
            }
        nombre_quitado = wizard.pop("alternativa_de", None)
        wizard["opciones_actuales"] = None
        if valor == "otro_tipo":
            wizard.update(subcategoria=None, marca=None, nombre_recurso=None, resolviendo_item=True, alternativas_para=[])
        elif valor != "volver":
            # Mismo camino que "agregar_item" con un activo ya resuelto: el
            # fold-in de `_avanzar_wizard` lo mete al carrito con sus
            # accesorios / contenidos por defecto.
            wizard["alternativas_para"] = [n for n in (wizard.get("alternativas_para") or []) if n != nombre_quitado]
            wizard.update(subcategoria=None, marca=None, nombre_recurso=valor, resolviendo_item=True)
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

        wizard["carrito"] = _quitar_del_carrito(wizard["carrito"], {valor})
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
    # accesorios", no "cancela toda la reserva". Mismo motivo para
    # "confirmar_contenidos_espacio": ahí "no" responde a "¿quieres quitar
    # alguno?" (ver `_interpretar_seleccion_multiple`) y significa "deja
    # todo lo del lugar incluido", no "cancela toda la reserva" -- si se
    # olvida acá, "no" cancelaría todo el wizard en vez de seguir a fecha.
    palabras_cancelar = PALABRAS_CANCELAR
    if wizard is not None and wizard.get("paso") in (
        "confirmar_fecha_interpretada",
        "confirmar_hora_inicio_interpretada",
        "confirmar_hora_fin_interpretada",
        "confirmar_accesorios",
        "confirmar_contenidos_espacio",
        # A propósito NO se agrega "elegir_alternativa" acá: ese paso no
        # tiene ninguna opción que empiece por "no" (sus opciones son
        # nombres de activos, "Volver al resumen" o "Elegir otro tipo de
        # activo"), así que "no" sigue significando cancelar, como en
        # cualquier otro paso. Si se agregara, un "no" del usuario dejaría
        # de cancelar y caería en el `_interpretar_opcion` de ese paso,
        # donde el match es por substring de la etiqueta normalizada: "no"
        # es substring de "canon" ("Cámara Canon EOS R6"), así que
        # "no" terminaría "eligiendo" esa cámara en vez de cancelar.
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
