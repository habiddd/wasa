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
    -- JID completo con el que escribe (ej. '573001234567@s.whatsapp.net' o
    -- '144186850431118@lid'). Hace falta para escribirle sin que haya un
    -- mensaje suyo al que responder (ej. avisarle que la CPA confirmó su
    -- reserva): rearmarlo como telefono_wa + '@s.whatsapp.net' no llega a
    -- las cuentas '@lid'. Se actualiza con cada mensaje entrante.
    jid_wa            VARCHAR(64),
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
    -- Número de placa/inventario, único. El equipo padre usa 8 dígitos
    -- numéricos terminados en 0 (ej. '12345670'). Un equipo hijo (accesorio
    -- que viene en bundle con un padre, ver `padre_id` abajo) puede tener
    -- su propia placa de 7-10 caracteres alfanuméricos: o la misma placa del
    -- padre con el último dígito distinto de 0 (ej. '12345671', '12345673'),
    -- o un código independiente con numeración propia (ej. 'LEN0022' para
    -- un lente, 'LEN0023' para otro).
    placa           VARCHAR(10),
    -- FK a recursos(id) del equipo principal cuando esta fila es un
    -- accesorio/hijo que viene en bundle con otro equipo (ej. los lentes de
    -- una cámara, el micrófono de un dron). NULL si el equipo no es hijo de
    -- otro (es un equipo principal o independiente). Permite que el bot
    -- responda "qué accesorios tiene la cámara X" (recursos con padre_id =
    -- id de la cámara) o "de qué equipo es hijo el lente Y" (seguir padre_id).
    padre_id        INTEGER REFERENCES recursos(id),
    -- FK a recursos(id) del espacio donde vive físicamente este activo (ej.
    -- la Sala 4 para un iMac que está ahí). Es una jerarquía DISTINTA e
    -- INDEPENDIENTE de `padre_id`: `padre_id` agrupa accesorios en bundle
    -- (la cámara y sus lentes), `espacio_id` dice en qué lugar está el
    -- activo. Un activo puede tener padre y no tener lugar, tener lugar y no
    -- tener padre, ambos, o ninguno. La fila referenciada debe tener
    -- tipo='espacio' -- eso se valida en la app (ver
    -- flask-app/app/routes/dashboard.py, _validar_espacio) y no con un CHECK
    -- porque Postgres no admite subqueries en un CHECK. Un espacio, en
    -- cambio, no tiene lugar ni padre: no se anidan lugares (ver
    -- recursos_espacio_sin_padre_ni_lugar).
    espacio_id      INTEGER REFERENCES recursos(id),
    -- Dos niveles de clasificación del equipo -- distintos de la columna
    -- `tipo` de arriba (equipo/espacio):
    -- `categoria` es la agrupación gruesa (ej. 'grabación audiovisual'; por
    -- ahora hay una sola, pero el campo existe para cuando se sumen más).
    -- `subcategoria` es el tipo concreto de equipo (ej. 'cámara', 'trípode',
    -- 'dron') -- el nivel sobre el que trabaja el asistente de reserva del
    -- bot (ver recursos_service._subcategorias_activas): "qué cámaras
    -- tenemos", sugerir otras marcas/modelos de la misma subcategoría cuando
    -- el modelo pedido no existe, etc. Ambos son texto libre a propósito (no
    -- un ENUM fijo) para no tener que migrar el esquema cada vez que se
    -- agregue una categoría o subcategoría nueva de activo.
    categoria       VARCHAR(50),
    subcategoria    VARCHAR(50),
    marca           VARCHAR(100),
    modelo          VARCHAR(100),
    identificador   VARCHAR(100),                     -- número de serie o IMEI (alfanumérico), si lo tiene
    requiere_confirmacion BOOLEAN NOT NULL DEFAULT FALSE,  -- el equipo de la CPA debe confirmar el préstamo de este activo (ver recursos_service._nota_requiere_confirmacion)
    -- Estado de inventario del recurso -- no confundir con el estado de
    -- agenda que calcula recursos_service._estado_recurso() a partir de
    -- `reservas` ("en uso ahora mismo", "libre hasta tal hora"): este es
    -- manual, lo fija quien administra el inventario desde el dashboard.
    -- Antes era un simple BOOLEAN `activo` -- ver migración más abajo.
    estado          VARCHAR(20) NOT NULL DEFAULT 'disponible',
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- El padre es siempre 8 dígitos numéricos (ver comentario de `placa`
    -- arriba); un hijo (padre_id NOT NULL) puede ser alfanumérico, de 7 a
    -- 10 caracteres.
    CONSTRAINT recursos_placa_formato CHECK (
        placa IS NULL
        OR (padre_id IS NULL AND placa ~ '^[0-9]{8}$')
        OR (padre_id IS NOT NULL AND placa ~ '^[A-Z0-9]{7,10}$')
    ),
    CONSTRAINT recursos_placa_requerida_equipo CHECK (tipo <> 'equipo' OR placa IS NOT NULL),
    CONSTRAINT recursos_categoria_requerida_equipo CHECK (tipo <> 'equipo' OR categoria IS NOT NULL),
    CONSTRAINT recursos_padre_no_self CHECK (padre_id IS NULL OR padre_id <> id),
    CONSTRAINT recursos_espacio_no_self CHECK (espacio_id IS NULL OR espacio_id <> id),
    CONSTRAINT recursos_espacio_sin_padre_ni_lugar CHECK (tipo <> 'espacio' OR (padre_id IS NULL AND espacio_id IS NULL)),
    CONSTRAINT recursos_estado_valido CHECK (estado IN ('disponible', 'en_mantenimiento', 'no_disponible'))
);

-- Único por activo, pero permite múltiples NULL (para 'espacio', que no
-- tiene placa) ya que Postgres no cuenta NULL como duplicado en UNIQUE.
CREATE UNIQUE INDEX IF NOT EXISTS idx_recursos_placa_unica ON recursos (placa);

-- idx_recursos_padre_id se crea más abajo, después del bloque de migración
-- que agrega la columna `padre_id` -- en una base ya existente (donde el
-- CREATE TABLE de arriba se saltó por "already exists") esa columna todavía
-- no existe en este punto del script.

-- Agrupa las reservas creadas juntas en una misma confirmación de "carrito"
-- (ej. la cámara + sus lentes + una sala, todas en el mismo horario) -- ver
-- reservas.grupo_id abajo. Permite mostrarle al usuario su reserva completa
-- y, en el futuro, cancelarla en bloque.
CREATE TABLE IF NOT EXISTS reserva_grupos (
    id         SERIAL PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    creado_en  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reservas (
    id              SERIAL PRIMARY KEY,
    usuario_id      INTEGER NOT NULL REFERENCES usuarios(id),
    recurso_id      INTEGER NOT NULL REFERENCES recursos(id),
    inicio          TIMESTAMPTZ NOT NULL,
    fin             TIMESTAMPTZ NOT NULL,
    -- confirmada | pendiente | denegada | cancelada | completada | no_show.
    -- 'pendiente' es un activo con recursos.requiere_confirmacion que espera
    -- a que la CPA lo apruebe desde el dashboard; ocupa el activo igual que
    -- una confirmada (ver reservas_no_solapamiento). 'denegada': la CPA no lo
    -- aprobó; no ocupa el activo.
    estado          VARCHAR(20) NOT NULL DEFAULT 'confirmada',
    canal_origen    VARCHAR(20) NOT NULL DEFAULT 'whatsapp',
    -- Número de reserva: lo comparten todos los activos confirmados juntos
    -- en la misma sesión. Obligatorio (ver el bloque de migración al final,
    -- que rellena las filas anteriores al carrito y pone el NOT NULL).
    grupo_id        INTEGER REFERENCES reserva_grupos(id),
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT rango_valido CHECK (fin > inicio)
);

-- Evita choques de horario para el mismo recurso a nivel de base de datos
CREATE INDEX IF NOT EXISTS idx_reservas_recurso_rango ON reservas (recurso_id, inicio, fin);
-- idx_reservas_grupo_id se crea más abajo, después del bloque de migración
-- que agrega la columna `grupo_id` (mismo motivo que idx_recursos_padre_id
-- arriba: en una base ya existente esa columna no existe todavía aquí).

-- Garantía real (no solo un índice) de que dos reservas confirmadas del
-- mismo recurso nunca se solapen en el tiempo, aunque la app tenga un bug
-- o dos requests concurrentes pasen el chequeo de la aplicación a la vez.
-- Postgres rechaza el INSERT/UPDATE con un error de exclusión si detecta
-- solapamiento; recursos_service.crear_reserva() atrapa ese error.
-- Una reserva 'pendiente' (espera la confirmación de la CPA) también aparta
-- el activo: si no, otro estudiante podría reservarlo mientras la CPA decide.
-- La primera versión solo cubría 'confirmada'; se recrea si todavía es esa.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'reservas_no_solapamiento'
          AND pg_get_constraintdef(oid) NOT LIKE '%pendiente%'
    ) THEN
        ALTER TABLE reservas DROP CONSTRAINT reservas_no_solapamiento;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'reservas_no_solapamiento'
    ) THEN
        ALTER TABLE reservas ADD CONSTRAINT reservas_no_solapamiento
            EXCLUDE USING gist (
                recurso_id WITH =,
                tstzrange(inicio, fin) WITH &&
            ) WHERE (estado IN ('confirmada', 'pendiente'));
    END IF;
END $$;

-- Antes se llamaba `secuencias_seguro` (no es un seguro sino una
-- confirmación de la CPA). El renombre va aquí, antes del CREATE TABLE IF
-- NOT EXISTS de abajo y no con las demás migraciones del final: si corriera
-- después, en una base viva el CREATE ya habría creado una tabla
-- `secuencias_confirmacion` nueva y vacía, y el RENAME de la vieja chocaría
-- con ese nombre. Nadie usa esta tabla desde el código todavía, pero se
-- conserva por si ya tiene filas cargadas a mano.
DO $$
BEGIN
    IF to_regclass('secuencias_seguro') IS NOT NULL THEN
        ALTER TABLE secuencias_seguro RENAME TO secuencias_confirmacion;
        ALTER SEQUENCE secuencias_seguro_id_seq RENAME TO secuencias_confirmacion_id_seq;
        ALTER TABLE secuencias_confirmacion RENAME CONSTRAINT secuencias_seguro_pkey TO secuencias_confirmacion_pkey;
        ALTER TABLE secuencias_confirmacion RENAME CONSTRAINT secuencias_seguro_reserva_id_fkey TO secuencias_confirmacion_reserva_id_fkey;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS secuencias_confirmacion (
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
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS jid_wa VARCHAR(64);

ALTER TABLE recursos ADD COLUMN IF NOT EXISTS placa VARCHAR(10);
ALTER TABLE recursos ALTER COLUMN placa TYPE VARCHAR(10);
ALTER TABLE recursos ADD COLUMN IF NOT EXISTS marca VARCHAR(100);
ALTER TABLE recursos ADD COLUMN IF NOT EXISTS modelo VARCHAR(100);
ALTER TABLE recursos ADD COLUMN IF NOT EXISTS identificador VARCHAR(100);
ALTER TABLE recursos ADD COLUMN IF NOT EXISTS categoria VARCHAR(50);
ALTER TABLE recursos ADD COLUMN IF NOT EXISTS padre_id INTEGER REFERENCES recursos(id);
-- `categoria` cambió de significado: antes tenía el tipo concreto de equipo
-- ('cámara', 'trípode', 'dron'), ahora tiene una agrupación más gruesa y esos
-- valores finos viven en `subcategoria`. El backfill va dentro del IF (y no
-- como un UPDATE suelto) porque este script se re-ejecuta con psql contra la
-- base viva: un UPDATE incondicional pisaría las categorías nuevas que se
-- hayan creado después de esta migración.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'recursos' AND column_name = 'subcategoria'
    ) THEN
        ALTER TABLE recursos ADD COLUMN subcategoria VARCHAR(50);
        UPDATE recursos SET subcategoria = categoria WHERE categoria IS NOT NULL;
        UPDATE recursos SET categoria = 'grabación audiovisual' WHERE tipo = 'equipo';
    END IF;
END $$;
-- ADD COLUMN IF NOT EXISTS con REFERENCES es válido en PG16 y es idempotente
-- para la columna; si la columna ya existe simplemente no vuelve a crear la
-- FK (que ya estaría creada desde el CREATE TABLE o desde una corrida
-- anterior de esta misma línea), lo cual está bien.
ALTER TABLE recursos ADD COLUMN IF NOT EXISTS espacio_id INTEGER REFERENCES recursos(id);
-- Reemplazada por la regla fija "todo hijo va incluido por defecto con su
-- padre, el usuario lo quita de la selección si no lo quiere" (ver
-- recursos_service._accesorios_por_defecto) -- ya no es configurable por
-- activo.
ALTER TABLE recursos DROP COLUMN IF EXISTS incluido_por_defecto;

-- Renombre: la columna se llamaba "seguro" pero nunca fue un seguro, es una
-- confirmación que da el equipo de la CPA antes de prestar el activo. Solo
-- corre si la base todavía tiene el nombre viejo (una base recién creada ya
-- nace con `requiere_confirmacion` por el CREATE TABLE de arriba). El RENAME
-- conserva los valores existentes.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'recursos' AND column_name = 'requiere_seguro'
    ) THEN
        ALTER TABLE recursos RENAME COLUMN requiere_seguro TO requiere_confirmacion;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_recursos_placa_unica ON recursos (placa);
CREATE INDEX IF NOT EXISTS idx_recursos_padre_id ON recursos (padre_id);
-- idx_recursos_espacio_id va después del ALTER que agrega `espacio_id` por
-- el mismo motivo que idx_recursos_padre_id: en una base ya existente esa
-- columna no existe todavía en el CREATE TABLE de arriba (se saltó por
-- "already exists"), así que el índice solo puede crearse aquí.
CREATE INDEX IF NOT EXISTS idx_recursos_espacio_id ON recursos (espacio_id);

-- reserva_grupos ya está creada más arriba (CREATE TABLE IF NOT EXISTS,
-- antes de "reservas") incluso para una base inicializada antes de que
-- existiera -- este ALTER solo agrega la columna que la referencia.
ALTER TABLE reservas ADD COLUMN IF NOT EXISTS grupo_id INTEGER REFERENCES reserva_grupos(id);
CREATE INDEX IF NOT EXISTS idx_reservas_grupo_id ON reservas (grupo_id);

DO $$
BEGIN
    -- Se recrea (no solo "si no existe") porque el formato cambió de
    -- 8 dígitos numéricos a 7-8 caracteres alfanuméricos, para admitir
    -- placas de equipos hijo (ej. '12345671' o 'LEN0022'); luego para
    -- permitir hasta 10 caracteres en las placas de hijos; y de nuevo para
    -- volver a exigirle al padre (padre_id NULL) su formato propio de
    -- 8 dígitos numéricos, ya que el rango alfanumérico de 7-8 solo
    -- correspondía al hijo.
    ALTER TABLE recursos DROP CONSTRAINT IF EXISTS recursos_placa_formato;
    ALTER TABLE recursos ADD CONSTRAINT recursos_placa_formato
        CHECK (
            placa IS NULL
            OR (padre_id IS NULL AND placa ~ '^[0-9]{8}$')
            OR (padre_id IS NOT NULL AND placa ~ '^[A-Z0-9]{7,10}$')
        );
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
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'recursos_padre_no_self'
    ) THEN
        ALTER TABLE recursos ADD CONSTRAINT recursos_padre_no_self
            CHECK (padre_id IS NULL OR padre_id <> id);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'recursos_espacio_no_self'
    ) THEN
        ALTER TABLE recursos ADD CONSTRAINT recursos_espacio_no_self
            CHECK (espacio_id IS NULL OR espacio_id <> id);
    END IF;
END $$;

-- Un lugar no va dentro de otro lugar ni es hijo (padre_id) de nada: no es
-- un caso de uso de la CPA (decisión del 10/09/2026, ver
-- docs/SESION_2026-09-10.md). Antes se permitía anidar espacios (edificio >
-- piso > sala); las filas que lo usaban se desvinculan justo antes de crear
-- el CHECK, dentro del mismo IF, para que re-ejecutar el script no toque
-- nada una vez que la constraint existe.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'recursos_espacio_sin_padre_ni_lugar'
    ) THEN
        UPDATE recursos SET espacio_id = NULL, padre_id = NULL
            WHERE tipo = 'espacio' AND (espacio_id IS NOT NULL OR padre_id IS NOT NULL);
        ALTER TABLE recursos ADD CONSTRAINT recursos_espacio_sin_padre_ni_lugar
            CHECK (tipo <> 'espacio' OR (padre_id IS NULL AND espacio_id IS NULL));
    END IF;
END $$;

-- Migración: `activo` (BOOLEAN) -> `estado` (VARCHAR: 'disponible' |
-- 'en_mantenimiento' | 'no_disponible'). Solo corre si la base todavía
-- tiene la columna vieja (una base recién creada ya nace con `estado`
-- directamente, por el CREATE TABLE de arriba).
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'recursos' AND column_name = 'activo'
    ) THEN
        ALTER TABLE recursos ADD COLUMN IF NOT EXISTS estado VARCHAR(20);
        UPDATE recursos SET estado = CASE WHEN activo THEN 'disponible' ELSE 'no_disponible' END
            WHERE estado IS NULL;
        ALTER TABLE recursos ALTER COLUMN estado SET DEFAULT 'disponible';
        ALTER TABLE recursos ALTER COLUMN estado SET NOT NULL;
        ALTER TABLE recursos DROP COLUMN activo;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'recursos_estado_valido'
    ) THEN
        ALTER TABLE recursos ADD CONSTRAINT recursos_estado_valido
            CHECK (estado IN ('disponible', 'en_mantenimiento', 'no_disponible'));
    END IF;
END $$;

-- El id de `reserva_grupos` es el NÚMERO DE RESERVA: identifica de forma
-- única cada reserva hecha, que puede cubrir varios activos (una cámara y
-- sus lentes confirmados juntos comparten número). Las filas anteriores al
-- carrito quedaron con grupo_id NULL; se les crea un grupo por cada
-- combinación de usuario + horario, que es lo que en la práctica fue "una
-- reserva". Idempotente: si no quedan NULLs, el bucle no hace nada.
DO $$
DECLARE
    huerfana    RECORD;
    grupo_nuevo INTEGER;
BEGIN
    FOR huerfana IN
        SELECT usuario_id, inicio, fin, MIN(creado_en) AS creado_en
        FROM reservas
        WHERE grupo_id IS NULL
        GROUP BY usuario_id, inicio, fin
    LOOP
        INSERT INTO reserva_grupos (usuario_id, creado_en)
        VALUES (huerfana.usuario_id, huerfana.creado_en)
        RETURNING id INTO grupo_nuevo;

        UPDATE reservas
        SET grupo_id = grupo_nuevo
        WHERE grupo_id IS NULL
          AND usuario_id = huerfana.usuario_id
          AND inicio = huerfana.inicio
          AND fin = huerfana.fin;
    END LOOP;
END $$;

-- Ya sin NULLs, el número de reserva pasa a ser obligatorio: ninguna reserva
-- nueva puede quedarse sin él (ver recursos_service.crear_reservas_en_bloque
-- y seed.py, que crean el grupo antes que las reservas).
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'reservas'
          AND column_name = 'grupo_id'
          AND is_nullable = 'YES'
    ) THEN
        ALTER TABLE reservas ALTER COLUMN grupo_id SET NOT NULL;
    END IF;
END $$;

-- Uso de la reserva: particular (uso personal) o para una asignatura, en
-- cuyo caso lleva el NRC (identificador de la asignatura que cursa el
-- estudiante) -- ver recursos_service.crear_reservas_grupo.
ALTER TABLE reserva_grupos ADD COLUMN IF NOT EXISTS tipo_uso VARCHAR(20) NOT NULL DEFAULT 'particular';
ALTER TABLE reserva_grupos ADD COLUMN IF NOT EXISTS nrc VARCHAR(4);
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'reserva_grupos_tipo_uso_valido') THEN
    ALTER TABLE reserva_grupos ADD CONSTRAINT reserva_grupos_tipo_uso_valido CHECK (tipo_uso IN ('particular', 'asignatura'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'reserva_grupos_nrc_segun_tipo') THEN
    ALTER TABLE reserva_grupos ADD CONSTRAINT reserva_grupos_nrc_segun_tipo CHECK ((tipo_uso = 'asignatura') = (nrc IS NOT NULL));
  END IF;
  -- El NRC siempre son exactamente 4 dígitos. La primera versión de la
  -- columna era VARCHAR(10); aquí se angosta en una base que ya la tenía.
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'reserva_grupos'
      AND column_name = 'nrc'
      AND character_maximum_length <> 4
  ) THEN
    ALTER TABLE reserva_grupos ALTER COLUMN nrc TYPE VARCHAR(4);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'reserva_grupos_nrc_formato') THEN
    ALTER TABLE reserva_grupos ADD CONSTRAINT reserva_grupos_nrc_formato CHECK (nrc ~ '^[0-9]{4}$');
  END IF;
END $$;

-- Modalidad de la reserva: el estudiante reserva solo o en grupo. Es
-- independiente de tipo_uso (un grupo puede ser particular o de asignatura).
-- `tamano_grupo` INCLUYE a quien reserva; sus integrantes (por código
-- uninorte, el mismo dato que se pide en el registro) van en
-- reserva_grupo_integrantes -- ver recursos_service.crear_reservas_grupo.
ALTER TABLE reserva_grupos ADD COLUMN IF NOT EXISTS modalidad VARCHAR(20) NOT NULL DEFAULT 'individual';
ALTER TABLE reserva_grupos ADD COLUMN IF NOT EXISTS tamano_grupo SMALLINT;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'reserva_grupos_modalidad_valida') THEN
    ALTER TABLE reserva_grupos ADD CONSTRAINT reserva_grupos_modalidad_valida
      CHECK (modalidad IN ('individual', 'grupo'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'reserva_grupos_tamano_segun_modalidad') THEN
    ALTER TABLE reserva_grupos ADD CONSTRAINT reserva_grupos_tamano_segun_modalidad
      CHECK ((modalidad = 'grupo') = (tamano_grupo IS NOT NULL));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'reserva_grupos_tamano_rango') THEN
    ALTER TABLE reserva_grupos ADD CONSTRAINT reserva_grupos_tamano_rango
      CHECK (tamano_grupo IS NULL OR tamano_grupo BETWEEN 2 AND 10);
  END IF;
END $$;

-- Un integrante por fila, en el orden en que se capturaron: orden = 1 es
-- siempre quien hace la reserva (su código ya está en usuarios). ON DELETE
-- CASCADE porque el dashboard borra la fila de reserva_grupos directamente
-- (ver api_eliminar_reserva_grupo).
CREATE TABLE IF NOT EXISTS reserva_grupo_integrantes (
    id               SERIAL PRIMARY KEY,
    grupo_id         INTEGER NOT NULL REFERENCES reserva_grupos(id) ON DELETE CASCADE,
    codigo_uninorte  VARCHAR(30) NOT NULL,
    orden            SMALLINT NOT NULL,
    CONSTRAINT reserva_grupo_integrantes_orden_unico UNIQUE (grupo_id, orden)
);
CREATE INDEX IF NOT EXISTS idx_reserva_grupo_integrantes_grupo ON reserva_grupo_integrantes (grupo_id);

-- Cláusula de seguro: ¿los activos salen de Barranquilla? Si salen, hay que
-- tramitar el seguro y la reserva empieza como mínimo el 5.º día hábil
-- (lunes a viernes, sin festivos de Colombia) después de crearla (ver
-- recursos_service.fecha_minima_sale_de_barranquilla). NULL = no se
-- preguntó (reservas anteriores); el bot siempre guarda TRUE/FALSE.
-- Renombre: la columna nació como `sale_area_metropolitana`; solo corre si
-- existe la vieja y aún no la nueva.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'reserva_grupos' AND column_name = 'sale_area_metropolitana')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'reserva_grupos' AND column_name = 'sale_de_barranquilla') THEN
        ALTER TABLE reserva_grupos RENAME COLUMN sale_area_metropolitana TO sale_de_barranquilla;
    END IF;
END $$;
ALTER TABLE reserva_grupos ADD COLUMN IF NOT EXISTS sale_de_barranquilla BOOLEAN;

CREATE INDEX IF NOT EXISTS idx_auditoria_telefono_fecha ON auditoria_bot (telefono_wa, creado_en);
CREATE INDEX IF NOT EXISTS idx_auditoria_mensaje_id ON auditoria_bot (mensaje_id);

-- Lista negra de fechas (pestaña "Excepciones" del dashboard): días en que
-- el bot NO acepta reservas. Ver app/fechas_bloqueadas_service.py.
-- Reglas generales: UNA sola fila (id = 1). dias_semana_bloqueados usa la
-- numeración de Python date.weekday(): 0 = lunes ... 6 = domingo. Los
-- festivos de Colombia no se guardan: se calculan con el paquete `holidays`.
CREATE TABLE IF NOT EXISTS calendario_reglas (
    id                      SMALLINT PRIMARY KEY DEFAULT 1,
    dias_semana_bloqueados  SMALLINT[] NOT NULL DEFAULT '{5,6}',
    festivos_co             BOOLEAN NOT NULL DEFAULT TRUE,
    actualizado_en          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT calendario_reglas_fila_unica CHECK (id = 1),
    CONSTRAINT calendario_reglas_dias_validos
        CHECK (dias_semana_bloqueados <@ ARRAY[0,1,2,3,4,5,6]::SMALLINT[])
);
INSERT INTO calendario_reglas (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- Excepciones de un día concreto: mandan sobre las reglas. 'bloqueo' cierra
-- un día normal; 'desbloqueo' abre un festivo o un día de semana bloqueado.
-- Una por fecha (PK); borrar la fila devuelve el día a las reglas.
CREATE TABLE IF NOT EXISTS fechas_excepcion (
    fecha           DATE PRIMARY KEY,
    tipo            VARCHAR(12) NOT NULL,
    motivo          VARCHAR(200),
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_en  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fechas_excepcion_tipo_valido CHECK (tipo IN ('bloqueo', 'desbloqueo'))
);

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
