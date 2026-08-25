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
- [x] Registro obligatorio antes de dejar reservar/consultar nada: el
      primer mensaje de un número dispara un flujo paso a paso (nombre
      completo, código de estudiante, cédula) y hasta que no está completo
      no pasa a ningún otro intent (`registro_service.py`,
      `usuarios_service.py` — ver `docs/SESION_2026-08-25.md`)
- [x] Confirmación explícita ("responde SÍ para confirmar") antes de
      escribir una reserva en la base de datos — el bot manda un resumen
      (activo, quién reserva, inicio, fin) y solo reserva si el usuario
      confirma (`recursos_service.gestionar_reserva` /
      `confirmar_reserva_pendiente`)
- [x] Restricción a nivel de Postgres (`EXCLUDE USING gist`, extensión
      `btree_gist`) que impide dos reservas confirmadas solapadas del
      mismo recurso, sin depender de que la lógica de la app siempre
      chequee bien — probado saltándose la app con un INSERT crudo por
      SQL y quedó rechazado (ver `docs/SESION_2026-08-25.md`)

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
- [ ] Hoy el registro es auto-servicio (cualquier número que escribe se
      auto-registra dando sus datos) — decide si para la CPA real hace
      falta validar esos datos contra algún padrón de Uninorte (ej. que el
      código de estudiante exista de verdad) en vez de aceptar lo que sea
      que el usuario escriba.
- [ ] Logs sin datos sensibles innecesarios (evita loguear el cuerpo
      completo de cada mensaje en texto plano de forma indefinida; define
      una política de retención para `auditoria_bot`).

### Modelo / LLM
- [x] El LLM nunca debe ser el que decide disponibilidad o ejecuta la
      escritura en la base de datos — siempre a través de funciones
      explícitas y verificadas en SQL (ver `docs/ARQUITECTURA.md`).
      Implementado: `recursos_service.py` es quien valida/escribe, el LLM
      solo extrae `{recurso, inicio, fin}` como texto libre → JSON.
- [ ] El modelo local en uso (`lfm2.5-2.6b`, razonador) puede tardar 30-90s
      en responder a la extracción de fecha/hora de una reserva porque
      gasta presupuesto de tokens en `reasoning_content` antes de escribir
      la respuesta final. Ya se ajustó el timeout de esa llamada (90s) y el
      de gunicorn (120s, ver `flask-app/Dockerfile`) para que no se corte a
      mitad de camino, pero un usuario de WhatsApp esperando ~1 minuto por
      una respuesta sigue siendo mala experiencia. Antes de producción,
      evalúa un modelo más chico/rápido sin cadena de razonamiento
      específicamente para esta tarea de extracción estructurada.

### Cumplimiento con WhatsApp/Meta
- [ ] Si sigues en modo Baileys en producción real: ten un plan B (número
      alterno) por si Meta banea el número, ya que es un uso no oficial.
- [ ] Si migras a Meta Cloud API: diseña el bot como flujo guiado (listas/
      botones) para calzar con la política de bots "predecibles" de Meta,
      no como chat abierto de propósito general.
