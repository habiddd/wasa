"""
API del dashboard interno de monitoreo para el equipo de la CPA: estado de
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
import re
import secrets
from datetime import datetime, timedelta, timezone

import qrcode
import requests
from flask import Blueprint, Response, current_app, jsonify, request
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app import estado_bot, guardrails, recursos_service, registro_service, whatsapp_client
from app.extensions import db, redis_client
from app.models import AuditoriaBot, Recurso, Reserva, ReservaGrupo, ReservaGrupoIntegrante, Usuario

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
            {"WWW-Authenticate": 'Basic realm="Dashboard CPA"'},
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
    hoy = datetime.now(timezone.utc).astimezone(recursos_service.ZONA_HORARIA_CPA).date()
    return jsonify({
        "dias_habiles": recursos_service.DIAS_MINIMOS_TRAMITE_SEGURO,
        "hoy": hoy.isoformat(),
        "fecha_minima": recursos_service.fecha_minima_sale_de_barranquilla(hoy).isoformat(),
    })


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
        "creado_en": u.creado_en.isoformat() if u.creado_en else None,
    }


@bp.route("/api/usuarios")
def api_usuarios():
    # Solo usuarios que terminaron el flujo de registro por WhatsApp -- deja
    # afuera contactos a medio registrar y el usuario dummy de seed.py
    # (registro_completo=False), que no es un registro real del bot.
    usuarios = (
        Usuario.query.filter_by(registro_completo=True)
        .order_by(Usuario.creado_en.desc())
        .all()
    )
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


def _serializar_reserva(r: Reserva) -> dict:
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
                .astimezone(recursos_service.ZONA_HORARIA_CPA).date()
            ).isoformat()
            if r.grupo and r.grupo.sale_de_barranquilla and r.creado_en else None
        ),
        "inicio": r.inicio.isoformat() if r.inicio else None,
        "fin": r.fin.isoformat() if r.fin else None,
        "estado": r.estado,
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


@bp.route("/api/reservas")
def api_reservas():
    # Sesiones de uso (reservas) de cada usuario. Sin parámetros devuelve las
    # últimas 500 por fecha de creación (sub-panel "Sesiones" dentro de
    # Usuarios). Con ?desde/?hasta devuelve las que *solapan* ese rango,
    # ordenadas por inicio: es lo que necesita el calendario, porque una
    # reserva de varios días debe aparecer aunque empiece antes del periodo
    # visible. Trae usuario/recurso con joinedload para no hacer una query
    # por fila.
    try:
        desde, hasta = _fecha_query("desde"), _fecha_query("hasta")
    except ValueError:
        return jsonify({"error": "Los parámetros 'desde' y 'hasta' deben ser fechas ISO-8601."}), 400

    consulta = Reserva.query.options(
        joinedload(Reserva.usuario), joinedload(Reserva.recurso),
        joinedload(Reserva.grupo).joinedload(ReservaGrupo.integrantes),
    )

    if desde or hasta:
        if hasta:
            consulta = consulta.filter(Reserva.inicio < hasta)
        if desde:
            consulta = consulta.filter(Reserva.fin > desde)
        reservas = consulta.order_by(Reserva.inicio.asc()).limit(2000).all()
    else:
        reservas = consulta.order_by(Reserva.creado_en.desc()).limit(500).all()

    return jsonify([_serializar_reserva(r) for r in reservas])


# Campos que el calendario puede modificar de una reserva. No incluye
# `usuario_id` ni `grupo_id`: reasignar una reserva a otra persona o moverla
# de carrito no es una edición del calendario, es otra reserva.
_CAMPOS_EDITABLES_RESERVA = {"inicio", "fin", "estado", "recurso_id"}

# Ver el comentario de la columna `estado` en postgres/init/02-schema.sql.
_ESTADOS_RESERVA = {"confirmada", "pendiente", "cancelada", "completada", "no_show", "denegada"}

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

    cambios, error = _preparar_cambios_reserva(
        request.get_json(silent=True) or {}, _CAMPOS_EDITABLES_RESERVA
    )
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


@bp.route("/api/reservas/grupo/<int:grupo_id>", methods=["PATCH"])
def api_actualizar_reserva_grupo(grupo_id):
    """Actualiza horario/estado y activos de una reserva completa atomically."""
    datos = request.get_json(silent=True) or {}
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
    cambios, error = _preparar_cambios_reserva(datos, _CAMPOS_EDITABLES_RESERVA)
    if error:
        return jsonify({"error": error}), 400

    if tiene_tipo_uso and tipo_uso_nuevo not in ("particular", "asignatura"):
        return jsonify({"error": "El uso debe ser 'particular' o 'asignatura'."}), 400

    if tiene_modalidad and modalidad_nueva not in ("individual", "grupo"):
        return jsonify({"error": "La modalidad debe ser 'individual' o 'grupo'."}), 400

    if tiene_sale_area and not isinstance(sale_area_nuevo, bool):
        return jsonify({"error": "'sale_de_barranquilla' debe ser true o false."}), 400

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
        if tipo_uso_efectivo == "asignatura":
            nrc_efectivo = nrc_nuevo if tiene_nrc else grupo.nrc
            if not isinstance(nrc_efectivo, str) or not re.fullmatch(r"\d{4}", nrc_efectivo):
                return jsonify({"error": "El NRC debe tener exactamente 4 dígitos."}), 400
            grupo.tipo_uso = "asignatura"
            grupo.nrc = nrc_efectivo
        else:
            grupo.tipo_uso = "particular"
            grupo.nrc = None

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
            if not isinstance(integrantes_efectivos, list) or not (
                recursos_service.TAMANO_GRUPO_MIN <= len(integrantes_efectivos) <= recursos_service.TAMANO_GRUPO_MAX
            ):
                return jsonify({
                    "error": f"El grupo debe tener entre {recursos_service.TAMANO_GRUPO_MIN} y "
                             f"{recursos_service.TAMANO_GRUPO_MAX} integrantes."
                }), 400

            codigos_validados = []
            for codigo in integrantes_efectivos:
                codigo_valido = registro_service.validar_codigo_uninorte(codigo) if isinstance(codigo, str) else None
                if codigo_valido is None:
                    return jsonify({"error": f"El ID uninorte '{codigo}' no es válido."}), 400
                codigos_validados.append(codigo_valido)
            if len(codigos_validados) != len(set(codigos_validados)):
                return jsonify({"error": "No puede haber integrantes repetidos."}), 400
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
    # advierte (ver ReservaDetalle.tsx); la CPA tiene la última palabra.
    if tiene_sale_area:
        grupo.sale_de_barranquilla = sale_area_nuevo

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
        # Un activo que agrega la CPA desde aquí ya lo aprobó ella misma: no
        # nace pendiente aunque la línea que se toma de base lo esté.
        estado_nuevo = cambios.get("estado")
        if estado_nuevo is None:
            estado_nuevo = (
                "confirmada"
                if reserva_base.estado == recursos_service.ESTADO_PENDIENTE
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
    """La CPA aprueba los activos de una reserva que esperaban su
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
    enviado = _avisar_estudiante(reservas[0].usuario, texto, "confirmacion_cpa")
    return jsonify({
        "reservas": [_serializar_reserva(r) for r in reservas],
        "notificado": enviado,
    })


@bp.route("/api/reservas/<int:reserva_id>/<any(confirmar, denegar):decision>", methods=["POST"])
def api_decidir_reserva(reserva_id, decision):
    """Confirma o deniega un solo activo pendiente de una reserva (un botón
    por activo en el modal) y le avisa al estudiante qué decidió la CPA y
    cómo quedó el resto (recursos_service.mensaje_decision_cpa).

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
    texto = recursos_service.mensaje_decision_cpa(decidida, reservas)
    intent = "confirmacion_cpa" if decision == "confirmar" else "denegacion_cpa"
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


def _avisar_estudiante(usuario: Usuario, texto: str, intent: str) -> bool:
    """Le escribe al estudiante sin un mensaje suyo al que responder (ver
    whatsapp_client.jid_de) y lo deja en la auditoría como cualquier mensaje
    del bot: así el ack de entrega de Baileys (/webhook/ack) lo encuentra por
    mensaje_id. Devuelve si Baileys aceptó el envío."""
    enviado, mensaje_id = whatsapp_client.enviar_mensaje_texto(whatsapp_client.jid_de(usuario), texto)
    guardrails.registrar_interaccion(
        usuario.telefono_wa, None, intent, texto, permitido=True,
        mensaje_id=mensaje_id,
        estado_envio="pendiente" if enviado else "error_http",
    )
    return enviado


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

# Debe coincidir con el CHECK `recursos_estado_valido` de
# postgres/init/02-schema.sql. Solo 'disponible' es reservable (ver
# recursos_service.ESTADO_DISPONIBLE) -- 'en_mantenimiento' y
# 'no_disponible' quedan fuera del catálogo que ve el bot.
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

    if "estado" in datos and datos["estado"] not in _ESTADOS_VALIDOS:
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
