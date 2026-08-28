-- Esquema inicial de la app de reservas.
-- Ajusta los campos a tu modelo de datos actual de la CPA antes de migrar
-- la información real (la idea es que este esquema pueda recibir esos datos
-- sin perder información).

\connect reservas_db

-- Necesaria para la restricción EXCLUDE con rangos de tipos "normales"
-- (INTEGER, TIMESTAMPTZ) más abajo -- sin esto solo se puede usar EXCLUDE
-- con tipos de rango nativos (int4range, tsrange, etc).
CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE IF NOT EXISTS usuarios (
    id                SERIAL PRIMARY KEY,
    -- NULL hasta que el flujo de registro por WhatsApp lo complete (ver
    -- flask-app/app/registro_service.py) -- no se conoce en el momento en
    -- que se crea la fila (primer contacto, antes de preguntarle nada).
    nombre            VARCHAR(150),
    telefono_wa       VARCHAR(20) UNIQUE NOT NULL,   -- número de WhatsApp (con código país)
    codigo_uninorte   VARCHAR(30),                   -- id de estudiante
    cedula            VARCHAR(20),
    registro_completo BOOLEAN NOT NULL DEFAULT FALSE, -- true cuando ya dio nombre+codigo+cedula
    rol               VARCHAR(30) NOT NULL DEFAULT 'estudiante', -- estudiante | staff | admin
    activo            BOOLEAN NOT NULL DEFAULT TRUE,
    creado_en         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS recursos (
    id              SERIAL PRIMARY KEY,
    tipo            VARCHAR(20) NOT NULL CHECK (tipo IN ('equipo', 'espacio')),
    nombre          VARCHAR(150) NOT NULL,
    descripcion     TEXT,
    ubicacion       VARCHAR(150),
    -- Los siguientes campos son de inventario físico y solo aplican a
    -- tipo='equipo' (un espacio -- sala, estudio -- no tiene placa,
    -- categoría, marca, modelo ni número de serie/IMEI). Ver constraints
    -- recursos_placa_requerida_equipo / recursos_categoria_requerida_equipo
    -- más abajo.
    placa           VARCHAR(8),                      -- número de placa/inventario, 8 dígitos, único
    -- Categoría del equipo (ej. 'camara', 'celular', 'dron', 'microfono',
    -- 'tripode', 'luces') -- distinta de la columna `tipo` de arriba
    -- (equipo/espacio). Permite que el bot responda "qué cámaras tenemos"
    -- o sugiera otras marcas/modelos de la misma categoría cuando el
    -- modelo pedido no existe. Texto libre a propósito (no un ENUM fijo)
    -- para no tener que migrar el esquema cada vez que se agregue una
    -- categoría nueva de activo.
    categoria       VARCHAR(50),
    marca           VARCHAR(100),
    modelo          VARCHAR(100),
    identificador   VARCHAR(100),                     -- número de serie o IMEI (alfanumérico), si lo tiene
    requiere_seguro BOOLEAN NOT NULL DEFAULT FALSE,  -- activa secuencia de seguro de equipos
    activo          BOOLEAN NOT NULL DEFAULT TRUE,
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT recursos_placa_formato CHECK (placa IS NULL OR placa ~ '^[0-9]{8}$'),
    CONSTRAINT recursos_placa_requerida_equipo CHECK (tipo <> 'equipo' OR placa IS NOT NULL),
    CONSTRAINT recursos_categoria_requerida_equipo CHECK (tipo <> 'equipo' OR categoria IS NOT NULL)
);

-- Único por activo, pero permite múltiples NULL (para 'espacio', que no
-- tiene placa) ya que Postgres no cuenta NULL como duplicado en UNIQUE.
CREATE UNIQUE INDEX IF NOT EXISTS idx_recursos_placa_unica ON recursos (placa);

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

-- Garantía real (no solo un índice) de que dos reservas confirmadas del
-- mismo recurso nunca se solapen en el tiempo, aunque la app tenga un bug
-- o dos requests concurrentes pasen el chequeo de la aplicación a la vez.
-- Postgres rechaza el INSERT/UPDATE con un error de exclusión si detecta
-- solapamiento; recursos_service.crear_reserva() atrapa ese error.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'reservas_no_solapamiento'
    ) THEN
        ALTER TABLE reservas ADD CONSTRAINT reservas_no_solapamiento
            EXCLUDE USING gist (
                recurso_id WITH =,
                tstzrange(inicio, fin) WITH &&
            ) WHERE (estado = 'confirmada');
    END IF;
END $$;

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
    mensaje_id      VARCHAR(64),                    -- id del mensaje en WhatsApp (lo asigna Baileys)
    estado_envio    VARCHAR(20) NOT NULL DEFAULT 'n/a', -- n/a | error_http | pendiente | enviado | entregado | leido | reproducido | fallido
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Migración para bases ya creadas antes de que existieran estas columnas
-- (postgres/init/*.sql solo corre la primera vez que se crea el volumen).
ALTER TABLE auditoria_bot ADD COLUMN IF NOT EXISTS mensaje_id VARCHAR(64);
ALTER TABLE auditoria_bot ADD COLUMN IF NOT EXISTS estado_envio VARCHAR(20) NOT NULL DEFAULT 'n/a';

ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS cedula VARCHAR(20);
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS registro_completo BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE usuarios ALTER COLUMN nombre DROP NOT NULL;

ALTER TABLE recursos ADD COLUMN IF NOT EXISTS placa VARCHAR(8);
ALTER TABLE recursos ADD COLUMN IF NOT EXISTS marca VARCHAR(100);
ALTER TABLE recursos ADD COLUMN IF NOT EXISTS modelo VARCHAR(100);
ALTER TABLE recursos ADD COLUMN IF NOT EXISTS identificador VARCHAR(100);
ALTER TABLE recursos ADD COLUMN IF NOT EXISTS categoria VARCHAR(50);

CREATE UNIQUE INDEX IF NOT EXISTS idx_recursos_placa_unica ON recursos (placa);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'recursos_placa_formato'
    ) THEN
        ALTER TABLE recursos ADD CONSTRAINT recursos_placa_formato
            CHECK (placa IS NULL OR placa ~ '^[0-9]{8}$');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'recursos_placa_requerida_equipo'
    ) THEN
        ALTER TABLE recursos ADD CONSTRAINT recursos_placa_requerida_equipo
            CHECK (tipo <> 'equipo' OR placa IS NOT NULL);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'recursos_categoria_requerida_equipo'
    ) THEN
        ALTER TABLE recursos ADD CONSTRAINT recursos_categoria_requerida_equipo
            CHECK (tipo <> 'equipo' OR categoria IS NOT NULL);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_auditoria_telefono_fecha ON auditoria_bot (telefono_wa, creado_en);
CREATE INDEX IF NOT EXISTS idx_auditoria_mensaje_id ON auditoria_bot (mensaje_id);

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
