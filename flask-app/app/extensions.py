import os

import redis
from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

db = SQLAlchemy()

# Limiter por IP a nivel de infraestructura (defensa adicional a los
# guardrails por número de WhatsApp que viven en guardrails.py)
limiter = Limiter(key_func=get_remote_address)

# Cliente de Redis, hoy solo para el chequeo de salud del dashboard.
# Timeouts cortos para que un Redis caído no cuelgue el healthcheck.
redis_client = redis.Redis.from_url(
    os.environ.get("REDIS_URL", "redis://redis:6379/0"),
    socket_connect_timeout=2,
    socket_timeout=2,
)
