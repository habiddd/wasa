# Seguridad y guardrails — checklist

## Ya incluido en este scaffold
- [x] Rate limiting por número de WhatsApp (`guardrails.py`)
- [x] Filtrado contra inyección de prompt (`guardrails.mensaje_es_sospechoso`).
      Compara sobre una forma *compactada* del mensaje (sin tildes, sin
      caracteres invisibles y sin nada que no sea letra o dígito), no sobre
      substrings literales del texto tal cual: la primera versión se evadía
      con solo meter un espacio de más (`"ignora  las  instrucciones"`).
      Cubre frases en español e inglés y marcadores de plantilla de chat
      (`<|im_start|>`, `role: system`). Los patrones que también aparecen en
      frases legítimas del dominio ("sin restricciones de horario") solo
      disparan en combinación, vía `REGEX_SOSPECHOSOS`.
- [x] Whitelist de intents permitidos
- [x] Auditoría de cada interacción (tabla `auditoria_bot`)
- [x] Usuario de base de datos separado para la app (no usa el superusuario
      de Postgres)
- [x] Secrets fuera del código, vía `.env` (no versionado)
- [x] System prompt que restringe explícitamente el alcance del bot
- [x] Dashboard interno (`/dashboard`) protegido con autenticación básica
      (`DASHBOARD_USER`/`DASHBOARD_PASSWORD`) — expone el QR de vinculación
      de WhatsApp, así que no puede quedar abierto
- [x] Ediciones administrativas del calendario protegidas por la misma
      autenticación del dashboard: cambio de horario, estado y activos de una
      reserva se aplican por `grupo_id` en una sola transacción; la eliminación
      completa exige confirmación explícita en un popup antes del `DELETE`.
- [x] Los estados `entregada`, `completada` y `devuelta_tarde` no se pueden
      fijar a mano: los tres endpoints de edición responden 409
      (`_ESTADOS_SOLO_CON_ACTA` en `routes/dashboard.py`). La única vía es
      registrar el acta firmada, que es el comprobante de que el equipo
      cambió de manos.
- [x] Subida de actas acotada: se valida la cabecera `%PDF-`, el tamaño contra
      `ACTAS_MAX_BYTES` (10 MiB por defecto) y `MAX_CONTENT_LENGTH` de Flask, y
      el `momento` contra el que realmente toca (409 si no coincide). El
      guardado en disco y la transición de estado van en un solo `commit`; si
      falla, se borra el archivo recién escrito.
- [x] Descarga de actas históricas sin salto de directorio: se comprueba que
      el acta pertenezca al `grupo_id` pedido y que la ruta resuelta quede
      dentro de `ACTAS_DIR` antes de servir nada. La ruta nunca se construye
      con algo que venga del request.
- [x] SHA-256 de cada versión del acta guardado en `reserva_actas`, y ningún
      archivo se sobrescribe jamás: cada firma o subida crea uno nuevo, así que
      el histórico es verificable a posteriori.
- [x] Actas servidas con `Content-Disposition: attachment` y
      `X-Content-Type-Options: nosniff`.
- [x] Registro obligatorio antes de dejar reservar/consultar nada: el
      primer mensaje de un número dispara un flujo paso a paso (nombre
      completo, código de estudiante, cédula) y hasta que no está completo
      no pasa a ningún otro intent (`registro_service.py`,
      `usuarios_service.py` — ver `docs/SESION_2026-08-25.md`)
- [x] Aviso de tratamiento de datos en el primer mensaje de cada número
      nuevo: antes de pedirle un solo dato se le informa que la conversación
      queda guardada y monitoreada para métricas y archivo. Se emite una sola
      vez por usuario y queda constancia de cuándo en
      `usuarios.aviso_privacidad_en` (`registro_service.py`)
- [x] Confirmación explícita antes de escribir una reserva en la base de
      datos — el bot manda un resumen tipo factura (cada activo del
      carrito, quién reserva, inicio, fin) con un menú (Confirmar /
      Agregar otro activo o espacio / Quitar un activo de la reserva /
      Cambiar fecha / Cambiar hora / Cancelar) y solo reserva si el
      usuario elige "Confirmar" (`recursos_service.gestionar_reserva` /
      `_crear_reserva_desde_wizard` — ver `docs/SESION_2026-08-28.md` y
      `docs/SESION_2026-08-31.md`)
- [x] Fecha y hora de una reserva nunca se aceptan de una interpretación
      sin que el usuario la confirme explícitamente: el formato exacto
      (dd/mm/aaaa, hh:mm am/pm, o un rango de fechas dd/mm/aaaa a
      dd/mm/aaaa para reservas de varios días) se acepta directo, pero
      cualquier variante en lenguaje natural ("mañana", "desde mañana
      hasta el viernes", "a las 3pm") se le muestra de vuelta al usuario
      con un menú numerado (1. Sí / 2. No) antes de usarla — mitiga que
      una mala interpretación (del parser propio o, antes, del LLM) se
      cuele sin que el usuario la vea (`_interpretar_fecha_natural`,
      `_interpretar_rango_fecha_natural`,
      `_interpretar_hora_individual_relajada`, ver
      `docs/SESION_2026-08-28.md` y `docs/SESION_2026-08-31.md`). Si en
      vez del número el usuario repite la misma fecha/hora, se toma como
      confirmación implícita; si escribe una distinta, se le vuelve a
      preguntar por esa nueva interpretación — nunca se salta el menú.
- [x] Restricción a nivel de Postgres (`EXCLUDE USING gist`, extensión
      `btree_gist`) que impide dos reservas confirmadas solapadas del
      mismo recurso, sin depender de que la lógica de la app siempre
      chequee bien — probado saltándose la app con un INSERT crudo por
      SQL y quedó rechazado (ver `docs/SESION_2026-08-25.md`)
- [x] Cancelar una reserva ya confirmada (intent `cancelar`) solo lista y
      modifica reservas del propio usuario que escribe: tanto el listado
      como el `UPDATE` final filtran por `Reserva.usuario_id == usuario.id`,
      donde `usuario` viene resuelto por el servidor a partir del número de
      WhatsApp del remitente, nunca de algo que el texto del chat pueda
      afirmar — un usuario no puede ver ni cancelar una reserva ajena, ni
      una a la vez ni varias juntas (`recursos_service.gestionar_cancelacion`).
      Probado dos veces: primero simulando un segundo usuario que reutiliza
      el estado de cancelación de otro con datos sintéticos, y después
      repitiendo el mismo intento contra las reservas **reales** del
      usuario que venía probando el bot (incluyendo forzar a mano el paso
      de confirmación con sus ids reales) — en ambos casos, cero filas
      modificadas (ver `docs/SESION_2026-08-31.md`)
- [x] `POST /send` de `baileys-bot` valida `x-api-key` contra `FLASK_API_KEY`
      con comparación en tiempo constante, y falla cerrado si la clave no
      está configurada (`claveValida` en `baileys-bot/index.js`). El puerto
      3000 ya se publica solo en `127.0.0.1`, igual que postgres/flask --
      Flask le habla por la red interna de Docker, que no pasa por el puerto
      publicado. Antes, cualquiera con acceso de red a ese puerto podía
      hacer que el número de la LACS enviara mensajes arbitrarios.

## Pendiente antes de producción con datos reales de la LACS

### Infraestructura
- [ ] Exponer Evolution API y Flask solo dentro de tu red (Tailscale),
      nunca directo a internet sin reverse proxy + HTTPS.
- [ ] Rotar `EVOLUTION_API_KEY` y `FLASK_SECRET_KEY` antes de ir a producción
      (los del `.env.example` son solo placeholders de desarrollo).
- [ ] Backups automáticos del volumen de Postgres (`pg_dump` programado).
- [ ] Límite de recursos por contenedor en `docker-compose.yml` (memory/cpu)
      para que un pico no tumbe el servidor completo.
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
      auto-registra dando sus datos) — decide si para la LACS real hace
      falta validar esos datos contra algún padrón de Uninorte (ej. que el
      código de estudiante exista de verdad) en vez de aceptar lo que sea
      que el usuario escriba.
- [ ] Logs sin datos sensibles innecesarios (evita loguear el cuerpo
      completo de cada mensaje en texto plano de forma indefinida; define
      una política de retención para `auditoria_bot`).
- [ ] Definir si la eliminación física desde el dashboard debe mantenerse
      para producción o reemplazarse por una cancelación lógica/auditable;
      el flujo actual elimina las líneas de `reservas` y su `reserva_grupos`
      después de la confirmación del administrador.
- [ ] Interruptor único para los avisos automáticos de WhatsApp. Hoy
      `BARRIDO_ACTIVO=0` apaga el hilo ticker pero **no** el barrido
      perezoso del listado de reservas, así que los mensajes siguen
      saliendo. Ver la sección "El bot ahora le escribe a estudiantes sin
      que nadie apriete nada" más abajo para el rodeo que sí funciona
      mientras tanto.
- [ ] Antes de apuntar a la instancia real de WhatsApp de la LACS: revisar
      qué reservas `confirmada` con `inicio` pasado hay vivas en la base.
      El primer barrido las procesa todas. El corte de frescura evita los
      mensajes, pero el cambio masivo a `no_show` sí ocurre.

### Riesgo aceptado: el contenido del acta firmada no se verifica

No es un pendiente por hacer, es una decisión tomada a sabiendas. Queda escrita
aquí para que nadie la descubra por sorpresa más adelante.

**Qué pasa.** El PDF firmado se compone **en el navegador** con pdf-lib y se
sube ya terminado. El servidor solo comprueba que empiece por `%PDF-` y que no
pase del tamaño máximo: no puede saber si el cuerpo del acta es el que él
generó. Con las credenciales del dashboard —o con un XSS en la SPA, o con un
`curl`— se puede subir un acta con otra lista de equipos, otras fechas u otro
usuario, y el sistema la guardará como el comprobante de la entrega.

**Por qué se aceptó.** Al dashboard solo entra quien administra la LACS, y la
opción de "subir un PDF firmado desde otro dispositivo" —que es un requisito
del flujo real— acepta un PDF arbitrario de todas formas. Cerrar solo el
camino de la firma en pantalla no habría cerrado el otro. Además se quiso
mantener el costo de composición fuera de los contenedores, pensando en
escalar.

**La alternativa, si algún día cambia el criterio.** Componer en el servidor
(reportlab + pypdf): el navegador manda solo los PNG de firma y Flask
reconstruye el cuerpo del acta desde la base de datos, con lo que el contenido
pasa a ser auténtico por construcción. Cuesta ~200 ms de CPU por firma, unas
2-4 veces por reserva. Con eso, el camino de firma en pantalla queda fuerte y
solo el de subida sigue siendo confiado — que es una posición bastante mejor
que tener los dos confiados.

**Qué NO mitiga esto.** Ni el SHA-256 ni el historial: sirven para detectar que
un archivo cambió después de guardarse, no para saber si el contenido era
legítimo cuando entró.

### El bot ahora le escribe a estudiantes sin que nadie apriete nada

Hasta el no-show automático, **todo** mensaje saliente tenía un humano o un
mensaje entrante detrás: o el estudiante escribía y el bot respondía
(`routes/webhook.py`), o un administrador tocaba un botón del dashboard
(`_avisar_estudiante`). Desde `app/ticker.py` eso dejó de ser cierto: un hilo
despierta cada 60 s y puede mandar WhatsApp por su cuenta. Es un cambio en el
perfil de riesgo del sistema, no solo una funcionalidad más, y por eso queda
escrito acá.

**Radio de alcance.** Cada grupo de reserva puede generar como máximo tres
mensajes (`previo`, `hora`, `no_show`), garantizado por el
`UNIQUE (grupo_id, tipo)` de `reserva_avisos`. No hay reintentos: un envío
fallido queda `motivo='error_envio'` y no se vuelve a intentar nunca. El techo
por barrido es, entonces, tres mensajes por cada reserva `confirmada` cuya
ventana de avisos esté abierta — no existe un camino por el que un bug de
bucle mande miles.

**Lo que contiene el radio.** El corte de frescura (`AVISO_VENTANA_MIN`) es lo
que impide el escenario feo: levantar el stack después de días apagado y
dispararle recordatorios viejos a medio padrón. Los avisos vencidos se
registran y no se mandan. Esto se probó de verdad: en el despliegue inicial
había 27 reservas `confirmada` con `inicio` pasado y ninguna generó mensajes.

**Cómo apagarlo hoy.** No hay una perilla dedicada (ver el pendiente arriba).
El rodeo que funciona es `AVISO_VENTANA_MIN=0`: todo aviso cuyo momento ya
pasó cuenta como vencido, así que no sale ningún mensaje, y los cambios de
estado a `no_show` se siguen aplicando. `BARRIDO_ACTIVO=0` **no** sirve para
esto: solo apaga el ticker, y el listado de reservas del dashboard sigue
llamando al mismo barrido cada 30 s.

**Qué NO mitiga.** Nada de esto valida el destinatario. El número sale de
`usuarios.telefono_wa`, que hoy es auto-servicio (ver el pendiente de
validación del registro, más arriba): si alguien se registró con el número de
otra persona, los avisos automáticos le llegan a esa otra persona sin que
nadie revise. Antes el daño se limitaba a lo que un administrador mandara a
mano; ahora es continuo.

### Modelo / LLM
- [x] El LLM nunca debe ser el que decide disponibilidad o ejecuta la
      escritura en la base de datos — siempre a través de funciones
      explícitas y verificadas en SQL (ver `docs/ARQUITECTURA.md`).
      Implementado: `recursos_service.py` es quien valida/escribe. El LLM
      solo se usa para identificar el activo mencionado en el primer
      mensaje (`extraer_datos_reserva`, campo `recurso`) y para clasificar
      el intent (`clasificar_intent`, con fallback por palabras clave si
      no responde) — el prompt le sigue pidiendo `inicio`/`fin` también,
      pero esos dos campos se descartan siempre sin usarlos (ver el bullet
      de arriba y `docs/SESION_2026-08-28.md`): se detectó en uso real que
      el modelo inventaba una hora que el usuario nunca dio pese a que el
      prompt le indicaba devolver `null` si faltaba información.
- [x] Ni el campo `recurso` que devuelve el LLM se acepta a ciegas — se
      detectaron dos veces en uso real (`docs/SESION_2026-08-31.md`) que
      el modelo completaba ese campo con un activo inventado cuando el
      mensaje no lo mencionaba con claridad, pese a que el prompt le pide
      `null` en ese caso: un mensaje sin ningún activo ("Reservar" a
      secas) devolvía un nombre igual, y un mensaje ambiguo entre dos
      modelos del catálogo ("cámara canon", con EOS R y EOS R6 en el
      catálogo) dejaba que el LLM "eligiera" uno sin que el texto lo
      distinguiera. `_resolver_recurso_para_reserva` ahora exige respaldo
      textual real (`_puntaje_nombre_en_texto`) antes de confiar el
      `recurso` del LLM, y nunca lo consulta para desempatar cuando el
      matcheo por palabra clave ya encontró más de un candidato — en ese
      caso siempre se le pregunta al usuario.
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
