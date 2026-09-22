"""
Ciclo de avisos de WhatsApp y no-show automático de las reservas.

Una reserva 'confirmada' que nadie recoge pasa sola a 'no_show'
NO_SHOW_MARGEN_MIN minutos después de `inicio`, con tres avisos por el camino
(ver AVISO_PREVIO / AVISO_HORA / AVISO_NO_SHOW). Dos cosas empujan el mismo
`barrer()`:

  - El "ticker": un hilo de fondo dentro de este mismo contenedor flask-app
    (ver app/ticker.py) que despierta cada BARRIDO_INTERVALO_SEG segundos.
  - El barrido perezoso: `routes/dashboard.api_reservas` (el listado que el
    dashboard consulta cada 30 s) llama a `barrer()` de paso.

No se metió APScheduler, Celery ni un contenedor nuevo a propósito: un hilo
daemon + un lock de Redis alcanza para el volumen de esta app, y evita sumar
una pieza más de infraestructura que mantener.

Módulo puro al estilo de `actas_service.py`: nada de `flask.request` ni
`jsonify` acá -- solo `current_app` para leer config y loguear. Así se puede
probar `barrer()` desde un `python -c` suelto en el contenedor, sin armar un
request completo.

Dos capas de protección contra avisos duplicados (gunicorn corre con
--workers 2, así que hay DOS procesos con su propio ticker, más el barrido
perezoso disparándose desde cualquier request):
  - `intentar_lock`: un lock de Redis (NX+EX) que hace que, en el caso
    normal, solo UN proceso corra el barrido por intervalo. Es la
    OPTIMIZACIÓN -- ahorra trabajo repetido, no es la garantía de fondo.
  - El UNIQUE (grupo_id, tipo) de `reserva_avisos` (ver el esquema): la
    garantía REAL. Si dos workers igual llegan a intentar el mismo aviso (el
    lock venció a mitad de una corrida larga, o Redis estaba caído), el
    segundo INSERT revienta con IntegrityError y ese worker hace rollback y
    sigue -- nunca se manda el mismo aviso dos veces.

Corte de frescura: el barrido tiene que poder correr después de que el
contenedor estuvo caído un rato sin mandar avisos falsos. Un "te recuerdo que
recoges en 30 min" que sale dos horas tarde no es un aviso tardío, es
información incorrecta. Por eso un aviso solo se ENVÍA si su momento ideal
cayó dentro de los últimos AVISO_VENTANA_MIN minutos; si ya venció, la fila
de `reserva_avisos` se crea igual con `enviado=false, motivo='vencido'`, para
no volver a intentarlo nunca en el siguiente tick. El cambio de estado a
'no_show' SÍ se aplica aunque el aviso esté vencido -- el estado es un hecho
(el activo se liberó), el mensaje es una cortesía con fecha de vencimiento.

Propiedad útil, y a propósito: como la fila (grupo_id, 'no_show') queda para
siempre en `reserva_avisos`, si la LACS revierte a mano un 'no_show' a
'confirmada' desde el dashboard, el barrido NO lo vuelve a marcar ni
re-notifica -- el UNIQUE se lee como "este aviso ya se resolvió una vez para
este grupo", no como "todavía falta mandarlo".
"""

from datetime import datetime, timedelta, timezone

from flask import current_app
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app import guardrails, recursos_service, whatsapp_client
from app.extensions import db, redis_client
from app.models import Reserva, ReservaAviso, Usuario

AVISO_PREVIO = "previo"
AVISO_HORA = "hora"
AVISO_NO_SHOW = "no_show"

# Debe calzar con el CHECK reserva_avisos_tipo_valido (postgres/init/02-schema.sql).
_TIPOS_AVISO = (AVISO_PREVIO, AVISO_HORA, AVISO_NO_SHOW)

# Clave fija: un solo barrido a la vez alcanza, no hace falta un lock por
# grupo -- barrer() ya procesa todos los grupos pendientes en una corrida.
_LOCK_KEY = "avisos_reservas:barrido"


def avisar_usuario(usuario: Usuario, texto: str, intent: str) -> bool:
    """Le escribe al estudiante sin un mensaje suyo al que responder (ver
    whatsapp_client.jid_de) y lo deja en la auditoría como cualquier mensaje
    del bot: así el ack de entrega de Baileys (/webhook/ack) lo encuentra por
    `mensaje_id`. Devuelve si Baileys aceptó el envío.

    Vive acá, no en routes/dashboard.py, porque el ticker corre en un hilo de
    fondo (ver app/ticker.py) y no debe importar un módulo de blueprint.
    `dashboard._avisar_estudiante` queda como una delegación de una línea
    para no tocar a sus tres llamadores actuales."""
    enviado, mensaje_id = whatsapp_client.enviar_mensaje_texto(whatsapp_client.jid_de(usuario), texto)
    guardrails.registrar_interaccion(
        usuario.telefono_wa, None, intent, texto, permitido=True,
        mensaje_id=mensaje_id,
        estado_envio="pendiente" if enviado else "error_http",
    )
    return enviado


def _momentos(inicio: datetime, fin: datetime, cfg) -> dict[str, datetime]:
    """Los tres instantes ideales de aviso de un grupo, ya con tz.

    `no_show` = min(inicio + NO_SHOW_MARGEN_MIN, fin): el `min` importa -- una
    reserva corta (ej. 15 min) no puede tener su deadline de no-show DESPUÉS
    de que la reserva ya terminó."""
    limite_no_show = inicio + timedelta(minutes=cfg.get("NO_SHOW_MARGEN_MIN", 30))
    return {
        AVISO_PREVIO: inicio - timedelta(minutes=cfg.get("AVISO_PREVIO_MIN", 30)),
        AVISO_HORA: inicio,
        AVISO_NO_SHOW: min(limite_no_show, fin),
    }


def intentar_lock(segundos: int) -> bool:
    """True si este proceso ganó la carrera para correr el barrido en este
    tick (lock de Redis, NX+EX). Es la OPTIMIZACIÓN para que, en el caso
    normal, los dos workers de gunicorn (o el ticker y el barrido perezoso
    del listado) no dupliquen trabajo -- la garantía real de no duplicar
    AVISOS es el UNIQUE (grupo_id, tipo) de `reserva_avisos` (ver `barrer`).
    Redis caído devuelve False y loguea un warning; nunca lanza -- un tick
    saltado no es motivo para tumbar el ticker ni el listado del dashboard."""
    try:
        return bool(redis_client.set(_LOCK_KEY, "1", nx=True, ex=segundos))
    except Exception as exc:  # noqa: BLE001 - Redis caído no debe tumbar nada
        current_app.logger.warning("Redis no disponible para el lock de avisos_reservas: %s", exc)
        return False


def _texto_aviso(tipo: str, reservas: list[Reserva]) -> str:
    if tipo == AVISO_PREVIO:
        return recursos_service.mensaje_aviso_previo(reservas)
    if tipo == AVISO_HORA:
        return recursos_service.mensaje_hora_recogida(reservas)
    return recursos_service.mensaje_no_show(reservas)


def _con_tz(momento: datetime) -> datetime:
    """`inicio`/`fin` pueden llegar naive de una fila vieja (mismo patrón
    defensivo que actas_service.esta_retrasada)."""
    return momento if momento.tzinfo else momento.replace(tzinfo=timezone.utc)


def barrer(ahora: datetime | None = None) -> dict:
    """Recorre las reservas 'confirmada' con algún aviso pendiente, manda los
    que tocan (o los marca 'vencido' si ya no tiene sentido mandarlos) y pasa
    a 'no_show' los grupos cuyo aviso AVISO_NO_SHOW ya se resolvió.

    Devuelve conteos para el log y para las pruebas:
    `{"grupos": n, "avisos_enviados": n, "avisos_vencidos": n, "no_show": n}`.
    """
    ahora = ahora or datetime.now(timezone.utc)
    cfg = current_app.config
    aviso_ventana = timedelta(minutes=cfg.get("AVISO_VENTANA_MIN", 120))

    conteos = {"grupos": 0, "avisos_enviados": 0, "avisos_vencidos": 0, "no_show": 0}

    # El aviso más temprano posible es AVISO_PREVIO_MIN minutos antes de
    # `inicio` -- filtrar por ese límite superior (con el índice
    # idx_reservas_estado_inicio) evita traer reservas futuras que todavía no
    # tienen ningún aviso pendiente. No hace falta límite inferior: una
    # reserva vieja que sigue en 'confirmada' es justo la que hay que barrer,
    # y en cuanto se le aplique 'no_show' sale de este filtro para siempre.
    limite_superior = ahora + timedelta(minutes=cfg.get("AVISO_PREVIO_MIN", 30))
    lineas = (
        Reserva.query.options(joinedload(Reserva.usuario), joinedload(Reserva.recurso))
        .filter(Reserva.estado == "confirmada", Reserva.inicio <= limite_superior)
        .all()
    )
    if not lineas:
        return conteos

    por_grupo: dict[int, list[Reserva]] = {}
    for linea in lineas:
        por_grupo.setdefault(linea.grupo_id, []).append(linea)
    conteos["grupos"] = len(por_grupo)

    # Una sola query contra reserva_avisos para TODO el lote -- una query por
    # grupo sería N+1 en el camino caliente (este barrido corre cada 60 s).
    existentes = {
        (a.grupo_id, a.tipo)
        for a in ReservaAviso.query.filter(ReservaAviso.grupo_id.in_(por_grupo.keys())).all()
    }

    for grupo_id, reservas_grupo in por_grupo.items():
        inicio_grupo = _con_tz(min(r.inicio for r in reservas_grupo))
        fin_grupo = _con_tz(max(r.fin for r in reservas_grupo))
        momentos = _momentos(inicio_grupo, fin_grupo, cfg)

        for tipo in _TIPOS_AVISO:
            if (grupo_id, tipo) in existentes:
                continue
            momento = momentos[tipo]
            if momento > ahora:
                continue  # todavía no le toca a este aviso

            vencido = (ahora - momento) > aviso_ventana
            if vencido:
                aviso = ReservaAviso(grupo_id=grupo_id, tipo=tipo, enviado=False, motivo="vencido")
                conteos["avisos_vencidos"] += 1
            else:
                usuario = reservas_grupo[0].usuario
                enviado = (
                    avisar_usuario(usuario, _texto_aviso(tipo, reservas_grupo), f"aviso_{tipo}")
                    if usuario is not None else False
                )
                aviso = ReservaAviso(
                    grupo_id=grupo_id, tipo=tipo, enviado=enviado,
                    motivo=None if enviado else "error_envio",
                )
                if enviado:
                    conteos["avisos_enviados"] += 1

            db.session.add(aviso)
            if tipo == AVISO_NO_SHOW:
                for linea in reservas_grupo:
                    linea.estado = "no_show"
                conteos["no_show"] += len(reservas_grupo)

            try:
                db.session.commit()
            except IntegrityError:
                # El otro worker ganó la carrera para esta fila -- el UNIQUE
                # (grupo_id, tipo) es la garantía real, no el lock de Redis
                # (ver el comentario de la tabla en el esquema). Se sigue con
                # el resto del lote, no se propaga.
                db.session.rollback()

    return conteos
