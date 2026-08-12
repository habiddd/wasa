import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "changeme")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Baileys (WhatsApp no oficial, gratuito)
    WHATSAPP_API_URL = os.environ.get("WHATSAPP_API_URL", "http://baileys-bot:3000")
    WHATSAPP_API_KEY = os.environ.get("WHATSAPP_API_KEY")

    # LLM local (Ollama / LM Studio en tu red)
    LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://192.168.3.167:9000")
    LLM_MODEL = os.environ.get("LLM_MODEL", "llama3.1")

    # Guardrails
    MAX_MESSAGES_PER_MINUTE = int(os.environ.get("MAX_MESSAGES_PER_MINUTE", 8))
    ALLOWED_INTENTS = set(
        (os.environ.get("ALLOWED_INTENTS", "reservar,consultar_disponibilidad,cancelar,ayuda"))
        .split(",")
    )

    # Redis (por ahora solo usado para el chequeo de salud del dashboard;
    # ver TODO en docs/SEGURIDAD.md sobre mover el rate limiter aquí)
    REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")

    # Dashboard interno (/dashboard) protegido con autenticación básica
    DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "admin")
    DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD")
