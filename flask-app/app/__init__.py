from flask import Flask, jsonify

from app.config import Config
from app.extensions import db, limiter


def create_app(config_class: type = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    limiter.init_app(app)

    with app.app_context():
        from app.routes import analytics, dashboard, webhook

        app.register_blueprint(webhook.bp)
        app.register_blueprint(analytics.bp)
        app.register_blueprint(dashboard.bp)

        db.create_all()  # crea tablas si no existen (idempotente)

        from app.seed import registrar_cli

        registrar_cli(app)

        # Hilo de fondo que dispara el no-show automático y los avisos de
        # recogida (ver app/ticker.py y app/avisos_reservas.py). Va después
        # de db.create_all() para no competir con la creación de tablas.
        from app import ticker

        ticker.iniciar(app)

    @app.route("/health")
    def health():
        return jsonify({"status": "ok"})

    return app
