import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "changeme")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Baileys (WhatsApp no oficial, gratuito)
    WHATSAPP_API_URL = os.environ.get("WHATSAPP_API_URL", "http://baileys-bot:3000")
    WHATSAPP_API_KEY = os.environ.get("WHATSAPP_API_KEY")

    # LLM local, vía API compatible con OpenAI (Ollama / LM Studio).
    # .strip() porque un espacio de más al final en .env (fácil de dejar sin
    # querer) rompe la URL silenciosamente.
    LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://192.168.3.167:9000").strip()
    LLM_MODEL = os.environ.get("LLM_MODEL", "llama3.1").strip()
    # Opcional: algunos servidores compatibles con OpenAI exigen
    # "Authorization: Bearer <key>" aunque sea local. LM Studio / Ollama no
    # lo requieren, así que puede quedar vacío.
    LLM_API_KEY = os.environ.get("LLM_API_KEY", "").strip() or None

    # Página web con el catálogo completo de equipos y espacios. Cuando una
    # consulta de disponibilidad no identifica ningún activo, el bot manda
    # este enlace en vez de listar todo el inventario (ver
    # recursos_service.evaluar_consulta_disponibilidad). Vacía = todavía no
    # hay página: se conserva el listado completo de antes. .strip() por lo
    # mismo que LLM_BASE_URL: un espacio de más en .env rompe el enlace.
    CATALOGO_URL = os.environ.get("CATALOGO_URL", "").strip()

    # Guardrails
    MAX_MESSAGES_PER_MINUTE = int(os.environ.get("MAX_MESSAGES_PER_MINUTE", 8))
    # "saludo" está permitido a propósito: es conversación social sin pedido
    # concreto, y se responde con texto que no consulta ni afirma nada de la
    # base de datos (ver llm_client.responder_charla). "otro" NO debe
    # agregarse aquí: es el único que sigue cayendo al menú de fallback, que
    # es el guardrail (ver routes/webhook.py).
    ALLOWED_INTENTS = set(
        (
            os.environ.get(
                "ALLOWED_INTENTS", "reservar,consultar_disponibilidad,cancelar,ayuda,saludo"
            )
        ).split(",")
    )

    # Redis (por ahora solo usado para el chequeo de salud del dashboard;
    # ver TODO en docs/SEGURIDAD.md sobre mover el rate limiter aquí)
    REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")

    # Dashboard interno (/dashboard) protegido con autenticación básica
    DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "admin")
    DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD")

    # Actas de entrega/devolución firmadas o subidas (ver app/actas_service.py).
    # Carpeta dentro del volumen con nombre `actas_pdf` (ver docker-compose.yml)
    # -- tiene que sobrevivir a `docker compose down` y a reconstruir la imagen,
    # porque son el histórico probatorio.
    ACTAS_DIR = os.environ.get("ACTAS_DIR", "/app/actas")
    ACTAS_MAX_BYTES = int(os.environ.get("ACTAS_MAX_BYTES", 10 * 1024 * 1024))
    # Tope real de Flask para el body completo del multipart, no solo el PDF:
    # el propio multipart (boundary, cabeceras de cada parte, el resto de
    # campos del form) suma unos cuantos KB encima del archivo. Sin este
    # margen, un PDF que sí pasa ACTAS_MAX_BYTES podría rechazarse igual con
    # un 413 antes de que el código de la ruta llegue a validarlo.
    MAX_CONTENT_LENGTH = ACTAS_MAX_BYTES + 1 * 1024 * 1024
