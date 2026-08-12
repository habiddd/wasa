# Seguridad y guardrails — checklist

## Ya incluido en este scaffold
- [x] Rate limiting por número de WhatsApp (`guardrails.py`)
- [x] Filtrado básico contra inyección de prompt
- [x] Whitelist de intents permitidos
- [x] Auditoría de cada interacción (tabla `auditoria_bot`)
- [x] Usuario de base de datos separado para la app (no usa el superusuario
      de Postgres)
- [x] Secrets fuera del código, vía `.env` (no versionado)
- [x] System prompt que restringe explícitamente el alcance del bot
- [x] Dashboard interno (`/dashboard`) protegido con autenticación básica
      (`DASHBOARD_USER`/`DASHBOARD_PASSWORD`) — expone el QR de vinculación
      de WhatsApp, así que no puede quedar abierto

## Pendiente antes de producción con datos reales de la CPA

### Infraestructura
- [ ] Exponer Evolution API y Flask solo dentro de tu red (Tailscale),
      nunca directo a internet sin reverse proxy + HTTPS.
- [ ] Rotar `EVOLUTION_API_KEY` y `FLASK_SECRET_KEY` antes de ir a producción
      (los del `.env.example` son solo placeholders de desarrollo).
- [ ] Backups automáticos del volumen de Postgres (`pg_dump` programado).
- [ ] Límite de recursos por contenedor en `docker-compose.yml` (memory/cpu)
      para que un pico no tumbe el servidor completo.
- [ ] El puerto 3000 de `baileys-bot` se publica sin restricción
      (`"3000:3000"`, a diferencia de postgres/flask que usan
      `127.0.0.1:...`), y su endpoint `POST /send` no valida ningún API key
      pese a que Flask sí lo envía. Cualquiera con acceso de red a ese
      puerto puede hacer que el número de la CPA envíe mensajes arbitrarios.
      Corregir: bindear a `127.0.0.1` y validar `x-api-key` en `/send`
      dentro de `baileys-bot/index.js`.
- [ ] `/webhook/whatsapp` y `/webhook/qr` en Flask reciben el header
      `x-api-key` pero nunca lo comparan contra `WHATSAPP_API_KEY` — hoy el
      riesgo es bajo porque Flask solo escucha en `127.0.0.1`, pero si se
      expone detrás de un reverse proxy hay que validar ese header antes.

### Aplicación
- [ ] Autenticación en los endpoints `/api/analytics/*` (hoy están abiertos;
      agrega al menos una API key o sesión de admin).
- [ ] Mover el rate limiter de `guardrails.py` de memoria a Redis si vas a
      correr más de un worker de Gunicorn (con memoria, cada worker tiene
      su propio contador y el límite real se vuelve inconsistente).
- [ ] Validar que el número de WhatsApp que escribe corresponda a un
      usuario registrado (`usuarios.telefono_wa`) antes de dejarlo reservar
      equipos costosos — decide si permites auto-registro o requiere
      alta manual por un admin de la CPA.
- [ ] Confirmar la reserva con un paso explícito ("responde SÍ para
      confirmar") antes de escribir en la base de datos, para evitar que
      una mala interpretación del LLM cree reservas fantasma.
- [ ] Logs sin datos sensibles innecesarios (evita loguear el cuerpo
      completo de cada mensaje en texto plano de forma indefinida; define
      una política de retención para `auditoria_bot`).

### Modelo / LLM
- [ ] El LLM nunca debe ser el que decide disponibilidad o ejecuta la
      escritura en la base de datos — siempre a través de funciones
      explícitas y verificadas en SQL (ver `docs/ARQUITECTURA.md`).
- [ ] Define un tope de tokens/tiempo de respuesta y un mensaje de
      fallback claro si el LLM local no responde (ya hay un try/except
      básico en `llm_client.py`, revísalo antes de producción).

### Cumplimiento con WhatsApp/Meta
- [ ] Si sigues en modo Baileys en producción real: ten un plan B (número
      alterno) por si Meta banea el número, ya que es un uso no oficial.
- [ ] Si migras a Meta Cloud API: diseña el bot como flujo guiado (listas/
      botones) para calzar con la política de bots "predecibles" de Meta,
      no como chat abierto de propósito general.
