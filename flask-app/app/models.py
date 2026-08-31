from datetime import datetime

from app.extensions import db


class Usuario(db.Model):
    __tablename__ = "usuarios"

    id = db.Column(db.Integer, primary_key=True)
    # nullable: se completa vía el flujo de registro por WhatsApp (ver
    # registro_service.py), no siempre se conoce en el momento en que se
    # crea el Usuario (primer contacto, antes de preguntarle nada).
    nombre = db.Column(db.String(150))
    telefono_wa = db.Column(db.String(20), unique=True, nullable=False)
    codigo_uninorte = db.Column(db.String(30))
    cedula = db.Column(db.String(20))
    registro_completo = db.Column(db.Boolean, nullable=False, default=False)
    rol = db.Column(db.String(30), nullable=False, default="estudiante")
    activo = db.Column(db.Boolean, nullable=False, default=True)
    creado_en = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)


class Recurso(db.Model):
    __tablename__ = "recursos"

    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(20), nullable=False)  # 'equipo' | 'espacio'
    nombre = db.Column(db.String(150), nullable=False)
    descripcion = db.Column(db.Text)
    ubicacion = db.Column(db.String(150))
    # Inventario físico -- solo aplica a tipo='equipo' (ver constraints
    # recursos_placa_formato / recursos_placa_requerida_equipo en
    # postgres/init/02-schema.sql). Un 'espacio' los deja en NULL.
    placa = db.Column(db.String(10), unique=True)         # placa/inventario: 7-8 caracteres alfanuméricos
    # FK al equipo principal cuando este recurso es un accesorio/hijo que
    # viene en bundle con otro equipo (ej. los lentes de una cámara). NULL
    # si el equipo no es hijo de otro.
    padre_id = db.Column(db.Integer, db.ForeignKey("recursos.id"))
    # Solo tiene sentido cuando padre_id no es None: si este hijo se agrega
    # automáticamente a la reserva del padre (ej. los lentes que siempre van
    # con la cámara) o si hay que pedirlo aparte.
    incluido_por_defecto = db.Column(db.Boolean, nullable=False, default=True)
    categoria = db.Column(db.String(50))                  # 'camara' | 'celular' | 'dron' | ... (texto libre)
    marca = db.Column(db.String(100))
    modelo = db.Column(db.String(100))
    identificador = db.Column(db.String(100))             # número de serie o IMEI, si lo tiene
    requiere_seguro = db.Column(db.Boolean, nullable=False, default=False)
    activo = db.Column(db.Boolean, nullable=False, default=True)
    creado_en = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)

    padre = db.relationship("Recurso", remote_side=[id], backref="hijos")


class ReservaGrupo(db.Model):
    __tablename__ = "reserva_grupos"

    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    creado_en = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)


class Reserva(db.Model):
    __tablename__ = "reservas"

    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    recurso_id = db.Column(db.Integer, db.ForeignKey("recursos.id"), nullable=False)
    inicio = db.Column(db.DateTime(timezone=True), nullable=False)
    fin = db.Column(db.DateTime(timezone=True), nullable=False)
    estado = db.Column(db.String(20), nullable=False, default="confirmada")
    canal_origen = db.Column(db.String(20), nullable=False, default="whatsapp")
    # NULL en reservas creadas antes de que existiera el carrito (ver
    # ReservaGrupo). Toda reserva nueva creada por el wizard de
    # recursos_service.py comparte grupo_id con las demás confirmadas junto
    # con ella en la misma sesión (ej. una cámara + sus lentes).
    grupo_id = db.Column(db.Integer, db.ForeignKey("reserva_grupos.id"))
    creado_en = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)

    usuario = db.relationship("Usuario")
    recurso = db.relationship("Recurso")
    grupo = db.relationship("ReservaGrupo", backref="reservas")


class AuditoriaBot(db.Model):
    __tablename__ = "auditoria_bot"

    id = db.Column(db.Integer, primary_key=True)
    telefono_wa = db.Column(db.String(20), nullable=False)
    intent_detectado = db.Column(db.String(50))
    mensaje_entrante = db.Column(db.Text)
    respuesta_enviada = db.Column(db.Text)
    permitido = db.Column(db.Boolean, nullable=False, default=True)
    motivo_bloqueo = db.Column(db.Text)
    # Id del mensaje de WhatsApp (lo devuelve Baileys al enviar) y su último
    # estado de entrega conocido, actualizado vía POST /webhook/ack cuando
    # Baileys recibe el ack de WhatsApp (server_ack/delivery_ack/leido/error).
    mensaje_id = db.Column(db.String(64))
    estado_envio = db.Column(db.String(20), nullable=False, default="n/a")
    creado_en = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)
