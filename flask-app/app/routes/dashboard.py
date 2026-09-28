"""
API del dashboard interno de monitoreo para el equipo de la LACS: estado de
los servicios (Postgres, Redis, Baileys/WhatsApp, LLM local), código QR de
vinculación y métricas básicas de uso. Protegido con autenticación básica
(DASHBOARD_USER / DASHBOARD_PASSWORD) porque expone el QR de vinculación
de WhatsApp, que no debería poder escanear cualquiera con acceso a la red.

El frontend (React/shadcn, ver dashboard-frontend/ en la raíz del repo)
corre en su propio contenedor Nginx, que sirve los estáticos y hace proxy
de /dashboard/api/ hacia acá -- este blueprint ya no sirve HTML ni assets.
"""

import base64
import io
import json
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import qrcode
import requests
from flask import Blueprint, Response, current_app, jsonify, request, send_file
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app import (
    acta_pdf,
    actas_service,
    avisos_reservas,
    estado_bot,
    fechas_bloqueadas_service,
    recursos_service,
    registro_service,
    usuarios_service,
)
from app.extensions import db, redis_client
from app.models import (
    AuditoriaBot,
    CalendarioReglas,
    FechaExcepcion,
    Recurso,
    Reserva,
    ReservaActa,
    ReservaGrupo,
    ReservaGrupoIntegrante,
    Usuario,
)

bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


@bp.before_request
def _requiere_auth():
    password_esperada = current_app.config["DASHBOARD_PASSWORD"]
    if not password_esperada:
        current_app.logger.warning(
            "DASHBOARD_PASSWORD no configurada: /dashboard queda deshabilitado."
        )
        return Response("Dashboard no configurado (falta DASHBOARD_PASSWORD).", 503)

    auth = request.authorization
    usuario_ok = auth and secrets.compare_digest(auth.username or "", current_app.config["DASHBOARD_USER"])
    password_ok = auth and secrets.compare_digest(auth.password or "", password_esperada)

    if not (usuario_ok and password_ok):
        return Response(
            "Autenticación requerida.",
            401,
            {"WWW-Authenticate": 'Basic realm="Dashboard LACS"'},
        )


# --- Chequeos de estado de cada servicio ------------------------------------

def _check_postgres() -> dict:
    try:
        db.session.execute(text("SELECT 1"))
        return {"ok": True, "detalle": "conectado"}
    except Exception as exc:  # noqa: BLE001 - queremos capturar cualquier fallo de DB
        return {"ok": False, "detalle": str(exc)}


def _check_redis() -> dict:
    try:
        redis_client.ping()
        return {"ok": True, "detalle": "conectado"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detalle": str(exc)}


def _consultar_baileys() -> dict | None:
    base_url = current_app.config["WHATSAPP_API_URL"].rstrip("/")
    try:
        resp = requests.get(f"{base_url}/status", timeout=3)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return None


def _check_baileys(estado: dict | None) -> dict:
    if estado is None:
        return {"ok": False, "detalle": "no responde"}
    return {
        "ok": True,
        "detalle": estado.get("status", "desconocido"),
        "whatsapp_conectado": estado.get("status") == "open",
        "numero": estado.get("phoneNumber"),
        "buffer_atascado": estado.get("eventBufferAtascado"),
    }


def _check_llm() -> dict:
    base_url = current_app.config["LLM_BASE_URL"].rstrip("/")
    api_key = current_app.config.get("LLM_API_KEY")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        resp = requests.get(f"{base_url}/v1/models", headers=headers, timeout=3)
        resp.raise_for_status()
        return {"ok": True, "detalle": "conectado"}
    except requests.RequestException as exc:
        return {"ok": False, "detalle": str(exc)}


# --- Rutas -------------------------------------------------------------------

@bp.route("/api/status")
def api_status():
    return jsonify(
        {
            "postgres": _check_postgres(),
            "redis": _check_redis(),
            "baileys": _check_baileys(_consultar_baileys()),
            "llm": _check_llm(),
            "flask": {"ok": True, "detalle": "respondiendo"},
            "chequeado_en": datetime.utcnow().isoformat() + "Z",
        }
    )


@bp.route("/api/seguro/fecha-minima")
def api_fecha_minima_seguro():
    """Fecha mínima de inicio HOY para una reserva que sale de Barranquilla
    (5.º día hábil, festivos de Colombia) -- la usa el formulario de edición
    del dashboard (ver ReservaDetalle.tsx) para advertir sin bloquear."""
    hoy = datetime.now(timezone.utc).astimezone(recursos_service.ZONA_HORARIA_LACS).date()
    return jsonify({
        "dias_habiles": recursos_service.DIAS_MINIMOS_TRAMITE_SEGURO,
        "hoy": hoy.isoformat(),
        "fecha_minima": recursos_service.fecha_minima_sale_de_barranquilla(hoy).isoformat(),
    })


# --- Excepciones: lista negra de fechas -------------------------------------

def _fecha_civil(crudo) -> date:
    """'YYYY-MM-DD' -> date. ValueError si no es texto o no se puede parsear."""
    if not isinstance(crudo, str):
        raise ValueError
    return date.fromisoformat(crudo)


@bp.route("/api/excepciones")
def api_excepciones():
    """Días relevantes (reglas, festivos, excepciones) entre ?desde y ?hasta
    (YYYY-MM-DD, incluidos). Sin parámetros: el año en curso (hora LACS)."""
    hoy = datetime.now(timezone.utc).astimezone(recursos_service.ZONA_HORARIA_LACS).date()
    try:
        desde = _fecha_civil(request.args.get("desde") or f"{hoy.year}-01-01")
        hasta = _fecha_civil(request.args.get("hasta") or f"{hoy.year}-12-31")
    except ValueError:
        return jsonify({"error": "'desde' y 'hasta' deben tener formato YYYY-MM-DD."}), 400
    if hasta < desde or (hasta - desde).days > fechas_bloqueadas_service.MAX_DIAS_RANGO:
        return jsonify({"error": f"Rango inválido (máximo {fechas_bloqueadas_service.MAX_DIAS_RANGO} días)."}), 400
    return jsonify({
        "reglas": fechas_bloqueadas_service.serializar_reglas(fechas_bloqueadas_service.obtener_reglas()),
        "dias": fechas_bloqueadas_service.dias_en_rango(desde, hasta),
    })


@bp.route("/api/excepciones/reglas")
def api_reglas_excepciones():
    return jsonify(fechas_bloqueadas_service.serializar_reglas(fechas_bloqueadas_service.obtener_reglas()))


@bp.route("/api/excepciones/reglas", methods=["PUT"])
def api_guardar_reglas_excepciones():
    datos = request.get_json(silent=True) or {}
    dias, festivos = datos.get("dias_semana"), datos.get("festivos_co")
    if not isinstance(dias, list) or not all(
        isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in dias
    ):
        return jsonify({"error": "'dias_semana' debe ser una lista de enteros de 0 (lunes) a 6 (domingo)."}), 400
    if len(set(dias)) == 7:
        return jsonify({"error": "No se pueden bloquear los 7 días de la semana."}), 400
    if not isinstance(festivos, bool):
        return jsonify({"error": "'festivos_co' debe ser true o false."}), 400
    reglas = CalendarioReglas.query.get(1) or CalendarioReglas(id=1)
    reglas.dias_semana_bloqueados = sorted(set(dias))   # reasignar, no mutar
    reglas.festivos_co = festivos
    reglas.actualizado_en = datetime.now(timezone.utc)
    db.session.add(reglas)
    db.session.commit()
    return jsonify(fechas_bloqueadas_service.serializar_reglas(reglas))


@bp.route("/api/excepciones/fechas", methods=["POST"])
def api_guardar_excepcion():
    """Upsert de una excepción para un día o un rango (desde..hasta, incluido)."""
    datos = request.get_json(silent=True) or {}
    try:
        desde = _fecha_civil(datos.get("desde"))
        hasta = _fecha_civil(datos.get("hasta")) if datos.get("hasta") else desde
    except ValueError:
        return jsonify({"error": "Las fechas deben tener formato YYYY-MM-DD."}), 400
    if hasta < desde or (hasta - desde).days > 365:
        return jsonify({"error": "Rango inválido (máximo un año)."}), 400
    tipo = datos.get("tipo")
    if tipo not in fechas_bloqueadas_service.TIPOS_EXCEPCION:
        return jsonify({"error": "'tipo' debe ser 'bloqueo' o 'desbloqueo'."}), 400
    motivo = datos.get("motivo")
    if motivo is not None and not isinstance(motivo, str):
        return jsonify({"error": "'motivo' debe ser texto."}), 400
    motivo = (motivo or "").strip() or None
    if motivo and len(motivo) > 200:
        return jsonify({"error": "El motivo no puede superar 200 caracteres."}), 400

    existentes = {e.fecha: e for e in FechaExcepcion.query.filter(
        FechaExcepcion.fecha >= desde, FechaExcepcion.fecha <= hasta).all()}
    ahora = datetime.now(timezone.utc)
    d = desde
    while d <= hasta:
        exc = existentes.get(d) or FechaExcepcion(fecha=d, creado_en=ahora)
        exc.tipo, exc.motivo, exc.actualizado_en = tipo, motivo, ahora
        db.session.add(exc)
        d += timedelta(days=1)
    db.session.commit()
    return jsonify({"dias": fechas_bloqueadas_service.dias_en_rango(desde, hasta)})


@bp.route("/api/excepciones/fechas/<fecha>", methods=["DELETE"])
def api_eliminar_excepcion(fecha):
    """Quita la excepción: el día vuelve a seguir las reglas. Devuelve el estado resultante."""
    try:
        d = _fecha_civil(fecha)
    except ValueError:
        return jsonify({"error": "Fecha inválida (YYYY-MM-DD)."}), 400
    exc = FechaExcepcion.query.get(d)
    if exc is None:
        return jsonify({"error": "Ese día no tiene una excepción."}), 404
    db.session.delete(exc)
    db.session.commit()
    return jsonify(fechas_bloqueadas_service.describir_fecha(d))


@bp.route("/api/qr")
def api_qr():
    estado = _consultar_baileys() or {}
    conectado = estado.get("status") == "open"

    qr_imagen = None
    qr_texto = estado_bot.obtener_qr()
    if not conectado and qr_texto:
        buffer = io.BytesIO()
        qrcode.make(qr_texto).save(buffer, format="PNG")
        qr_imagen = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()

    return jsonify(
        {
            "conectado": conectado,
            "numero": estado.get("phoneNumber"),
            "qr_imagen": qr_imagen,
        }
    )


@bp.route("/api/metricas")
def api_metricas():
    desde = datetime.utcnow() - timedelta(days=30)

    reservas_por_dia = (
        db.session.query(
            func.date(Reserva.inicio).label("dia"),
            func.count(Reserva.id).label("total"),
        )
        .filter(Reserva.inicio >= desde)
        .group_by(func.date(Reserva.inicio))
        .order_by(func.date(Reserva.inicio))
        .all()
    )

    total_bloqueadas_30d = (
        db.session.query(func.count(AuditoriaBot.id))
        .filter(AuditoriaBot.creado_en >= desde, AuditoriaBot.permitido.is_(False))
        .scalar()
    )

    ultimas_interacciones = (
        db.session.query(AuditoriaBot)
        .order_by(AuditoriaBot.creado_en.desc())
        .limit(20)
        .all()
    )

    return jsonify(
        {
            "reservas_por_dia": [{"dia": str(f.dia), "total": f.total} for f in reservas_por_dia],
            "total_reservas_30d": sum(f.total for f in reservas_por_dia),
            "total_bloqueadas_30d": total_bloqueadas_30d or 0,
            "ultimas_interacciones": [
                {
                    "telefono_wa": i.telefono_wa,
                    "intent": i.intent_detectado,
                    "permitido": i.permitido,
                    "motivo_bloqueo": i.motivo_bloqueo,
                    "estado_envio": i.estado_envio,
                    "creado_en": i.creado_en.isoformat() if i.creado_en else None,
                }
                for i in ultimas_interacciones
            ],
        }
    )


# Campos que el panel de Usuarios puede modificar. No incluye `id`,
# `registro_completo` ni `creado_en` -- el registro se completa vía el flujo
# de WhatsApp (ver registro_service.py), no desde el dashboard.
_CAMPOS_EDITABLES_USUARIO = {
    "nombre",
    "telefono_wa",
    "codigo_uninorte",
    "cedula",
    "rol",
    "activo",
}

# Ver comentario de Usuario.rol en postgres/init/02-schema.sql.
_ROLES_VALIDOS = {"estudiante", "staff", "admin"}


def _serializar_usuario(u: Usuario) -> dict:
    return {
        "id": u.id,
        "nombre": u.nombre,
        "telefono_wa": u.telefono_wa,
        "codigo_uninorte": u.codigo_uninorte,
        "cedula": u.cedula,
        "rol": u.rol,
        "activo": u.activo,
        "registro_completo": u.registro_completo,
        "creado_en": u.creado_en.isoformat() if u.creado_en else None,
    }


@bp.route("/api/usuarios")
def api_usuarios():
    # Solo usuarios que terminaron el flujo de registro por WhatsApp -- deja
    # afuera contactos a medio registrar y el usuario dummy de seed.py
    # (registro_completo=False), que no es un registro real del bot.
    # ?incluir_incompletos=1 los incluye igual -- lo usa el selector de
    # cliente al crear una reserva desde el dashboard (ver
    # api_crear_reserva), donde un cliente nuevo o a medio registrar
    # también debe poder elegirse.
    consulta = Usuario.query
    if request.args.get("incluir_incompletos") != "1":
        consulta = consulta.filter_by(registro_completo=True)
    usuarios = consulta.order_by(Usuario.creado_en.desc()).all()
    return jsonify([_serializar_usuario(u) for u in usuarios])


@bp.route("/api/usuarios/<int:usuario_id>", methods=["PATCH"])
def api_actualizar_usuario(usuario_id):
    usuario = Usuario.query.get_or_404(usuario_id)
    datos = request.get_json(silent=True) or {}

    campos_desconocidos = set(datos) - _CAMPOS_EDITABLES_USUARIO
    if campos_desconocidos:
        return jsonify({"error": f"Campos no editables: {', '.join(sorted(campos_desconocidos))}"}), 400

    if "rol" in datos and datos["rol"] not in _ROLES_VALIDOS:
        return jsonify({"error": f"El rol debe ser uno de: {', '.join(sorted(_ROLES_VALIDOS))}."}), 400

    if "telefono_wa" in datos and not datos["telefono_wa"]:
        return jsonify({"error": "El teléfono no puede quedar vacío."}), 400

    for campo, valor in datos.items():
        setattr(usuario, campo, valor)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Ya existe otro usuario con ese teléfono."}), 400

    return jsonify(_serializar_usuario(usuario))


@bp.route("/api/usuarios/<int:usuario_id>", methods=["DELETE"])
def api_eliminar_usuario(usuario_id):
    # No se borra en cascada: un usuario con reservas se desactiva en vez de
    # eliminarse, para no perder el historial de uso de los activos.
    usuario = Usuario.query.get_or_404(usuario_id)
    if Reserva.query.filter_by(usuario_id=usuario.id).first() is not None:
        return jsonify({
            "error": "El usuario tiene reservas y no se puede eliminar. Desactívalo en su lugar."
        }), 409

    ReservaGrupo.query.filter_by(usuario_id=usuario.id).delete()
    db.session.delete(usuario)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "No se puede eliminar el usuario porque tiene información asociada."}), 400

    return "", 204


def _serializar_reserva(r: Reserva, ahora: datetime | None = None) -> dict:
    """`ahora` es opcional y se recibe calculado UNA sola vez por el
    llamador (nunca `datetime.now()` por fila): este serializador corre en
    listados grandes (hasta 2000 filas, ver `api_reservas`) y un reloj por
    fila es gasto de CPU evitable en un contenedor que se quiere liviano."""
    ahora = ahora or datetime.now(timezone.utc)
    return {
        "id": r.id,
        "usuario_id": r.usuario_id,
        "usuario_nombre": r.usuario.nombre if r.usuario else None,
        "usuario_telefono": r.usuario.telefono_wa if r.usuario else None,
        "usuario_codigo_uninorte": r.usuario.codigo_uninorte if r.usuario else None,
        "usuario_rol": r.usuario.rol if r.usuario else None,
        "recurso_id": r.recurso_id,
        "recurso_nombre": r.recurso.nombre if r.recurso else None,
        "recurso_tipo": r.recurso.tipo if r.recurso else None,
        "grupo_id": r.grupo_id,
        "tipo_uso": r.grupo.tipo_uso if r.grupo else "particular",
        "nrc": r.grupo.nrc if r.grupo else None,
        "modalidad": r.grupo.modalidad if r.grupo else "individual",
        "tamano_grupo": r.grupo.tamano_grupo if r.grupo else None,
        # r.grupo.integrantes ya viene ordenado por `orden` (ver
        # ReservaGrupo.integrantes en models.py) -- integrantes[0] es
        # siempre quien reserva.
        "integrantes": [i.codigo_uninorte for i in r.grupo.integrantes] if r.grupo else [],
        # ¿Los activos salen de Barranquilla? None = no se preguntó (reserva
        # anterior a esta pregunta, ver postgres/init/02-schema.sql).
        "sale_de_barranquilla": r.grupo.sale_de_barranquilla if r.grupo else None,
        # Fecha mínima de inicio calculada desde la creación (5 días
        # hábiles, festivos de Colombia) -- solo si sale de Barranquilla.
        "fecha_minima_seguro": (
            recursos_service.fecha_minima_sale_de_barranquilla(
                (r.creado_en if r.creado_en.tzinfo else r.creado_en.replace(tzinfo=timezone.utc))
                .astimezone(recursos_service.ZONA_HORARIA_LACS).date()
            ).isoformat()
            if r.grupo and r.grupo.sale_de_barranquilla and r.creado_en else None
        ),
        # Nota libre y opcional de la reserva (dato del grupo completo, no
        # de cada activo). None = sin nota. Ver ReservaGrupo.nota.
        "nota": r.grupo.nota if r.grupo else None,
        "inicio": r.inicio.isoformat() if r.inicio else None,
        "fin": r.fin.isoformat() if r.fin else None,
        "estado": r.estado,
        # Derivado, no una columna (ver comentario de Reserva.estado en
        # models.py): el equipo sigue afuera y ya pasó `fin`.
        "retrasada": actas_service.esta_retrasada(r, ahora),
        # Bandera persistida (no derivada, a diferencia de "retrasada"): el
        # estudiante recogió después del margen de no-show pero dentro de su
        # ventana. Ver Reserva.entrega_tardia y actas_service.aplicar_transicion.
        "entrega_tardia": bool(r.entrega_tardia),
        "canal_origen": r.canal_origen,
        "creado_en": r.creado_en.isoformat() if r.creado_en else None,
    }


def _fecha_iso(crudo) -> datetime:
    """Convierte un texto ISO-8601 en datetime con tz (UTC si no la trae).

    Acepta el sufijo "Z" que produce Date.toISOString() en el navegador, que
    fromisoformat() no entiende en Python < 3.11. Lanza TypeError si no es
    texto y ValueError si no se puede parsear.
    """
    if not isinstance(crudo, str):
        raise TypeError("se esperaba una fecha en texto")
    valor = datetime.fromisoformat(crudo.replace("Z", "+00:00"))
    return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)


def _fecha_query(nombre: str) -> datetime | None:
    """Lee un parámetro ISO-8601 (?desde=/?hasta=). None si no viene."""
    crudo = request.args.get(nombre)
    return _fecha_iso(crudo) if crudo else None


# Ventanas del selector temporal del panel de Reservas. None = sin recorte.
# El recorte es solo hacia atrás (`fin > now() - delta`): lo que todavía no ha
# terminado sale SIEMPRE, sea cual sea la ventana, porque el panel se usa sobre
# todo para vigilar lo que viene.
VENTANAS_RESERVAS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "1a": timedelta(days=365),
    "todas": None,
}
# Válvula de seguridad de "todas": no es un recorte esperado, es un tope para
# que el panel avise en vez de mentir si el histórico se dispara.
MAX_RESERVAS_VENTANA = 5000


def _barrido_perezoso():
    """Empuja el no-show automático desde el propio listado de reservas (el
    ticker de app/ticker.py es la vía principal, pero este endpoint lo pide
    el dashboard cada 30 s y es una segunda oportunidad barata de que corra,
    ej. justo después de levantar el contenedor). `intentar_lock` con
    BARRIDO_INTERVALO_SEG como TTL hace que como máximo corra una vez por
    minuto aunque lleguen decenas de requests de varias pestañas abiertas a
    la vez -- el mismo lock que usa el ticker, así que compiten por el mismo
    turno, no se duplican.

    Nunca debe romper la respuesta del listado: un barrido fallido (Postgres
    con hipo, lo que sea) se loguea y se ignora, el listado se sirve igual."""
    try:
        intervalo = current_app.config.get("BARRIDO_INTERVALO_SEG", 60)
        if avisos_reservas.intentar_lock(intervalo):
            avisos_reservas.barrer()
    except Exception:  # noqa: BLE001 - el listado tiene que responder igual
        current_app.logger.exception("Error en el barrido perezoso de avisos_reservas")


@bp.route("/api/reservas")
def api_reservas():
    _barrido_perezoso()
    # Sesiones de uso (reservas) de cada usuario, con TRES modos mutuamente
    # excluyentes:
    # - Sin parámetros: últimas 500 por fecha de creación (sub-panel
    #   "Sesiones" dentro de Usuarios). Devuelve una LISTA.
    # - ?desde/?hasta: las que *solapan* ese rango, ordenadas por inicio -- es
    #   lo que necesita el calendario, porque una reserva de varios días debe
    #   aparecer aunque empiece antes del periodo visible. Devuelve una LISTA.
    # - ?ventana=24h|7d|30d|1a|todas: recorte por ventana temporal para el
    #   panel de Reservas (ver VENTANAS_RESERVAS). Devuelve un OBJETO, no una
    #   lista, porque además de las filas hace falta decir si se truncó -- los
    #   otros dos modos siguen devolviendo lista para no romper a sus
    #   consumidores (fetchSesiones, fetchReservasRango).
    # Trae usuario/recurso con joinedload para no hacer una query por fila.
    try:
        desde, hasta = _fecha_query("desde"), _fecha_query("hasta")
    except ValueError:
        return jsonify({"error": "Los parámetros 'desde' y 'hasta' deben ser fechas ISO-8601."}), 400

    ventana = request.args.get("ventana")
    if ventana is not None:
        if desde or hasta:
            return jsonify({"error": "'ventana' no se puede combinar con 'desde'/'hasta'."}), 400
        if ventana not in VENTANAS_RESERVAS:
            return jsonify({
                "error": f"'ventana' debe ser una de: {', '.join(sorted(VENTANAS_RESERVAS))}."
            }), 400

    consulta = Reserva.query.options(
        joinedload(Reserva.usuario), joinedload(Reserva.recurso),
        joinedload(Reserva.grupo).joinedload(ReservaGrupo.integrantes),
    )

    # Una sola vez por request, no un now() por fila dentro de
    # `_serializar_reserva` (ver su comentario): este endpoint puede devolver
    # hasta 2000 filas.
    ahora = datetime.now(timezone.utc)

    if ventana is not None:
        delta = VENTANAS_RESERVAS[ventana]
        if delta is not None:
            consulta = consulta.filter(Reserva.fin > ahora - delta)
        # Pedimos una fila de más que el tope para poder distinguir "hay
        # exactamente el tope" de "hay más y se truncó" sin un COUNT aparte.
        reservas = consulta.order_by(Reserva.inicio.desc()).limit(MAX_RESERVAS_VENTANA + 1).all()
        truncado = len(reservas) > MAX_RESERVAS_VENTANA
        reservas = reservas[:MAX_RESERVAS_VENTANA]
        return jsonify({
            "reservas": [_serializar_reserva(r, ahora) for r in reservas],
            "truncado": truncado,
            "total": len(reservas),
            "ventana": ventana,
        })

    if desde or hasta:
        if hasta:
            consulta = consulta.filter(Reserva.inicio < hasta)
        if desde:
            consulta = consulta.filter(Reserva.fin > desde)
        reservas = consulta.order_by(Reserva.inicio.asc()).limit(2000).all()
    else:
        reservas = consulta.order_by(Reserva.creado_en.desc()).limit(500).all()

    return jsonify([_serializar_reserva(r, ahora) for r in reservas])


@bp.route("/api/reservas/ocupados")
def api_reservas_ocupados():
    """IDs de recurso ocupados (confirmada/pendiente) en un rango horario --
    lo usa el formulario "Nueva reserva" del dashboard para avisar en vivo
    si un activo que ya estaba marcado deja de estar libre mientras se
    completa el formulario."""
    inicio_crudo, fin_crudo = request.args.get("inicio"), request.args.get("fin")
    if not inicio_crudo or not fin_crudo:
        return jsonify({"error": "Los parámetros 'inicio' y 'fin' son obligatorios."}), 400
    try:
        inicio, fin = _fecha_iso(inicio_crudo), _fecha_iso(fin_crudo)
    except (TypeError, ValueError):
        return jsonify({"error": "Los parámetros 'inicio' y 'fin' deben ser fechas ISO-8601."}), 400

    return jsonify({"ocupados": sorted(recursos_service.ids_ocupados_en_rango(inicio, fin))})


# Dos mensajes del mismo número separados por más de esto son conversaciones
# distintas: es el hueco que usamos para recortar el hilo que terminó en una
# reserva, ya que auditoria_bot no guarda a qué reserva pertenece cada mensaje.
HUECO_CONVERSACION = timedelta(minutes=30)
# Topes de seguridad para que un número muy activo no devuelva su historial entero.
VENTANA_MAX_CONVERSACION = timedelta(hours=24)
MAX_MENSAJES_CONVERSACION = 100


def _serializar_mensaje_conversacion(m: AuditoriaBot, ancla_id: int) -> dict:
    return {
        "id": m.id,
        "entrante": m.mensaje_entrante,
        "respuesta": m.respuesta_enviada,
        "intent": m.intent_detectado,
        "permitido": m.permitido,
        "motivo_bloqueo": m.motivo_bloqueo,
        "estado_envio": m.estado_envio,
        "creado_en": m.creado_en.isoformat() if m.creado_en else None,
        "es_ancla": m.id == ancla_id,
    }


@bp.route("/api/reservas/<int:reserva_id>/conversacion")
def api_conversacion_reserva(reserva_id):
    """Reconstruye el hilo de WhatsApp que dio lugar a una reserva.

    No hay FK entre `Reserva` y `auditoria_bot`: el único nexo posible es
    `telefono_wa` + tiempo. Partimos de `reserva.creado_en` como "ancla" (el
    instante en que se confirmó la reserva) y expandimos hacia atrás y hacia
    adelante mientras los mensajes consecutivos del mismo teléfono queden
    separados por menos de `HUECO_CONVERSACION`: un hueco mayor significa que
    la persona volvió a escribir por otro motivo, ya en otra conversación.
    Se llama solo al abrir el modal de una sesión (nunca junto al listado de
    `/api/reservas`, que puede traer 500 filas), así que el costo de esta
    heurística no afecta la carga normal del panel.
    """
    reserva = Reserva.query.options(joinedload(Reserva.usuario)).get(reserva_id)
    if reserva is None:
        return jsonify({"error": "La reserva indicada no existe."}), 404

    # Las reservas creadas desde el dashboard (o sin usuario/fecha de
    # creación por algún dato viejo) simplemente no tienen hilo de WhatsApp:
    # no es un error, el modal lo muestra como estado vacío.
    if reserva.canal_origen != "whatsapp" or reserva.usuario is None or reserva.creado_en is None:
        return jsonify({
            "reserva_id": reserva.id,
            "telefono": reserva.usuario.telefono_wa if reserva.usuario else None,
            "origen": reserva.canal_origen,
            "motivo": "sin_conversacion",
            "truncada": False,
            "mensajes": [],
        })

    # Mismo patrón defensivo que `_serializar_reserva`: `creado_en` puede
    # llegar naive de filas viejas, y hay que compararlo con datetimes aware.
    ancla = reserva.creado_en if reserva.creado_en.tzinfo else reserva.creado_en.replace(tzinfo=timezone.utc)
    telefono = reserva.usuario.telefono_wa

    candidatos = (
        AuditoriaBot.query.filter(
            AuditoriaBot.telefono_wa == telefono,
            AuditoriaBot.creado_en >= ancla - VENTANA_MAX_CONVERSACION,
            AuditoriaBot.creado_en <= ancla + VENTANA_MAX_CONVERSACION,
        )
        .order_by(AuditoriaBot.creado_en.asc())
        .all()
    )

    if not candidatos:
        return jsonify({
            "reserva_id": reserva.id,
            "telefono": telefono,
            "origen": reserva.canal_origen,
            "motivo": "sin_mensajes",
            "truncada": False,
            "mensajes": [],
        })

    def normalizada(m: AuditoriaBot) -> datetime:
        return m.creado_en if m.creado_en.tzinfo else m.creado_en.replace(tzinfo=timezone.utc)

    # Mensaje más cercano a la creación de la reserva: es el punto de partida
    # del recorte por huecos y el que el frontend marca como "es_ancla".
    idx_ancla = min(range(len(candidatos)), key=lambda i: abs(normalizada(candidatos[i]) - ancla))

    inicio = idx_ancla
    while inicio > 0 and normalizada(candidatos[inicio]) - normalizada(candidatos[inicio - 1]) <= HUECO_CONVERSACION:
        inicio -= 1
    fin = idx_ancla
    while (
        fin < len(candidatos) - 1
        and normalizada(candidatos[fin + 1]) - normalizada(candidatos[fin]) <= HUECO_CONVERSACION
    ):
        fin += 1

    hilo = candidatos[inicio : fin + 1]
    truncada = False
    if len(hilo) > MAX_MENSAJES_CONVERSACION:
        # Nos quedamos con los más cercanos al ancla: recortamos del extremo
        # (inicio o final) que esté más lejos de ella hasta llegar al tope.
        truncada = True
        idx_ancla_hilo = idx_ancla - inicio
        while len(hilo) > MAX_MENSAJES_CONVERSACION:
            dist_inicio = idx_ancla_hilo - 0
            dist_fin = len(hilo) - 1 - idx_ancla_hilo
            if dist_inicio >= dist_fin:
                hilo = hilo[1:]
                idx_ancla_hilo -= 1
            else:
                hilo = hilo[:-1]

    ancla_id = candidatos[idx_ancla].id
    return jsonify({
        "reserva_id": reserva.id,
        "telefono": telefono,
        "origen": reserva.canal_origen,
        "motivo": "ok",
        "truncada": truncada,
        "mensajes": [_serializar_mensaje_conversacion(m, ancla_id) for m in hilo],
    })


def _desc_activo(recurso: Recurso) -> str:
    return f"{recurso.nombre} ({recurso.placa})" if recurso.placa else recurso.nombre


@bp.route("/api/reservas", methods=["POST"])
def api_crear_reserva():
    """Crea una reserva completa desde el dashboard, con los mismos campos y
    validaciones de forma que el wizard del bot por WhatsApp (ver
    recursos_service._avanzar_wizard), pero sin sus bloqueos "blandos":
    fecha bloqueada, plazo del seguro e inicio ya pasado solo se avisan acá
    (`advertencias` en la respuesta), no impiden crear la reserva -- quien
    la crea ya es la LACS (ver docs/ARQUITECTURA.md, sección "Crear reservas
    desde el dashboard"). Nace 'confirmada' con `canal_origen='dashboard'`
    sin importar `requiere_confirmacion`: no tiene sentido que la LACS se
    autoconfirme algo que acaba de crear ella misma."""
    datos = request.get_json(silent=True) or {}

    usuario_id = datos.get("usuario_id")
    cliente_nuevo = datos.get("cliente_nuevo")
    usa_usuario_id = isinstance(usuario_id, int) and not isinstance(usuario_id, bool)
    usa_cliente_nuevo = isinstance(cliente_nuevo, dict)
    if usa_usuario_id == usa_cliente_nuevo:
        return jsonify({"error": "Indica para quién es la reserva."}), 400

    recurso_ids = datos.get("recurso_ids")
    if (
        not isinstance(recurso_ids, list)
        or not recurso_ids
        or any(not isinstance(recurso_id, int) or isinstance(recurso_id, bool) for recurso_id in recurso_ids)
        or len(recurso_ids) != len(set(recurso_ids))
    ):
        return jsonify({"error": "Elige al menos un activo, sin repetir."}), 400

    recursos = Recurso.query.filter(Recurso.id.in_(recurso_ids)).all()
    if len(recursos) != len(recurso_ids):
        return jsonify({"error": "Alguno de los activos indicados no existe."}), 400
    for recurso in recursos:
        if recurso.estado != recursos_service.ESTADO_DISPONIBLE:
            return jsonify(
                {"error": f"No se puede reservar {_desc_activo(recurso)}: está {recurso.estado}."}
            ), 400

    try:
        inicio, fin = _fecha_iso(datos.get("inicio")), _fecha_iso(datos.get("fin"))
    except (TypeError, ValueError):
        return jsonify({"error": "Las fechas de inicio y fin deben ser ISO-8601."}), 400
    if fin <= inicio:
        return jsonify({"error": _MENSAJES_INTEGRIDAD_RESERVA["rango_valido"]}), 400

    nrc_limpio, error = _validar_tipo_uso(datos.get("tipo_uso"), datos.get("nrc"))
    if error:
        return jsonify({"error": error}), 400

    modalidad = datos.get("modalidad")
    if modalidad not in ("individual", "grupo"):
        return jsonify({"error": "La modalidad debe ser 'individual' o 'grupo'."}), 400

    codigos_validados = None
    if modalidad == "grupo":
        codigos_validados, error = _validar_integrantes(datos.get("integrantes"))
        if error:
            return jsonify({"error": error}), 400

    sale_de_barranquilla = datos.get("sale_de_barranquilla")
    if not isinstance(sale_de_barranquilla, bool):
        return jsonify({"error": "Indica si los activos salen de Barranquilla."}), 400

    # La nota es OPCIONAL: que no llegue no es error, a diferencia de
    # sale_de_barranquilla arriba.
    nota_limpia, error = _validar_nota(datos.get("nota"))
    if error:
        return jsonify({"error": error}), 400

    usuario_creado = False
    if usa_usuario_id:
        usuario = Usuario.query.get(usuario_id)
        if usuario is None:
            return jsonify({"error": "El usuario no existe."}), 404
    else:
        usuario, usuario_creado, error = usuarios_service.preparar_usuario_dashboard(
            cliente_nuevo.get("nombre"),
            cliente_nuevo.get("telefono"),
            cliente_nuevo.get("codigo_uninorte"),
            cliente_nuevo.get("cedula"),
        )
        if error:
            return jsonify({"error": error}), 400

    ocupados = recursos_service.ids_ocupados_en_rango(inicio, fin, recurso_ids)
    if ocupados:
        db.session.rollback()
        nombres = [_desc_activo(r) for r in recursos if r.id in ocupados]
        return jsonify({
            "error": f"Ocupado en ese horario: {', '.join(nombres)}.",
            "ocupados": sorted(ocupados),
        }), 409

    advertencias = recursos_service.advertencias_fechas(inicio, fin, sale_de_barranquilla)
    if usa_cliente_nuevo and not usuario_creado:
        advertencias.append(
            f"Ya existía un usuario con ese teléfono ({usuario.nombre or usuario.telefono_wa}); "
            "la reserva quedó a su nombre."
        )

    reservas, error = recursos_service.crear_reservas_grupo(
        usuario.id,
        recursos,
        inicio,
        fin,
        tipo_uso=datos.get("tipo_uso"),
        nrc=nrc_limpio,
        modalidad=modalidad,
        tamano_grupo=len(codigos_validados) if modalidad == "grupo" else None,
        integrantes=codigos_validados,
        sale_de_barranquilla=sale_de_barranquilla,
        nota=nota_limpia,
        canal_origen="dashboard",
        aprobada_por_lacs=True,
    )
    if error:
        return jsonify(
            {"error": "Alguno de los activos se acaba de reservar en ese horario. Intenta de nuevo."}
        ), 409

    completas = _reservas_del_grupo(reservas[0].grupo_id)

    notificado = None
    if datos.get("notificar") is True:
        notificado = _avisar_estudiante(
            usuario, recursos_service.mensaje_reserva_confirmada(completas), "reserva_dashboard"
        )

    return jsonify({
        "reservas": [_serializar_reserva(r) for r in completas],
        "grupo_id": reservas[0].grupo_id,
        "usuario": _serializar_usuario(usuario),
        "usuario_creado": usuario_creado,
        "advertencias": advertencias,
        "notificado": notificado,
    }), 201


# Campos que el calendario puede modificar de una reserva. No incluye
# `usuario_id` ni `grupo_id`: reasignar una reserva a otra persona o moverla
# de carrito no es una edición del calendario, es otra reserva.
_CAMPOS_EDITABLES_RESERVA = {"inicio", "fin", "estado", "recurso_id"}

# Ver el comentario de la columna `estado` en postgres/init/02-schema.sql.
_ESTADOS_RESERVA = {
    "confirmada", "pendiente", "cancelada", "entregada", "completada",
    "devuelta_tarde", "no_show", "denegada", "no_devuelta",
}

# Estados a los que SOLO se puede llegar registrando el acta correspondiente
# (ver actas_service.aplicar_transicion) -- nunca a mano desde el selector de
# estado del dashboard. Es una regla de negocio explícita que pidió el
# usuario, no una limitación técnica: simular una entrega o una devolución
# sin el acta real le quitaría todo el valor probatorio al historial.
_ESTADOS_SOLO_CON_ACTA = {"entregada", "completada", "devuelta_tarde", "no_devuelta"}
_MENSAJES_ESTADO_SOLO_ACTA = {
    "entregada": "A 'entregada' solo se llega registrando el acta de entrega (firmada o subida).",
    "completada": "A 'completada' solo se llega registrando el acta de devolución (firmada o subida).",
    "devuelta_tarde": "A 'devuelta tarde' solo se llega registrando el acta de devolución fuera de plazo (firmada o subida).",
    "no_devuelta": "A 'no devuelta' solo se llega registrando el acta de devolución sin marcar ese equipo.",
}


def _rechazar_estado_solo_con_acta(datos: dict) -> str | None:
    """Mensaje de error si `datos` intenta poner a mano un estado de
    `_ESTADOS_SOLO_CON_ACTA`, o None si no aplica. Se llama ANTES de
    `_preparar_cambios_reserva` (que devuelve siempre 400) porque este caso
    es un 409: el estado en sí es válido, lo que se rechaza es el camino
    para llegar a él sin pasar por el acta."""
    return _MENSAJES_ESTADO_SOLO_ACTA.get(datos.get("estado"))

# Constraints de reservas (postgres/init/02-schema.sql) traducidas a mensajes
# legibles. El solapamiento lo impide Postgres con un EXCLUDE gist que solo
# aplica a las confirmadas y pendientes, igual que para las reservas que crea el bot.
_MENSAJES_INTEGRIDAD_RESERVA = {
    "reservas_no_solapamiento": "Ese recurso ya tiene otra reserva confirmada o pendiente que se cruza con ese horario.",
    "rango_valido": "La hora de fin debe ser posterior a la de inicio.",
}


def _preparar_cambios_reserva(datos: dict, campos_permitidos: set) -> tuple[dict | None, str | None]:
    """Valida el cuerpo de un PATCH de reserva y convierte las fechas.

    Devuelve (cambios, None) o (None, mensaje de error).
    """
    campos_desconocidos = set(datos) - campos_permitidos
    if campos_desconocidos:
        return None, f"Campos no editables: {', '.join(sorted(campos_desconocidos))}"

    if "estado" in datos and datos["estado"] not in _ESTADOS_RESERVA:
        return None, f"El estado debe ser uno de: {', '.join(sorted(_ESTADOS_RESERVA))}."

    cambios = dict(datos)
    for campo in ("inicio", "fin"):
        if campo in cambios:
            try:
                cambios[campo] = _fecha_iso(cambios[campo])
            except (TypeError, ValueError):
                return None, f"El campo '{campo}' debe ser una fecha ISO-8601."

    if "recurso_id" in cambios and Recurso.query.get(cambios["recurso_id"]) is None:
        return None, "El recurso indicado no existe."

    return cambios, None


def _aplicar_cambios_reserva(reserva: Reserva, cambios: dict) -> str | None:
    """Escribe los cambios en la reserva (sin commit). Devuelve error o None."""
    inicio = cambios.get("inicio", reserva.inicio)
    fin = cambios.get("fin", reserva.fin)
    if fin <= inicio:
        return _MENSAJES_INTEGRIDAD_RESERVA["rango_valido"]

    for campo, valor in cambios.items():
        setattr(reserva, campo, valor)
    return None


def _error_integridad_reserva(exc: IntegrityError) -> str:
    detalle = str(exc.orig)
    for clave, texto in _MENSAJES_INTEGRIDAD_RESERVA.items():
        if clave in detalle:
            return texto
    return "No se pudo guardar la reserva: revisa los datos ingresados."


@bp.route("/api/reservas/<int:reserva_id>", methods=["PATCH"])
def api_actualizar_reserva(reserva_id):
    reserva = Reserva.query.get_or_404(reserva_id)
    datos = request.get_json(silent=True) or {}

    error = _rechazar_estado_solo_con_acta(datos)
    if error:
        return jsonify({"error": error}), 409

    cambios, error = _preparar_cambios_reserva(datos, _CAMPOS_EDITABLES_RESERVA)
    if error:
        return jsonify({"error": error}), 400

    error = _aplicar_cambios_reserva(reserva, cambios)
    if error:
        return jsonify({"error": error}), 400

    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        return jsonify({"error": _error_integridad_reserva(exc)}), 400

    return jsonify(_serializar_reserva(reserva))


def _validar_tipo_uso(tipo_uso, nrc) -> tuple[str | None, str | None]:
    """Valida uso/NRC con las mismas reglas del wizard del bot (ver
    recursos_service._avanzar_wizard). Devuelve (nrc_limpio, error); para
    'particular' el nrc limpio siempre es None. Compartido por el PATCH de
    grupo y la creación de reservas desde el dashboard."""
    if tipo_uso not in ("particular", "asignatura"):
        return None, "El uso debe ser 'particular' o 'asignatura'."
    if tipo_uso == "particular":
        return None, None
    if not isinstance(nrc, str) or not re.fullmatch(r"\d{4}", nrc):
        return None, "El NRC debe tener exactamente 4 dígitos."
    return nrc, None


def _validar_integrantes(integrantes) -> tuple[list[str] | None, str | None]:
    """Valida la lista de códigos Uninorte de un grupo (2 a
    TAMANO_GRUPO_MAX, sin repetidos), igual que el wizard del bot. Devuelve
    (códigos limpios, error). Compartido por el PATCH de grupo y la
    creación de reservas desde el dashboard."""
    if not isinstance(integrantes, list) or not (
        recursos_service.TAMANO_GRUPO_MIN <= len(integrantes) <= recursos_service.TAMANO_GRUPO_MAX
    ):
        return None, (
            f"El grupo debe tener entre {recursos_service.TAMANO_GRUPO_MIN} y "
            f"{recursos_service.TAMANO_GRUPO_MAX} integrantes."
        )

    codigos_validados = []
    for codigo in integrantes:
        codigo_valido = registro_service.validar_codigo_uninorte(codigo) if isinstance(codigo, str) else None
        if codigo_valido is None:
            return None, f"El ID uninorte '{codigo}' no es válido."
        codigos_validados.append(codigo_valido)
    if len(codigos_validados) != len(set(codigos_validados)):
        return None, "No puede haber integrantes repetidos."
    return codigos_validados, None


def _validar_nota(valor) -> tuple[str | None, str | None]:
    """Valida la nota de la reserva, opcional, con el mismo límite que el
    wizard del bot (recursos_service.NOTA_MAX_CARACTERES) y el VARCHAR(300)
    del esquema. Devuelve (nota_limpia, error); None o "" (tras strip)
    siempre es válido y equivale a "sin nota". Compartido por el PATCH de
    grupo y la creación de reservas desde el dashboard."""
    if valor is None or valor == "":
        return None, None
    if not isinstance(valor, str):
        return None, "La nota debe ser texto."
    texto = valor.strip()
    if not texto:
        return None, None
    if len(texto) > recursos_service.NOTA_MAX_CARACTERES:
        return None, f"La nota no puede superar los {recursos_service.NOTA_MAX_CARACTERES} caracteres."
    return texto, None


@bp.route("/api/reservas/grupo/<int:grupo_id>", methods=["PATCH"])
def api_actualizar_reserva_grupo(grupo_id):
    """Actualiza horario/estado y activos de una reserva completa atomically."""
    datos = request.get_json(silent=True) or {}

    error = _rechazar_estado_solo_con_acta(datos)
    if error:
        return jsonify({"error": error}), 409

    recurso_ids = datos.pop("recurso_ids", None)
    tiene_tipo_uso = "tipo_uso" in datos
    tipo_uso_nuevo = datos.pop("tipo_uso", None)
    tiene_nrc = "nrc" in datos
    nrc_nuevo = datos.pop("nrc", None)
    tiene_modalidad = "modalidad" in datos
    modalidad_nueva = datos.pop("modalidad", None)
    tiene_tamano_grupo = "tamano_grupo" in datos
    tamano_grupo_nuevo = datos.pop("tamano_grupo", None)
    tiene_integrantes = "integrantes" in datos
    integrantes_nuevos = datos.pop("integrantes", None)
    tiene_sale_area = "sale_de_barranquilla" in datos
    sale_area_nuevo = datos.pop("sale_de_barranquilla", None)
    tiene_nota = "nota" in datos
    nota_nueva = datos.pop("nota", None)
    cambios, error = _preparar_cambios_reserva(datos, _CAMPOS_EDITABLES_RESERVA)
    if error:
        return jsonify({"error": error}), 400

    if tiene_modalidad and modalidad_nueva not in ("individual", "grupo"):
        return jsonify({"error": "La modalidad debe ser 'individual' o 'grupo'."}), 400

    if tiene_sale_area and not isinstance(sale_area_nuevo, bool):
        return jsonify({"error": "'sale_de_barranquilla' debe ser true o false."}), 400

    nota_limpia = None
    if tiene_nota:
        nota_limpia, error = _validar_nota(nota_nueva)
        if error:
            return jsonify({"error": error}), 400

    if recurso_ids is not None:
        if (
            not isinstance(recurso_ids, list)
            or not recurso_ids
            or any(not isinstance(recurso_id, int) for recurso_id in recurso_ids)
            or len(recurso_ids) != len(set(recurso_ids))
        ):
            return jsonify({"error": "Debes conservar al menos un activo y no repetir activos."}), 400
        recursos = Recurso.query.filter(Recurso.id.in_(recurso_ids)).all()
        if len(recursos) != len(recurso_ids):
            return jsonify({"error": "Alguno de los activos indicados no existe."}), 400

    grupo = ReservaGrupo.query.get_or_404(grupo_id)
    reservas = Reserva.query.filter_by(grupo_id=grupo.id).order_by(Reserva.id).all()
    if not reservas:
        return jsonify({"error": "La reserva no tiene activos."}), 404

    # Si solo llega uno de los dos (ej. el usuario corrige el NRC sin tocar
    # el tipo de uso), el otro se completa con lo que ya tenía el grupo.
    if tiene_tipo_uso or tiene_nrc:
        tipo_uso_efectivo = tipo_uso_nuevo if tiene_tipo_uso else grupo.tipo_uso
        nrc_efectivo = nrc_nuevo if tiene_nrc else grupo.nrc
        nrc_limpio, error = _validar_tipo_uso(tipo_uso_efectivo, nrc_efectivo)
        if error:
            return jsonify({"error": error}), 400
        grupo.tipo_uso = tipo_uso_efectivo
        grupo.nrc = nrc_limpio

    # Mismo criterio que tipo_uso/nrc arriba: si solo llega uno de los tres
    # campos, los otros se completan con lo que ya tenía el grupo. El
    # reemplazo de integrantes es completo (no un PATCH por fila): se limpia
    # la lista y se reinserta 1..N -- `cascade="all, delete-orphan"` en
    # ReservaGrupo.integrantes (models.py) hace el DELETE de las filas viejas.
    if tiene_modalidad or tiene_tamano_grupo or tiene_integrantes:
        modalidad_efectiva = modalidad_nueva if tiene_modalidad else grupo.modalidad
        if modalidad_efectiva == "grupo":
            integrantes_efectivos = (
                integrantes_nuevos if tiene_integrantes
                else [i.codigo_uninorte for i in grupo.integrantes]
            )
            codigos_validados, error = _validar_integrantes(integrantes_efectivos)
            if error:
                return jsonify({"error": error}), 400
            if tiene_tamano_grupo and tamano_grupo_nuevo != len(codigos_validados):
                return jsonify({"error": "El tamaño del grupo no coincide con la cantidad de integrantes."}), 400

            grupo.modalidad = "grupo"
            grupo.tamano_grupo = len(codigos_validados)
            grupo.integrantes = [
                ReservaGrupoIntegrante(codigo_uninorte=codigo, orden=orden)
                for orden, codigo in enumerate(codigos_validados, start=1)
            ]
        else:
            grupo.modalidad = "individual"
            grupo.tamano_grupo = None
            grupo.integrantes = []

    # Sin validar el plazo de 5 días hábiles del seguro: el dashboard solo
    # advierte (ver ReservaDetalle.tsx); la LACS tiene la última palabra.
    if tiene_sale_area:
        grupo.sale_de_barranquilla = sale_area_nuevo

    if tiene_nota:
        grupo.nota = nota_limpia

    for reserva in reservas:
        error = _aplicar_cambios_reserva(reserva, cambios)
        if error:
            return jsonify({"error": error}), 400

    if recurso_ids is not None:
        ids_actuales = {reserva.recurso_id for reserva in reservas}
        ids_nuevos = set(recurso_ids)
        for reserva in reservas:
            if reserva.recurso_id not in ids_nuevos:
                db.session.delete(reserva)

        reserva_base = reservas[0]
        # Un activo que agrega la LACS desde aquí ya lo aprobó ella misma: no
        # nace pendiente aunque la línea que se toma de base lo esté.
        estado_nuevo = cambios.get("estado")
        if estado_nuevo is None:
            estado_nuevo = (
                "confirmada"
                if reserva_base.estado == recursos_service.ESTADO_PENDIENTE
                # Un activo agregado ahora nunca "heredó" haber sido
                # entregado/devuelto: si la base ya está en un estado de
                # _ESTADOS_SOLO_CON_ACTA, la línea nueva nace 'confirmada' y
                # queda a la espera de su PROPIA acta de entrega -- lo
                # contrario dejaría un equipo marcado como entregado sin que
                # nadie lo haya firmado nunca.
                else "confirmada" if reserva_base.estado in _ESTADOS_SOLO_CON_ACTA
                else reserva_base.estado
            )
        inicio_nuevo = cambios.get("inicio", reserva_base.inicio)
        fin_nuevo = cambios.get("fin", reserva_base.fin)
        for recurso_id in ids_nuevos - ids_actuales:
            db.session.add(
                Reserva(
                    usuario_id=reserva_base.usuario_id,
                    recurso_id=recurso_id,
                    inicio=inicio_nuevo,
                    fin=fin_nuevo,
                    estado=estado_nuevo,
                    canal_origen=reserva_base.canal_origen,
                    grupo_id=grupo.id,
                )
            )

    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        return jsonify({"error": _error_integridad_reserva(exc)}), 400

    actualizadas = Reserva.query.filter_by(grupo_id=grupo.id).order_by(Reserva.id).all()
    return jsonify([_serializar_reserva(reserva) for reserva in actualizadas])


@bp.route("/api/reservas/grupo/<int:grupo_id>/confirmar", methods=["POST"])
def api_confirmar_reserva_grupo(grupo_id):
    """La LACS aprueba los activos de una reserva que esperaban su
    confirmación (estado 'pendiente') y se le avisa al estudiante por
    WhatsApp.

    El cambio de estado se guarda antes de enviar y no depende del envío: si
    WhatsApp no está conectado, la reserva queda confirmada igual y la
    respuesta lo dice (`notificado: false`) para que el dashboard advierta
    que hay que avisarle por otro medio."""
    grupo = ReservaGrupo.query.get_or_404(grupo_id)
    reservas = _reservas_del_grupo(grupo.id)
    pendientes = [r for r in reservas if r.estado == recursos_service.ESTADO_PENDIENTE]
    if not pendientes:
        return jsonify({"error": "Esta reserva no tiene activos pendientes de confirmación."}), 400

    for reserva in pendientes:
        reserva.estado = "confirmada"
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        return jsonify({"error": _error_integridad_reserva(exc)}), 400

    texto = recursos_service.mensaje_reserva_confirmada(reservas)
    enviado = _avisar_estudiante(reservas[0].usuario, texto, "confirmacion_lacs")
    return jsonify({
        "reservas": [_serializar_reserva(r) for r in reservas],
        "notificado": enviado,
    })


@bp.route("/api/reservas/<int:reserva_id>/<any(confirmar, denegar):decision>", methods=["POST"])
def api_decidir_reserva(reserva_id, decision):
    """Confirma o deniega un solo activo pendiente de una reserva (un botón
    por activo en el modal) y le avisa al estudiante qué decidió la LACS y
    cómo quedó el resto (recursos_service.mensaje_decision_lacs).

    Denegar deja la línea 'denegada': el activo queda libre en ese horario y
    deja de contar como parte de la reserva, pero la fila se conserva para
    saber qué se negó. Igual que al confirmar la reserva completa, el cambio
    se guarda aunque el WhatsApp no salga."""
    reserva = Reserva.query.get_or_404(reserva_id)
    if reserva.estado != recursos_service.ESTADO_PENDIENTE:
        return jsonify({"error": "Ese activo ya no está pendiente de confirmación."}), 400

    reserva.estado = "confirmada" if decision == "confirmar" else recursos_service.ESTADO_DENEGADA
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        return jsonify({"error": _error_integridad_reserva(exc)}), 400

    reservas = _reservas_del_grupo(reserva.grupo_id)
    decidida = next(r for r in reservas if r.id == reserva.id)
    texto = recursos_service.mensaje_decision_lacs(decidida, reservas)
    intent = "confirmacion_lacs" if decision == "confirmar" else "denegacion_lacs"
    enviado = _avisar_estudiante(decidida.usuario, texto, intent)
    return jsonify({
        "reservas": [_serializar_reserva(r) for r in reservas],
        "notificado": enviado,
    })


def _reservas_del_grupo(grupo_id: int) -> list[Reserva]:
    return (
        Reserva.query.options(
            joinedload(Reserva.usuario), joinedload(Reserva.recurso),
            joinedload(Reserva.grupo).joinedload(ReservaGrupo.integrantes),
        )
        .filter_by(grupo_id=grupo_id)
        .order_by(Reserva.id)
        .all()
    )


def _serializar_acta(a: ReservaActa) -> dict:
    return {
        "id": a.id,
        "momento": a.momento,
        "origen": a.origen,
        "bytes": a.bytes,
        "sha256": a.sha256,
        "firmado_por": a.firmado_por,
        "nota": a.nota,
        "creado_en": a.creado_en.isoformat() if a.creado_en else None,
    }


def _ruta_acta_si_valida(acta: ReservaActa) -> Path | None:
    """Resuelve `acta.archivo` contra ACTAS_DIR y comprueba que el resultado
    quede DENTRO de esa carpeta y que el archivo exista -- nunca se construye
    la ruta a partir de algo que venga del request (acá `acta` ya salió de
    una fila propia de la base, pero la comprobación queda igual como defensa
    en profundidad: si `archivo` llegara a guardarse mal algún día, esto
    evita servir cualquier archivo del sistema)."""
    actas_dir = Path(current_app.config["ACTAS_DIR"]).resolve()
    ruta = (actas_dir / acta.archivo).resolve()
    if not ruta.is_relative_to(actas_dir) or not ruta.is_file():
        return None
    return ruta


@bp.route("/api/reservas/grupo/<int:grupo_id>/pdf")
def api_acta_reserva_grupo(grupo_id):
    """Acta de entrega y devolución en PDF de la reserva completa -- un
    archivo por `grupo_id`, no por activo, para que quien firma lo haga una
    sola vez por todo lo que se lleva.

    Si ya hay un acta guardada (firmada en pantalla o subida), se sirve ESE
    archivo tal cual -- es el que se va actualizando en la página a medida
    que se registra cada momento, y de paso ahorra recomponer el PDF con
    reportlab en cada apertura (antes se regeneraba siempre). `?base=1`
    fuerza el acta recién generada, para depurar la plantilla sin mirar una
    firma vieja."""
    reservas = _reservas_del_grupo(grupo_id)
    if not reservas:
        return jsonify({"error": "Esa reserva no existe."}), 404

    ultima_acta = None if request.args.get("base") == "1" else actas_service.ultima(grupo_id)
    ruta = _ruta_acta_si_valida(ultima_acta) if ultima_acta is not None else None

    if ruta is not None:
        respuesta = send_file(
            ruta, mimetype="application/pdf", as_attachment=True,
            download_name=f"reserva-{grupo_id}.pdf",
        )
    else:
        # Sin acta guardada (o el archivo desapareció del disco -- volumen
        # movido, borrado a mano) se cae al acta base recién generada, en vez
        # de romper la descarga.
        pdf = acta_pdf.generar_acta(reservas)
        respuesta = Response(
            pdf,
            mimetype="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="reserva-{grupo_id}.pdf"'},
        )

    respuesta.headers["X-Content-Type-Options"] = "nosniff"
    return respuesta


@bp.route("/api/reservas/grupo/<int:grupo_id>/acta/estado")
def api_estado_acta_grupo(grupo_id):
    """Lo que el navegador necesita antes de mostrar el modal de firma: qué
    falta registrar (`momento_siguiente`), con qué anclas estampar la
    próxima firma y el historial de versiones ya guardadas.

    Las anclas son las de la ÚLTIMA acta guardada si existe -- no se
    recalculan, la firma siguiente se estampa sobre ESE archivo (ver el
    comentario de `reserva_actas.anclas` en el esquema). Si todavía no hay
    ninguna acta, se generan las del acta base recién compuesta: son las que
    va a usar la PRIMERA firma."""
    reservas = _reservas_del_grupo(grupo_id)
    if not reservas:
        return jsonify({"error": "Esa reserva no existe."}), 404

    ultima_acta = actas_service.ultima(grupo_id)
    if ultima_acta is not None:
        anclas = ultima_acta.anclas or {}
    else:
        _pdf, anclas = acta_pdf.generar_acta_con_anclas(reservas)

    historial = (
        ReservaActa.query.filter_by(grupo_id=grupo_id)
        .order_by(ReservaActa.creado_en.desc(), ReservaActa.id.desc())
        .all()
    )
    return jsonify({
        "momento_siguiente": actas_service.momento_siguiente(reservas),
        "tiene_acta": ultima_acta is not None,
        "anclas": anclas,
        "historial": [_serializar_acta(a) for a in historial],
        # TODAS las líneas del grupo, sin filtrar -- el paso "Equipos" del
        # diálogo de firma no puede depender de lo que muestre el calendario
        # (que oculta canceladas y aplica filtros): en la entrega, una línea
        # que faltara ahí se cancelaría sin que nadie la viera.
        "lineas": [
            {
                "id": r.id,
                "nombre": r.recurso.nombre if r.recurso else f"Recurso #{r.recurso_id}",
                "estado": r.estado,
            }
            for r in reservas
        ],
    })


@bp.route("/api/reservas/grupo/<int:grupo_id>/actas/<int:acta_id>/pdf")
def api_acta_historica_pdf(grupo_id, acta_id):
    """Descarga una versión histórica concreta del acta (no necesariamente
    la última) -- lo usa el Historial del modal de actas."""
    acta = ReservaActa.query.filter_by(id=acta_id, grupo_id=grupo_id).first()
    if acta is None:
        return jsonify({"error": "Esa versión del acta no existe."}), 404

    ruta = _ruta_acta_si_valida(acta)
    if ruta is None:
        return jsonify({"error": "El archivo de esa acta no está disponible."}), 404

    respuesta = send_file(
        ruta, mimetype="application/pdf", as_attachment=True,
        download_name=f"reserva-{grupo_id}-{acta.momento}-{acta.id}.pdf",
    )
    respuesta.headers["X-Content-Type-Options"] = "nosniff"
    return respuesta


@bp.route("/api/reservas/grupo/<int:grupo_id>/actas", methods=["POST"])
def api_crear_acta_grupo(grupo_id):
    """Registra una firma hecha en pantalla o una subida de acta externa --
    la ÚNICA puerta por la que una reserva llega a 'entregada'/'completada'/
    'devuelta_tarde' (ver _ESTADOS_SOLO_CON_ACTA). `multipart/form-data`:
    `archivo` (el PDF), `momento` ('entrega'|'devolucion'), `origen`
    ('firmada'|'subida'), `anclas` (JSON en texto, opcional -- las que usó el
    navegador para estampar, solo tiene sentido con origen='firmada'),
    `firmado_por` (texto, opcional) y `nota` (texto, opcional, ver _validar_nota).

    Guarda el archivo y cambia el estado de las líneas en la MISMA
    transacción (un solo commit): si falla, se borra el archivo recién
    escrito para no dejar huérfanos en disco."""
    reservas = _reservas_del_grupo(grupo_id)
    if not reservas:
        return jsonify({"error": "Esa reserva no existe."}), 404

    momento = request.form.get("momento")
    if momento not in (actas_service.MOMENTO_ENTREGA, actas_service.MOMENTO_DEVOLUCION):
        return jsonify({"error": "'momento' debe ser 'entrega' o 'devolucion'."}), 400

    origen = request.form.get("origen")
    if origen not in ("firmada", "subida"):
        return jsonify({"error": "'origen' debe ser 'firmada' o 'subida'."}), 400

    siguiente = actas_service.momento_siguiente(reservas)
    if momento != siguiente:
        if siguiente is None:
            mensaje = "Esta reserva ya no tiene activos por entregar."
        elif siguiente == actas_service.MOMENTO_ENTREGA:
            mensaje = "Esta reserva todavía no tiene registrada el acta de entrega."
        else:
            mensaje = "Esta reserva ya fue entregada; falta registrar la devolución, no una nueva entrega."
        return jsonify({"error": mensaje}), 409

    archivo = request.files.get("archivo")
    if archivo is None or not archivo.filename:
        return jsonify({"error": "Falta el archivo PDF del acta."}), 400
    datos = archivo.read()

    anclas = None
    anclas_crudo = request.form.get("anclas")
    if anclas_crudo:
        try:
            anclas = json.loads(anclas_crudo)
        except (TypeError, ValueError):
            return jsonify({"error": "'anclas' debe ser JSON válido."}), 400

    firmado_por = (request.form.get("firmado_por") or "").strip() or None

    nota, error = _validar_nota(request.form.get("nota"))
    if error:
        return jsonify({"error": error}), 400

    # Ids de reserva (líneas) marcados en el paso "Equipos" del diálogo de
    # firma, separados por coma. Ausente = todas (acta subida o cliente
    # viejo), ver actas_service.aplicar_transicion.
    equipos = None
    equipos_crudo = request.form.get("equipos")
    if equipos_crudo is not None:
        try:
            equipos = {int(x) for x in equipos_crudo.split(",") if x.strip()}
        except ValueError:
            return jsonify({"error": "'equipos' debe ser una lista de ids separados por coma."}), 400

    try:
        acta = actas_service.guardar(grupo_id, momento, origen, datos, anclas, firmado_por, nota)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    cambiadas, error = actas_service.aplicar_transicion(reservas, momento, equipos)
    if error:
        # Equipos marcados inválidos, o la carrera de dos requests
        # concurrentes registrando la misma acta: el segundo llega con las
        # líneas ya cambiadas por el primero.
        db.session.rollback()
        (Path(current_app.config["ACTAS_DIR"]) / acta.archivo).unlink(missing_ok=True)
        return jsonify({"error": error}), 409

    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        (Path(current_app.config["ACTAS_DIR"]) / acta.archivo).unlink(missing_ok=True)
        return jsonify({"error": _error_integridad_reserva(exc)}), 400

    actualizadas = _reservas_del_grupo(grupo_id)
    ahora = datetime.now(timezone.utc)
    return jsonify({
        "reservas": [_serializar_reserva(r, ahora) for r in actualizadas],
        "acta": _serializar_acta(acta),
    }), 201


def _avisar_estudiante(usuario: Usuario, texto: str, intent: str) -> bool:
    """Delegación de una línea: el cuerpo se movió a
    avisos_reservas.avisar_usuario porque el ticker de no-show (hilo de
    fondo, ver app/ticker.py) también lo necesita y no debe importar un
    módulo de blueprint. Se conserva esta función para no tocar a sus tres
    llamadores actuales en este archivo."""
    return avisos_reservas.avisar_usuario(usuario, texto, intent)


@bp.route("/api/reservas/grupo/<int:grupo_id>", methods=["DELETE"])
def api_eliminar_reserva_grupo(grupo_id):
    """Elimina todas las líneas y el número de una reserva completa."""
    grupo = ReservaGrupo.query.get_or_404(grupo_id)
    for reserva in Reserva.query.filter_by(grupo_id=grupo.id).all():
        db.session.delete(reserva)
    db.session.delete(grupo)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "No se puede eliminar la reserva porque tiene información asociada."}), 400

    return "", 204


@bp.route("/api/reservas/bloque", methods=["PATCH"])
def api_actualizar_reservas_bloque():
    """Aplica el mismo cambio a varias reservas en una sola transacción.

    El calendario muestra como un solo evento las reservas del mismo usuario
    y horario (una reserva hecha para varios activos), así que mover ese
    evento debe mover todas sus líneas o ninguna. No incluye `recurso_id`:
    eso solo tiene sentido reserva por reserva.
    """
    datos = request.get_json(silent=True) or {}
    ids = datos.get("ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, int) for i in ids):
        return jsonify({"error": "Debes indicar los ids de las reservas a editar."}), 400

    error = _rechazar_estado_solo_con_acta(datos)
    if error:
        return jsonify({"error": error}), 409

    cambios, error = _preparar_cambios_reserva(
        {campo: valor for campo, valor in datos.items() if campo != "ids"},
        _CAMPOS_EDITABLES_RESERVA - {"recurso_id"},
    )
    if error:
        return jsonify({"error": error}), 400

    reservas = Reserva.query.filter(Reserva.id.in_(ids)).all()
    if len(reservas) != len(set(ids)):
        return jsonify({"error": "Alguna de las reservas ya no existe."}), 404

    for reserva in reservas:
        error = _aplicar_cambios_reserva(reserva, cambios)
        if error:
            db.session.rollback()
            return jsonify({"error": error}), 400

    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        return jsonify({"error": _error_integridad_reserva(exc)}), 400

    return jsonify([_serializar_reserva(r) for r in reservas])


# --- Activos (equipos/espacios) y sus relaciones padre/hijo -----------------

# Campos que el panel de Activos puede modificar. No incluye `id` ni
# `creado_en`. `tipo` se puede editar pero sigue sujeto al CHECK de Postgres
# (equipo|espacio) y a las constraints de placa/categoría requeridas.
_CAMPOS_EDITABLES_ACTIVO = {
    "tipo",
    "nombre",
    "descripcion",
    "ubicacion",
    "placa",
    "categoria",
    "subcategoria",
    "marca",
    "modelo",
    "identificador",
    "requiere_confirmacion",
    "estado",
    "padre_id",
    "espacio_id",
}

# El CHECK `recursos_estado_valido` de postgres/init/02-schema.sql también
# permite 'no_devuelto', pero ese no se elige a mano: solo lo fija el acta de
# devolución (ver actas_service.RECURSO_NO_DEVUELTO). El PATCH de abajo
# permite salir de 'no_devuelto' o reenviarlo sin cambios, pero nunca ponerlo
# desde acá. Solo 'disponible' es reservable (ver
# recursos_service.ESTADO_DISPONIBLE) -- 'en_mantenimiento', 'no_disponible'
# y 'no_devuelto' quedan fuera del catálogo que ve el bot.
_ESTADOS_VALIDOS = {"disponible", "en_mantenimiento", "no_disponible"}

# Un padre (padre_id) es siempre un equipo: los accesorios cuelgan de un
# equipo, y el lugar donde algo está se dice con espacio_id, no con padre_id.
_ERROR_PADRE_NO_EQUIPO = "El padre debe ser un equipo. Para indicar en qué lugar está, usa Lugar / espacio."

# Traduce violaciones de constraints de Postgres (ver postgres/init/02-schema.sql)
# a mensajes legibles, en vez de exponer el error crudo de asyncpg/psycopg.
_MENSAJES_INTEGRIDAD = {
    "recursos_placa_formato": "La placa de un padre debe ser 8 dígitos numéricos; la de un hijo, 7-10 caracteres alfanuméricos en mayúsculas.",
    "recursos_placa_requerida_equipo": "Los equipos requieren una placa.",
    "recursos_categoria_requerida_equipo": "Los equipos requieren una categoría.",
    "recursos_padre_no_self": "Un activo no puede ser su propio padre.",
    "recursos_espacio_no_self": "Un activo no puede estar dentro de sí mismo.",
    "recursos_espacio_sin_padre_ni_lugar": "Un lugar no puede estar dentro de otro lugar ni ser hijo de un activo.",
    "idx_recursos_placa_unica": "Ya existe otro activo con esa placa.",
    "recursos_tipo_check": "El tipo debe ser 'equipo' o 'espacio'.",
    "recursos_estado_valido": "El estado debe ser 'disponible', 'en_mantenimiento' o 'no_disponible'.",
}


def _mensaje_integridad(exc: IntegrityError) -> str:
    detalle = str(exc.orig)
    for clave, texto in _MENSAJES_INTEGRIDAD.items():
        if clave in detalle:
            return texto
    return "No se pudo guardar: revisa los datos ingresados."


def _serializar_recurso(r: Recurso) -> dict:
    return {
        "id": r.id,
        "tipo": r.tipo,
        "nombre": r.nombre,
        "descripcion": r.descripcion,
        "ubicacion": r.ubicacion,
        "placa": r.placa,
        "padre_id": r.padre_id,
        "espacio_id": r.espacio_id,
        "categoria": r.categoria,
        "subcategoria": r.subcategoria,
        "marca": r.marca,
        "modelo": r.modelo,
        "identificador": r.identificador,
        "requiere_confirmacion": r.requiere_confirmacion,
        "estado": r.estado,
        "creado_en": r.creado_en.isoformat() if r.creado_en else None,
    }


def _validar_espacio(recurso_id: int | None, espacio_id) -> str | None:
    """Valida un `espacio_id` propuesto para `recurso_id` (None si es un alta).

    Devuelve un mensaje de error legible, o None si el espacio es válido.
    No se valida con un CHECK de Postgres porque `tipo='espacio'` requiere
    una subquery (Postgres no las admite en CHECK) y un trigger daría un
    error genérico que rompería el mapeo de `_MENSAJES_INTEGRIDAD`, que
    espera poder distinguir cada violación por el nombre de la constraint.
    """
    if espacio_id is None:
        return None

    espacio = Recurso.query.get(espacio_id)
    if espacio is None:
        return "El espacio indicado no existe."
    if espacio.tipo != "espacio":
        return "El destino debe ser un activo de tipo 'espacio'."
    if recurso_id is not None and espacio.id == recurso_id:
        return "Un activo no puede estar dentro de sí mismo."

    # No hace falta buscar ciclos: un espacio no puede tener lugar (CHECK
    # recursos_espacio_sin_padre_ni_lugar), así que la cadena de lugares
    # nunca pasa de un nivel.
    return None


def _propagar_espacio_a_hijos(recurso: Recurso, espacio_anterior, nuevo_espacio) -> list[Recurso]:
    """Mueve al nuevo lugar a los descendientes (por padre_id) que estaban
    sincronizados con `recurso`, es decir cuyo espacio_id era igual al valor
    *anterior* del padre (incluido el caso NULL == NULL).

    Los hijos que el usuario ya había puesto a mano en otro lugar no se
    tocan -- esa excepción manual es "pegajosa": una vez que alguien saca un
    hijo del lugar del padre, futuros cambios del padre ya no lo arrastran.
    Recorre recursivamente con un set de visitados como guarda contra ciclos
    (no deberían existir por padre_id, pero por si acaso).
    """
    actualizados = []
    visitados: set[int] = set()
    pendientes = list(recurso.hijos)
    while pendientes:
        hijo = pendientes.pop()
        if hijo.id in visitados:
            continue
        visitados.add(hijo.id)
        if hijo.espacio_id == espacio_anterior:
            hijo.espacio_id = nuevo_espacio
            actualizados.append(hijo)
        pendientes.extend(hijo.hijos)
    return actualizados


@bp.route("/api/activos")
def api_activos():
    recursos = Recurso.query.order_by(Recurso.nombre).all()
    return jsonify([_serializar_recurso(r) for r in recursos])


@bp.route("/api/activos", methods=["POST"])
def api_crear_activo():
    datos = request.get_json(silent=True) or {}

    campos_desconocidos = set(datos) - _CAMPOS_EDITABLES_ACTIVO
    if campos_desconocidos:
        return jsonify({"error": f"Campos no editables: {', '.join(sorted(campos_desconocidos))}"}), 400

    if not datos.get("tipo"):
        return jsonify({"error": "El tipo es obligatorio (equipo o espacio)."}), 400
    if not datos.get("nombre"):
        return jsonify({"error": "El nombre es obligatorio."}), 400

    estado = datos.get("estado") or "disponible"
    if estado not in _ESTADOS_VALIDOS:
        return jsonify({"error": _MENSAJES_INTEGRIDAD["recursos_estado_valido"]}), 400

    padre_id = datos.get("padre_id")
    padre = Recurso.query.get(padre_id) if padre_id is not None else None
    if padre_id is not None and padre is None:
        # No hace falta chequear ciclos aquí: un recurso recién creado no
        # tiene descendientes todavía, así que no puede formar uno.
        return jsonify({"error": "El padre indicado no existe."}), 400
    if padre is not None and padre.tipo != "equipo":
        return jsonify({"error": _ERROR_PADRE_NO_EQUIPO}), 400

    if "espacio_id" in datos:
        espacio_id = datos.get("espacio_id")
    elif padre is not None:
        # Herencia: si no se especifica lugar pero sí padre, el hijo nuevo
        # nace en el mismo lugar que su padre (el padre no tiene por qué
        # tener lugar -- en ese caso hereda NULL, que es lo correcto).
        espacio_id = padre.espacio_id
    else:
        espacio_id = None

    error = _validar_espacio(None, espacio_id)
    if error:
        return jsonify({"error": error}), 400

    recurso = Recurso(
        tipo=datos.get("tipo"),
        nombre=datos.get("nombre"),
        descripcion=datos.get("descripcion"),
        ubicacion=datos.get("ubicacion"),
        placa=datos.get("placa"),
        categoria=datos.get("categoria"),
        subcategoria=datos.get("subcategoria"),
        marca=datos.get("marca"),
        modelo=datos.get("modelo"),
        identificador=datos.get("identificador"),
        requiere_confirmacion=bool(datos.get("requiere_confirmacion", False)),
        estado=estado,
        padre_id=padre_id,
        espacio_id=espacio_id,
    )
    db.session.add(recurso)

    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        return jsonify({"error": _mensaje_integridad(exc)}), 400

    return jsonify(_serializar_recurso(recurso)), 201


@bp.route("/api/activos/<int:recurso_id>", methods=["PATCH"])
def api_actualizar_activo(recurso_id):
    recurso = Recurso.query.get_or_404(recurso_id)
    datos = request.get_json(silent=True) or {}

    # No es un campo de la tabla, es una opción de la petición: se saca del
    # dict ANTES del chequeo de campos_desconocidos (si no, lo rechazaría) y
    # antes del setattr genérico de más abajo.
    propagar_espacio = datos.pop("propagar_espacio", True)

    campos_desconocidos = set(datos) - _CAMPOS_EDITABLES_ACTIVO
    if campos_desconocidos:
        return jsonify({"error": f"Campos no editables: {', '.join(sorted(campos_desconocidos))}"}), 400

    if "estado" in datos and datos["estado"] != recurso.estado and datos["estado"] not in _ESTADOS_VALIDOS:
        if datos["estado"] == actas_service.RECURSO_NO_DEVUELTO:
            return jsonify({"error": "A 'no devuelto' solo se llega registrando el acta de devolución sin marcar ese equipo."}), 409
        return jsonify({"error": _MENSAJES_INTEGRIDAD["recursos_estado_valido"]}), 400

    if "tipo" in datos and datos["tipo"] != "espacio":
        # Si deja de ser 'espacio' pero todavía hay activos ubicados ahí
        # (espacio_id = este recurso), esos activos quedarían apuntando a un
        # equipo -- no tiene sentido y rompería la regla que valida
        # _validar_espacio. Hay que resolver eso primero (mover o desvincular
        # esos activos) antes de cambiar el tipo.
        cantidad_contenidos = Recurso.query.filter_by(espacio_id=recurso.id).count()
        if cantidad_contenidos:
            return jsonify({
                "error": f"No puedes cambiar el tipo: hay {cantidad_contenidos} activos ubicados en este espacio."
            }), 400

    if "tipo" in datos and datos["tipo"] == "espacio" and recurso.tipo != "espacio":
        # Espejo del caso de arriba: si este equipo tiene hijos por
        # padre_id, pasarlo a espacio los dejaría con un lugar como padre
        # (ver _ERROR_PADRE_NO_EQUIPO). Hay que desvincularlos primero.
        cantidad_hijos = Recurso.query.filter_by(padre_id=recurso.id).count()
        if cantidad_hijos:
            return jsonify({
                "error": f"No puedes cambiar el tipo: este activo tiene {cantidad_hijos} hijo{'' if cantidad_hijos == 1 else 's'}."
            }), 400

    # Misma regla que el CHECK recursos_espacio_sin_padre_ni_lugar, pero
    # antes de tocar nada: si solo la atrapara el CHECK, el lazy load de
    # `recurso.hijos` en _propagar_espacio_a_hijos hace autoflush del UPDATE
    # fuera del try/commit de abajo y el IntegrityError sale como un 500.
    if datos.get("tipo", recurso.tipo) == "espacio" and (
        datos.get("padre_id", recurso.padre_id) is not None
        or datos.get("espacio_id", recurso.espacio_id) is not None
    ):
        return jsonify({"error": _MENSAJES_INTEGRIDAD["recursos_espacio_sin_padre_ni_lugar"]}), 400

    espacio_anterior = recurso.espacio_id
    espacio_cambia = "espacio_id" in datos and datos["espacio_id"] != espacio_anterior
    if espacio_cambia:
        error = _validar_espacio(recurso.id, datos["espacio_id"])
        if error:
            return jsonify({"error": error}), 400

    if "padre_id" in datos:
        nuevo_padre_id = datos["padre_id"]
        if nuevo_padre_id is not None and nuevo_padre_id != recurso.padre_id:
            if nuevo_padre_id == recurso.id:
                return jsonify({"error": "Un activo no puede ser su propio padre."}), 400
            padre = Recurso.query.get(nuevo_padre_id)
            if padre is None:
                return jsonify({"error": "El padre indicado no existe."}), 400
            if padre.tipo != "equipo":
                return jsonify({"error": _ERROR_PADRE_NO_EQUIPO}), 400
            # Evita ciclos: si recurso.id aparece en la cadena de padres del
            # nuevo padre, ese nuevo padre es en realidad descendiente de
            # recurso, y asignarlo lo convertiría en un ciclo.
            actual = padre
            while actual is not None:
                if actual.id == recurso.id:
                    return jsonify({"error": "Esa relación crearía un ciclo entre activos."}), 400
                actual = actual.padre

    for campo, valor in datos.items():
        setattr(recurso, campo, valor)

    # La propagación se calcula DESPUÉS de aplicar el cambio a `recurso`
    # (para que sus hijos por padre_id queden accesibles vía la relación
    # `hijos`), pero usando `espacio_anterior` -- el valor de ANTES del
    # setattr de arriba -- como referencia de qué hijos estaban
    # sincronizados con el padre.
    hijos_actualizados = []
    if espacio_cambia and propagar_espacio:
        hijos_actualizados = _propagar_espacio_a_hijos(recurso, espacio_anterior, datos["espacio_id"])

    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        return jsonify({"error": _mensaje_integridad(exc)}), 400

    respuesta = _serializar_recurso(recurso)
    if hijos_actualizados:
        # El frontend hace reload() de todos modos; esto es solo para que
        # el toast de confirmación pueda decir cuántos hijos se movieron.
        respuesta["hijos_actualizados"] = len(hijos_actualizados)
    return jsonify(respuesta)
