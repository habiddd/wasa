import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "changeme")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Baileys (WhatsApp no oficial, gratuito)
    WHATSAPP_API_URL = os.environ.get("WHATSAPP_API_URL", "http://baileys-bot:3000")
    WHATSAPP_API_KEY = os.environ.get("WHATSAPP_API_KEY")

    # Canal oficial de Meta (WhatsApp Cloud API). El canal activo se elige en
    # el dashboard (Servicios); aquí solo las credenciales y el canal por
    # defecto. Vacías = no se puede cambiar a Meta (ver
    # whatsapp_client.cambiar_proveedor). .strip() por lo mismo que LLM_BASE_URL.
    WHATSAPP_PROVEEDOR_DEFAULT = os.environ.get("WHATSAPP_PROVEEDOR_DEFAULT", "baileys").strip() or "baileys"
    META_WA_TOKEN = os.environ.get("META_WA_TOKEN", "").strip()
    META_WA_PHONE_NUMBER_ID = os.environ.get("META_WA_PHONE_NUMBER_ID", "").strip()
    META_WA_WABA_ID = os.environ.get("META_WA_WABA_ID", "").strip()
    META_APP_SECRET = os.environ.get("META_APP_SECRET", "").strip()
    META_WEBHOOK_VERIFY_TOKEN = os.environ.get("META_WEBHOOK_VERIFY_TOKEN", "").strip()
    META_GRAPH_VERSION = os.environ.get("META_GRAPH_VERSION", "v25.0").strip() or "v25.0"
    META_PLANTILLA_AVISO = os.environ.get("META_PLANTILLA_AVISO", "").strip()
    META_PLANTILLA_IDIOMA = os.environ.get("META_PLANTILLA_IDIOMA", "es").strip() or "es"

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

    # Ciclo de avisos y no-show automático (ver app/avisos_reservas.py y
    # app/ticker.py).
    # Minutos después de `inicio` sin acta de entrega para que una
    # 'confirmada' pase sola a 'no_show'.
    NO_SHOW_MARGEN_MIN = int(os.environ.get("NO_SHOW_MARGEN_MIN", 30))
    # Minutos ANTES de `inicio` para el aviso "previo".
    AVISO_PREVIO_MIN = int(os.environ.get("AVISO_PREVIO_MIN", 30))
    # Minutos ANTES de `fin` para el aviso "devolucion" (reservas entregadas).
    AVISO_DEVOLUCION_MIN = int(os.environ.get("AVISO_DEVOLUCION_MIN", 30))
    # Corte de frescura: un aviso solo se manda si su momento ideal cayó
    # dentro de los últimos AVISO_VENTANA_MIN minutos -- si el contenedor
    # estuvo caído más que eso, el barrido igual deja la fila en
    # reserva_avisos (con motivo='vencido') para no reintentarlo nunca, pero
    # ya no manda el mensaje: un recordatorio de recogida que llega dos horas
    # tarde es información incorrecta, no un aviso tardío.
    AVISO_VENTANA_MIN = int(os.environ.get("AVISO_VENTANA_MIN", 120))
    # Cada cuánto despierta el hilo ticker (ver app/ticker.py) a correr el
    # barrido dentro del propio contenedor flask-app.
    BARRIDO_INTERVALO_SEG = int(os.environ.get("BARRIDO_INTERVALO_SEG", 60))
    # Apaga el hilo ticker (ej. para pruebas locales que no quieren un hilo de
    # fondo compitiendo). El barrido perezoso del listado de reservas sigue
    # activo igual: no depende de esta variable.
    BARRIDO_ACTIVO = os.environ.get("BARRIDO_ACTIVO", "1") != "0"
    # Días que se guarda el texto y el teléfono de auditoria_bot antes de
    # anonimizarlos (guardrails.anonimizar_auditoria_vieja). 0 = no anonimizar.
    AUDITORIA_RETENCION_DIAS = int(os.environ.get("AUDITORIA_RETENCION_DIAS", 180))
