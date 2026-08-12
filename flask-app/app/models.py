from datetime import datetime

from app.extensions import db


class Usuario(db.Model):
    __tablename__ = "usuarios"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    telefono_wa = db.Column(db.String(20), unique=True, nullable=False)
    codigo_uninorte = db.Column(db.String(30))
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
    requiere_seguro = db.Column(db.Boolean, nullable=False, default=False)
    activo = db.Column(db.Boolean, nullable=False, default=True)
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
    creado_en = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)

    usuario = db.relationship("Usuario")
    recurso = db.relationship("Recurso")


class AuditoriaBot(db.Model):
    __tablename__ = "auditoria_bot"

    id = db.Column(db.Integer, primary_key=True)
    telefono_wa = db.Column(db.String(20), nullable=False)
    intent_detectado = db.Column(db.String(50))
    mensaje_entrante = db.Column(db.Text)
    respuesta_enviada = db.Column(db.Text)
    permitido = db.Column(db.Boolean, nullable=False, default=True)
    motivo_bloqueo = db.Column(db.Text)
    creado_en = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)
