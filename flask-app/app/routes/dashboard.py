"""
Dashboard interno de monitoreo para el equipo de la CPA: estado de los
servicios (Postgres, Redis, Baileys/WhatsApp, LLM local), código QR de
vinculación y métricas básicas de uso. Protegido con autenticación básica
(DASHBOARD_USER / DASHBOARD_PASSWORD) porque expone el QR de vinculación
de WhatsApp, que no debería poder escanear cualquiera con acceso a la red.
"""

import base64
import io
import secrets
from datetime import datetime, timedelta

import qrcode
import requests
from flask import Blueprint, Response, current_app, jsonify, render_template, request
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError

from app import estado_bot
from app.extensions import db, redis_client
from app.models import AuditoriaBot, Recurso, Reserva, Usuario

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

@bp.route("/")
def index():
    return render_template("dashboard.html")


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
    "marca",
    "modelo",
    "identificador",
    "requiere_seguro",
    "estado",
    "padre_id",
}

# Debe coincidir con el CHECK `recursos_estado_valido` de
# postgres/init/02-schema.sql. Solo 'disponible' es reservable (ver
# recursos_service.ESTADO_DISPONIBLE) -- 'en_mantenimiento' y
# 'no_disponible' quedan fuera del catálogo que ve el bot.
_ESTADOS_VALIDOS = {"disponible", "en_mantenimiento", "no_disponible"}

# Traduce violaciones de constraints de Postgres (ver postgres/init/02-schema.sql)
# a mensajes legibles, en vez de exponer el error crudo de asyncpg/psycopg.
_MENSAJES_INTEGRIDAD = {
    "recursos_placa_formato": "La placa de un padre debe ser 8 dígitos numéricos; la de un hijo, 7-10 caracteres alfanuméricos en mayúsculas.",
    "recursos_placa_requerida_equipo": "Los equipos requieren una placa.",
    "recursos_categoria_requerida_equipo": "Los equipos requieren una categoría.",
    "recursos_padre_no_self": "Un activo no puede ser su propio padre.",
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
        "categoria": r.categoria,
        "marca": r.marca,
        "modelo": r.modelo,
        "identificador": r.identificador,
        "requiere_seguro": r.requiere_seguro,
        "estado": r.estado,
        "creado_en": r.creado_en.isoformat() if r.creado_en else None,
    }


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
    if padre_id is not None and Recurso.query.get(padre_id) is None:
        # No hace falta chequear ciclos aquí: un recurso recién creado no
        # tiene descendientes todavía, así que no puede formar uno.
        return jsonify({"error": "El padre indicado no existe."}), 400

    recurso = Recurso(
        tipo=datos.get("tipo"),
        nombre=datos.get("nombre"),
        descripcion=datos.get("descripcion"),
        ubicacion=datos.get("ubicacion"),
        placa=datos.get("placa"),
        categoria=datos.get("categoria"),
        marca=datos.get("marca"),
        modelo=datos.get("modelo"),
        identificador=datos.get("identificador"),
        requiere_seguro=bool(datos.get("requiere_seguro", False)),
        estado=estado,
        padre_id=padre_id,
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

    campos_desconocidos = set(datos) - _CAMPOS_EDITABLES_ACTIVO
    if campos_desconocidos:
        return jsonify({"error": f"Campos no editables: {', '.join(sorted(campos_desconocidos))}"}), 400

    if "estado" in datos and datos["estado"] not in _ESTADOS_VALIDOS:
        return jsonify({"error": _MENSAJES_INTEGRIDAD["recursos_estado_valido"]}), 400

    if "padre_id" in datos:
        nuevo_padre_id = datos["padre_id"]
        if nuevo_padre_id is not None and nuevo_padre_id != recurso.padre_id:
            if nuevo_padre_id == recurso.id:
                return jsonify({"error": "Un activo no puede ser su propio padre."}), 400
            padre = Recurso.query.get(nuevo_padre_id)
            if padre is None:
                return jsonify({"error": "El padre indicado no existe."}), 400
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

    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        return jsonify({"error": _mensaje_integridad(exc)}), 400

    return jsonify(_serializar_recurso(recurso))
