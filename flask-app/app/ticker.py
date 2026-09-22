"""
Hilo de fondo mínimo que despierta cada BARRIDO_INTERVALO_SEG segundos a
correr `avisos_reservas.barrer()`, sin sumar APScheduler, Celery ni un
contenedor nuevo -- ver el docstring de app/avisos_reservas.py para el
porqué completo del diseño (ticker + barrido perezoso + lock de Redis +
UNIQUE en la base).

Un `threading.Thread(daemon=True)` por proceso de gunicorn: con
`--workers 2` (ver flask-app/Dockerfile) hay dos hilos ticker corriendo en
paralelo, cada uno en su propio proceso -- por eso `avisos_reservas.barrer`
está protegida con el lock de Redis y, de fondo, con el UNIQUE de
`reserva_avisos`.
"""

import threading
import time

from app import avisos_reservas


def _bucle(app):
    intervalo = app.config.get("BARRIDO_INTERVALO_SEG", 60)
    while True:
        # Duerme PRIMERO y barre después: al arrancar el contenedor, Postgres
        # todavía puede estar levantando (ver `depends_on` con
        # `condition: service_healthy` en docker-compose.yml, que ayuda pero
        # no es una garantía instantánea) -- no tiene sentido pegarle a la
        # base en el primer instante de vida del proceso.
        time.sleep(intervalo)
        try:
            if avisos_reservas.intentar_lock(intervalo):
                with app.app_context():
                    conteos = avisos_reservas.barrer()
                    if any(conteos.values()):
                        # .warning y no .info a propósito: este proyecto no
                        # configura logging propio, así que sin handler
                        # explícito Python solo saca por stderr WARNING para
                        # arriba (el "lastResort" de la librería estándar) --
                        # un .info() acá quedaría mudo en `docker compose
                        # logs`. Mismo motivo por el que el resto del código
                        # (whatsapp_client, sesiones, llm_client) tampoco usa
                        # .info en ningún lado.
                        app.logger.warning("Barrido de avisos_reservas: %s", conteos)
        except Exception:
            # Un hilo que muere en silencio es peor que uno que loguea y
            # sigue vivo para el próximo tick -- nunca dejar que una
            # excepción (Postgres caído, lo que sea) tumbe el ticker.
            app.logger.exception("Error en el tick del ticker de avisos_reservas")


def iniciar(app):
    """Lanza el hilo si BARRIDO_ACTIVO está activo (default sí). Pensado para
    poder apagarlo en pruebas locales que no quieren un hilo de fondo
    compitiendo -- el barrido perezoso del listado de reservas sigue activo
    igual, no depende de esta bandera."""
    if not app.config.get("BARRIDO_ACTIVO", True):
        app.logger.warning("Ticker de avisos_reservas desactivado (BARRIDO_ACTIVO=0).")
        return
    hilo = threading.Thread(target=_bucle, args=(app,), daemon=True, name="ticker-avisos-reservas")
    hilo.start()
    # .warning, no .info -- ver el comentario en _bucle.
    app.logger.warning(
        "Ticker de avisos_reservas iniciado (cada %s s).",
        app.config.get("BARRIDO_INTERVALO_SEG", 60),
    )
