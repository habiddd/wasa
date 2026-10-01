# Guía de despliegue y operación

Entregable 3 del Objetivo Específico 4. Está escrita para la persona del
LACS que levante, opere o reconstruya el sistema sin haberlo desarrollado.

## 1. Componentes

| Servicio (contenedor) | Qué hace | Puerto en el anfitrión |
|---|---|---|
| `postgres` (`lacs_postgres`) | Base de datos: recursos, reservas, usuarios, auditoría | `127.0.0.1:5432` |
| `redis` (`lacs_redis`) | Sesiones de conversación y bloqueo del barrido | solo red interna |
| `baileys-bot` (`lacs_baileys_bot`) | Pasarela de WhatsApp por QR | `127.0.0.1:3000` |
| `flask-app` (`lacs_flask_app`) | Lógica de reservas, guardrails, bot, API del back office | `127.0.0.1:5000` |
| `dashboard-frontend` (`lacs_dashboard_frontend`) | Back office (React servido por Nginx) | `127.0.0.1:8080` |

Todos los puertos se publican **solo en la interfaz local** del anfitrión.
Fuera de Docker, el modelo de lenguaje corre en LM Studio u Ollama, en el
mismo equipo o en la red local.

Datos persistentes (sobreviven a `docker compose down` y a reconstruir las
imágenes):

| Volumen | Contenido |
|---|---|
| `cpa-reservas-bot_postgres_data` | Base de datos |
| `cpa-reservas-bot_redis_data` | Sesiones de conversación |
| `cpa-reservas-bot_baileys_sessions` | Vinculación de WhatsApp (evita escanear el QR de nuevo) |
| `lacs-reservas-bot_actas_pdf` | PDF de actas firmadas: histórico probatorio |

## 2. Requisitos

- Docker y Docker Compose.
- Un número de WhatsApp libre, con el celular a mano para escanear el QR.
- LM Studio u Ollama con el modelo cargado y servidor en modo API compatible
  con OpenAI.

## 3. Primer despliegue

```bash
cp .env.example .env
```

Editar `.env` y reemplazar **todos** los valores `changeme_*`:

| Variable | Qué poner |
|---|---|
| `POSTGRES_SUPERUSER_PASSWORD`, `APP_DB_PASSWORD` | Contraseñas largas y distintas |
| `FLASK_SECRET_KEY`, `FLASK_API_KEY` | Cadenas aleatorias largas |
| `DASHBOARD_USER`, `DASHBOARD_PASSWORD` | Usuario y contraseña del back office; sin contraseña, el back office responde 503 |
| `LLM_BASE_URL`, `LLM_MODEL` | Dirección y modelo del servidor de LLM. Si corre en el mismo equipo que Docker Desktop: `http://host.docker.internal:1234` |
| `MAX_MESSAGES_PER_MINUTE` | Límite de mensajes por número (por defecto 8) |
| `AUDITORIA_RETENCION_DIAS` | Días antes de anonimizar la auditoría (por defecto 180) |

Levantar:

```bash
docker compose up -d --build
docker exec lacs_flask_app flask --app wsgi.py seed-recursos   # catálogo de prueba (opcional)
```

Postgres crea la base y el esquema solo la primera vez que se crea su volumen.

## 4. Vincular WhatsApp

1. Abrir el back office en `http://localhost:8080/dashboard` e iniciar sesión.
2. Ir a **Servicios → QR / WhatsApp** y escanear el QR desde el celular del
   número del bot (WhatsApp → Dispositivos vinculados).
3. Cuando aparezca el número vinculado, escribirle "hola" desde otro teléfono.

Para usar la API oficial de Meta en vez del QR, seguir
`docs/GUIA_META_WHATSAPP.md` y elegir **Meta** en la misma pantalla.

## 5. Operación diaria

| Tarea | Dónde |
|---|---|
| Ver qué se entrega y qué se recibe hoy | **Hoy** (se refresca cada 30 s) |
| Confirmar o denegar solicitudes de recursos con autorización | **Hoy → Por confirmar** o **Reservas** |
| Firmar el acta de entrega o devolución | Botón del acta en **Hoy** o **Reservas** |
| Agregar, editar o poner en mantenimiento un equipo o espacio | **Activos** y **Lugares** |
| Bloquear días (festivos, cierres) | **Excepciones** |
| Ver informes | **Métricas** (General, Por activo, Por uso, Rendimiento bot) y botón **Descargar Excel** |
| Ver si todo está en línea | **Servicios → Estado** (punto verde o rojo al pie del menú) |

Lo automático, sin que nadie intervenga: avisos al estudiante antes de la
recogida, a la hora y antes del vencimiento; paso a "no se presentó" 30
minutos después del inicio sin recoger; anonimización de la auditoría vieja.

## 6. Respaldo y restauración

Respaldo de la base (programarlo a diario):

```bash
docker exec lacs_postgres pg_dump -U postgres -Fc reservas_db > respaldo_$(date +%F).dump
```

Respaldo de las actas firmadas:

```bash
docker run --rm -v lacs-reservas-bot_actas_pdf:/actas -v "$PWD":/destino alpine \
  tar czf /destino/actas_$(date +%F).tgz -C /actas .
```

(Confirmar el nombre del volumen con `docker volume ls | grep actas`; el
prefijo es el nombre del proyecto de Compose.)

Restaurar la base:

```bash
docker exec -i lacs_postgres pg_restore -U postgres -d reservas_db --clean < respaldo_AAAA-MM-DD.dump
```

## 7. Actualizar el sistema

```bash
git pull
# Si cambió postgres/init/02-schema.sql, aplicar el esquema a la base viva (es idempotente):
docker exec -e APP_DB_USER=reservas_user -i lacs_postgres \
  psql -v ON_ERROR_STOP=1 -U postgres -d reservas_db < postgres/init/02-schema.sql
docker compose up -d --build flask-app dashboard-frontend baileys-bot
```

Después de actualizar, correr las pruebas (`entregables/OE3/pruebas/README.md`).

## 8. Solución de problemas

| Síntoma | Revisar |
|---|---|
| El bot no responde | `docker compose logs -f flask-app` y `docker compose logs -f baileys-bot`; en **Servicios → Estado**, que WhatsApp esté conectado. |
| Pide escanear el QR otra vez | La sesión se cerró desde el celular; escanear de nuevo en **Servicios → QR / WhatsApp**. |
| Respuestas lentas o de menú a todo | El LLM no responde: **Servicios → Estado** y `LLM_BASE_URL`. Sin LLM el bot sigue funcionando con su clasificador por reglas, pero entiende menos. |
| Un usuario dice que el bot no le contesta | Puede estar excediendo `MAX_MESSAGES_PER_MINUTE`; en **Métricas → Rendimiento bot** aparecen los bloqueos. |
| El back office pide usuario y contraseña y no entra | `DASHBOARD_USER` / `DASHBOARD_PASSWORD` en `.env`, y reiniciar `flask-app` y `dashboard-frontend`. |
| No salen los avisos automáticos | `BARRIDO_ACTIVO=1` en `.env` y el log "Ticker de avisos_reservas iniciado" en `flask-app`. |

## 9. Seguridad: lo que no se debe hacer

- No publicar los puertos `5000`, `3000` ni `5432` hacia fuera del equipo.
  Para la API de Meta solo se expone `/webhook/meta`, por un túnel con HTTPS
  (`docs/GUIA_META_WHATSAPP.md`).
- No subir `.env` al repositorio (ya está en `.gitignore`).
- No agregar `otro` a `ALLOWED_INTENTS`: eso quitaría la lista blanca.

Checklist completa de seguridad: `docs/SEGURIDAD.md`.
