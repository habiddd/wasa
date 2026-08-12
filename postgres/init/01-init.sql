-- Se ejecuta automáticamente la primera vez que se crea el volumen de Postgres.
-- Crea la base de datos de Evolution API y la base de datos + usuario de tu app.
-- Todas las credenciales vienen del .env (pasadas como variables de entorno
-- al contenedor postgres por docker-compose.yml).
--
-- El entrypoint de Docker de Postgres ejecuta este script con:
--   psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --no-password --no-psqlrc
-- Usamos \set para capturar las variables de entorno del contenedor y luego
-- las referenciamos con :varname en comandos SQL directos.

\echo '=== Inicializando bases de datos ==='

-- Capturamos las variables de entorno del contenedor
\set db_user `echo $APP_DB_USER`
\set db_pass `echo $APP_DB_PASSWORD`
\set db_name `echo $APP_DB_NAME`

-- 1. Base de datos para Evolution API
CREATE DATABASE evolution_db;

-- 2. Usuario de la app (CREATE USER es idempotente con IF NOT EXISTS)
CREATE USER :"db_user" WITH PASSWORD :'db_pass';

-- 3. Base de datos de la app + privilegios
CREATE DATABASE :"db_name" OWNER :"db_user";
GRANT ALL PRIVILEGES ON DATABASE :"db_name" TO :"db_user";

\echo '=== Inicialización completada ==='
