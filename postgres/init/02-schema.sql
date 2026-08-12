-- Esquema inicial de la app de reservas.
-- Ajusta los campos a tu modelo de datos actual de la CPA antes de migrar
-- la información real (la idea es que este esquema pueda recibir esos datos
-- sin perder información).

\connect reservas_db

CREATE TABLE IF NOT EXISTS usuarios (
    id              SERIAL PRIMARY KEY,
    nombre          VARCHAR(150) NOT NULL,
    telefono_wa     VARCHAR(20) UNIQUE NOT NULL,   -- número de WhatsApp (con código país)
    codigo_uninorte VARCHAR(30),
    rol             VARCHAR(30) NOT NULL DEFAULT 'estudiante', -- estudiante | staff | admin
    activo          BOOLEAN NOT NULL DEFAULT TRUE,
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS recursos (
    id              SERIAL PRIMARY KEY,
    tipo            VARCHAR(20) NOT NULL CHECK (tipo IN ('equipo', 'espacio')),
    nombre          VARCHAR(150) NOT NULL,
    descripcion     TEXT,
    ubicacion       VARCHAR(150),
    requiere_seguro BOOLEAN NOT NULL DEFAULT FALSE,  -- activa secuencia de seguro de equipos
    activo          BOOLEAN NOT NULL DEFAULT TRUE,
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reservas (
    id              SERIAL PRIMARY KEY,
    usuario_id      INTEGER NOT NULL REFERENCES usuarios(id),
    recurso_id      INTEGER NOT NULL REFERENCES recursos(id),
    inicio          TIMESTAMPTZ NOT NULL,
    fin             TIMESTAMPTZ NOT NULL,
    estado          VARCHAR(20) NOT NULL DEFAULT 'confirmada', -- confirmada | cancelada | completada | no_show
    canal_origen    VARCHAR(20) NOT NULL DEFAULT 'whatsapp',
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT rango_valido CHECK (fin > inicio)
);

-- Evita choques de horario para el mismo recurso a nivel de base de datos
CREATE INDEX IF NOT EXISTS idx_reservas_recurso_rango ON reservas (recurso_id, inicio, fin);

CREATE TABLE IF NOT EXISTS secuencias_seguro (
    id              SERIAL PRIMARY KEY,
    reserva_id      INTEGER NOT NULL REFERENCES reservas(id),
    paso            VARCHAR(50) NOT NULL,  -- ej: 'checklist_enviado', 'firmado', 'equipo_entregado'
    completado      BOOLEAN NOT NULL DEFAULT FALSE,
    completado_en   TIMESTAMPTZ
);

-- Auditoría: registro de cada interacción relevante del bot (guardrails/trazabilidad)
CREATE TABLE IF NOT EXISTS auditoria_bot (
    id              SERIAL PRIMARY KEY,
    telefono_wa     VARCHAR(20) NOT NULL,
    intent_detectado VARCHAR(50),
    mensaje_entrante TEXT,
    respuesta_enviada TEXT,
    permitido       BOOLEAN NOT NULL DEFAULT TRUE,  -- false si un guardrail lo bloqueó
    motivo_bloqueo  TEXT,
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_auditoria_telefono_fecha ON auditoria_bot (telefono_wa, creado_en);

-- Este script corre con el superusuario de Postgres (ver 01-init.sql), así
-- que las tablas de arriba quedan con OWNER postgres. GRANT ALL PRIVILEGES
-- ON DATABASE (en 01-init.sql) solo da privilegios a nivel de base de datos,
-- no de tabla, así que sin esto :db_user (el que usa la app Flask) no puede
-- ni leer ni escribir en ninguna de ellas.
\set db_user `echo $APP_DB_USER`

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"db_user";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"db_user";
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"db_user";
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO :"db_user";
