"""
Disco y transiciones de estado de las actas de entrega/devolución.

Módulo puro a propósito, igual que `acta_pdf.py`: nada de `flask.request` ni
`jsonify` acá -- `routes/dashboard.py` se limita a leer el multipart, llamar
a estas funciones y armar la respuesta HTTP. Así se puede probar el guardado
en disco o una transición de estado sin un contexto de request armado.

Todo lo de acá trabaja sobre `grupo_id` (la reserva completa), igual que
`acta_pdf.generar_acta`: un acta es una sola firma para todos los activos
del carrito, no una por línea.
"""

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import current_app

from app.extensions import db
from app.models import Reserva, ReservaActa
from app.recursos_service import ESTADO_DEVUELTA_TARDE, ESTADO_ENTREGADA

# Tamaño máximo de un acta subida o firmada -- mismo valor que
# Config.ACTAS_MAX_BYTES (10 MiB por defecto). Se repite acá el default para
# que este módulo también funcione fuera de un `current_app` con config
# completa (ej. un script suelto de verificación).
MAX_BYTES = 10 * 1024 * 1024

# Los `momento` posibles de un acta -- deben calzar con el CHECK
# reserva_actas_momento_valido (postgres/init/02-schema.sql).
MOMENTO_ENTREGA = "entrega"
MOMENTO_DEVOLUCION = "devolucion"

# Acta de DEVOLUCIÓN con un equipo sin marcar: la línea se cierra como
# 'no_devuelta' y el activo pasa a 'no_devuelto' (fuera del catálogo del bot,
# que solo ofrece 'disponible'). Solo la LACS lo saca de ahí, desde Activos.
ESTADO_NO_DEVUELTA = "no_devuelta"
RECURSO_NO_DEVUELTO = "no_devuelto"


def _actas_dir() -> Path:
    return Path(current_app.config.get("ACTAS_DIR", "/app/actas"))


def _max_bytes() -> int:
    return int(current_app.config.get("ACTAS_MAX_BYTES", MAX_BYTES))


def ruta_grupo(grupo_id: int) -> Path:
    """Carpeta donde viven las actas de un grupo (creada si hace falta). Una
    subcarpeta por `grupo_id` para no mezclar los archivos de miles de
    reservas en un solo directorio plano."""
    carpeta = _actas_dir() / str(grupo_id)
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta


def _nombre_archivo(carpeta: Path, momento: str, origen: str) -> str:
    """`YYYYmmddTHHMMSS-momento-origen.pdf`, con sufijo `-2`, `-3`... si dos
    firmas caen en el mismo segundo (dos actas seguidas del mismo grupo, ej.
    entrega y devolución casi inmediatas en una prueba)."""
    base = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{momento}-{origen}"
    nombre = f"{base}.pdf"
    contador = 2
    while (carpeta / nombre).exists():
        nombre = f"{base}-{contador}.pdf"
        contador += 1
    return nombre


def guardar(
    grupo_id: int,
    momento: str,
    origen: str,
    datos: bytes,
    anclas: dict | None,
    firmado_por: str | None,
    nota: str | None = None,
) -> ReservaActa:
    """Escribe `datos` a disco y arma la fila `ReservaActa` (sin `commit`:
    lo hace el endpoint, junto con el cambio de estado, para que un fallo de
    cualquiera de los dos revierta el conjunto).

    Nunca sobrescribe un archivo existente -- cada firma o subida es un
    archivo nuevo, que es el histórico que pidió el usuario. No hace falta
    `db.session.add()` explícito porque el modelo va directo a la sesión al
    construirlo (SQLAlchemy no lo requiere, pero se agrega igual por
    claridad)."""
    if datos[:5] != b"%PDF-":
        raise ValueError("El archivo no es un PDF válido.")
    if len(datos) > _max_bytes():
        raise ValueError(f"El archivo supera el tamaño máximo permitido ({_max_bytes()} bytes).")

    carpeta = ruta_grupo(grupo_id)
    nombre = _nombre_archivo(carpeta, momento, origen)
    ruta = carpeta / nombre
    ruta.write_bytes(datos)

    acta = ReservaActa(
        grupo_id=grupo_id,
        momento=momento,
        origen=origen,
        # Relativa a ACTAS_DIR (no absoluta): así el volumen se puede montar
        # en otra ruta sin que las filas viejas queden apuntando mal.
        archivo=f"{grupo_id}/{nombre}",
        sha256=hashlib.sha256(datos).hexdigest(),
        bytes=len(datos),
        anclas=anclas,
        firmado_por=firmado_por,
        nota=nota,
    )
    db.session.add(acta)
    return acta


def ultima(grupo_id: int) -> ReservaActa | None:
    """El acta más reciente de un grupo -- `id` como desempate de
    `creado_en` (mismo segundo es posible, ver `_nombre_archivo`)."""
    return (
        ReservaActa.query.filter_by(grupo_id=grupo_id)
        .order_by(ReservaActa.creado_en.desc(), ReservaActa.id.desc())
        .first()
    )


def _fin_aware(reserva: Reserva) -> datetime:
    """`fin` puede llegar naive de una fila vieja -- mismo patrón defensivo
    que el resto del módulo (ver esta_retrasada)."""
    return reserva.fin if reserva.fin.tzinfo else reserva.fin.replace(tzinfo=timezone.utc)


def momento_siguiente(reservas: list[Reserva], ahora: datetime | None = None) -> str | None:
    """Qué acta falta por registrar para este grupo, o None si ya está
    cerrada (nada que firmar: todo cancelada/denegada/completada/
    devuelta_tarde/no_devuelta, o un no_show cuya ventana ya cerró). Si
    hubiera líneas en ambos estados a la vez (mezcla rara, ej. edición
    manual concurrente), gana 'entrega' -- es el paso que lógicamente va
    primero.

    Un grupo en 'no_show' TODAVÍA puede firmar la ENTREGA mientras alguna de
    sus líneas 'no_show' tenga `fin > ahora`: es la "entrega tardía" -- el
    estudiante llegó después del margen de no-show (NO_SHOW_MARGEN_MIN) pero
    sigue dentro de su horario, así que se le entrega igual (ver
    `aplicar_transicion`, que marca `entrega_tardia=True`). Si esa ventana ya
    cerró, no queda nada por firmar.

    `ahora` es opcional, igual que en `esta_retrasada`: quien lo llama puede
    fijarlo una sola vez por request en vez de pedir la hora del sistema por
    cada grupo."""
    estados = {r.estado for r in reservas}
    if "confirmada" in estados:
        return MOMENTO_ENTREGA
    if "no_show" in estados:
        ahora = ahora or datetime.now(timezone.utc)
        if any(_fin_aware(r) > ahora for r in reservas if r.estado == "no_show"):
            return MOMENTO_ENTREGA
        return None
    if ESTADO_ENTREGADA in estados:
        return MOMENTO_DEVOLUCION
    return None


def esta_retrasada(reserva: Reserva, ahora: datetime | None = None) -> bool:
    """El equipo sigue en la calle después de su hora de fin -- derivado, no
    un estado persistido (ver comentario de `estado` en models.py). `fin`
    puede llegar naive de una fila vieja, igual que en `acta_pdf._normalizar_utc`.

    `ahora` es opcional: quien serializa listados grandes (ver
    `routes/dashboard._serializar_reserva`) lo calcula una sola vez por
    request y lo pasa, en vez de pedir la hora del sistema en cada fila."""
    if reserva.estado != ESTADO_ENTREGADA:
        return False
    ahora = ahora or datetime.now(timezone.utc)
    fin = reserva.fin if reserva.fin.tzinfo else reserva.fin.replace(tzinfo=timezone.utc)
    return fin < ahora


def _filtrar_marcadas(
    objetivo: list[Reserva], ids: set[int] | None, verbo: str, permitir_vacio: bool = False
) -> tuple[list[Reserva], str | None]:
    """Las líneas de `objetivo` que el administrador marcó en el paso
    "Equipos" del diálogo de firma. `ids=None` (acta subida, o cliente viejo)
    = todas, el comportamiento de antes. Un id fuera de `objetivo` se rechaza:
    es justo lo que impide devolver un equipo que nunca se entregó.
    `permitir_vacio`: en la devolución, nada marcado = no devolvió ninguno."""
    if ids is None:
        return objetivo, None
    if not ids and not permitir_vacio:
        return [], f"Marca al menos un equipo {verbo}."
    if ids - {r.id for r in objetivo}:
        return [], f"Hay equipos marcados que no se pueden registrar como {verbo}s."
    return [r for r in objetivo if r.id in ids], None


def aplicar_transicion(
    reservas: list[Reserva], momento: str, ids: set[int] | None = None
) -> tuple[int, str | None]:
    """Cambia el estado de las líneas afectadas por registrar el acta de
    `momento` (sin `commit`, igual que `guardar`). Devuelve (cuántas líneas
    cambiaron, error legible o None).

    'entrega': las líneas 'confirmada' O 'no_show' pasan a 'entregada'. Una
    línea que venía de 'no_show', o cuya entrega ya pasó el margen de no-show
    aunque siga 'confirmada', se marca además con `entrega_tardia=True`
    (ver el comentario de esa columna en el esquema) -- el estudiante llegó
    tarde pero dentro de su ventana, y el horario NO se extiende por eso:
    `fin` no se toca. Si TODAS las líneas objetivo ya tienen `fin <= ahora`
    (la ventana cerró para todas), no se registra nada.
    'devolucion': todas las líneas 'entregada' pasan a 'completada', salvo
    que ya haya pasado `fin` -- ahí pasan a 'devuelta_tarde'. La comparación
    se hace una vez por línea (cada activo puede tener un `fin` propio si se
    editó horario por separado, aunque en la práctica todas comparten
    horario de grupo).

    `ids` (opcional): las líneas que el administrador marcó como
    entregadas/devueltas. En la entrega, las NO marcadas pasan a 'cancelada'
    (no se las llevó, y así no quedan pidiendo otra acta de entrega); en la
    devolución, las no marcadas pasan a 'no_devuelta' y su activo a
    'no_devuelto' (ver ESTADO_NO_DEVUELTA); nada queda pendiente. Una
    devolución sin nada marcado es válida (no devolvió ninguno)."""
    ahora = datetime.now(timezone.utc)
    cambiadas = 0

    if momento == MOMENTO_ENTREGA:
        pendientes = [r for r in reservas if r.estado in ("confirmada", "no_show")]
        if not pendientes:
            return 0, "Esta reserva no tiene activos por entregar."
        objetivo, error = _filtrar_marcadas(pendientes, ids, "entregado")
        if error:
            return 0, error
        if all(_fin_aware(r) <= ahora for r in objetivo):
            return 0, "La ventana de esta reserva ya terminó; no se puede registrar la entrega."
        # Tardía también si ya pasó el margen aunque la línea siga
        # 'confirmada': el barrido de no-show pudo no haber corrido todavía
        # (contenedor caído) o la LACS revirtió el no_show a mano.
        margen = timedelta(minutes=current_app.config.get("NO_SHOW_MARGEN_MIN", 30))
        for r in objetivo:
            inicio = r.inicio if r.inicio.tzinfo else r.inicio.replace(tzinfo=timezone.utc)
            if r.estado == "no_show" or ahora > inicio + margen:
                r.entrega_tardia = True
            r.estado = ESTADO_ENTREGADA
            cambiadas += 1
        for r in pendientes:
            if r not in objetivo:
                r.estado = "cancelada"
        return cambiadas, None

    if momento == MOMENTO_DEVOLUCION:
        afuera = [r for r in reservas if r.estado == ESTADO_ENTREGADA]
        if not afuera:
            return 0, "Esta reserva no tiene activos por devolver."
        objetivo, error = _filtrar_marcadas(afuera, ids, "devuelto", permitir_vacio=True)
        if error:
            return 0, error
        for r in afuera:
            if r in objetivo:
                fin = r.fin if r.fin.tzinfo else r.fin.replace(tzinfo=timezone.utc)
                r.estado = ESTADO_DEVUELTA_TARDE if ahora > fin else "completada"
            else:
                r.estado = ESTADO_NO_DEVUELTA
                r.recurso.estado = RECURSO_NO_DEVUELTO
            cambiadas += 1
        return cambiadas, None

    return 0, f"Momento de acta desconocido: {momento!r}."
