# Arquitectura

## Flujo de un mensaje entrante

1. Usuario escribe por WhatsApp al número de la CPA.
2. `baileys-bot` (servicio Node.js sobre `@whiskeysockets/baileys`, WhatsApp
   Web no oficial) recibe el mensaje vía `messages.upsert` y lo reenvía a
   `POST /webhook/whatsapp` en Flask, con el `remoteJid` completo tal cual
   lo entrega Baileys (ver nota sobre JIDs `@lid` más abajo).
3. Flask, en orden (ver `routes/webhook.py:recibir_webhook`):
   - Aplica **rate limiting** por número (`guardrails.excede_limite_mensajes`).
   - Aplica **filtrado de entrada** contra mensajes sospechosos/inyección
     de prompt (`guardrails.mensaje_es_sospechoso`).
   - Resuelve/crea el **Usuario** por `telefono_wa` (`usuarios_service`) y,
     si su registro no está completo, el mensaje se trata como respuesta a
     la siguiente pregunta pendiente del alta (`registro_service`) --
     ver "Identidad y registro" más abajo. Esto pasa *antes* de clasificar
     intent a propósito.
   - Si hay un **wizard de reserva en curso** en la sesión del usuario
     (`sesion["reserva_wizard"]`, ver "Memoria de conversación" y "Reserva
     paso a paso" más abajo), el mensaje se trata como la respuesta al paso
     exacto en que quedó ese wizard (`recursos_service.gestionar_reserva`),
     también antes de clasificar intent.
   - Clasifica el **intent** (`llm_client.clasificar_intent`) y lo valida
     contra la whitelist (`guardrails.intent_permitido`).
   - Ejecuta la lógica de negocio correspondiente:
     - `consultar_disponibilidad` y `reservar` van a `recursos_service.py`,
       que consulta `recursos`/`reservas` de verdad -- nunca al LLM en
       modo libre (ver "Por qué separar el LLM de la lógica de reservas").
     - `cancelar` y `ayuda` siguen delegando en el LLM conversacional
       (`llm_client.generar_respuesta`) -- `cancelar` con lógica real
       (identificar y anular una reserva existente) queda pendiente.
   - Envía la respuesta de vuelta por `whatsapp_client.enviar_mensaje_texto`,
     que hace `POST /send` al servicio `baileys-bot`.
   - Registra la interacción completa en `auditoria_bot`.
4. `baileys-bot` reporta de forma asíncrona el estado de entrega
   (server-ack/delivery-ack/leído) vía `POST /webhook/ack`, correlacionado
   por `mensaje_id` (ver `guardrails.actualizar_estado_envio`).

`flask-app/app/evolution_client.py` es un cliente para Evolution API que
quedó del scaffold original y ya no se usa — el canal activo hoy es
Baileys directo (`whatsapp_client.py`), documentado en
`docs/SESION_2026-08-10.md`.

## Por qué Baileys directo (sin Evolution API de por medio)

- Es gratuito y no requiere levantar/mantener una instancia aparte de
  Evolution API.
- Te da reconexión automática y gestión de sesión de WhatsApp (la sesión
  persiste en el volumen `baileys_sessions`) sin que tengas que
  mantenerlo tú a mano.
- Permite correr en modo no oficial (gratis, rápido de arrancar) mientras
  tramitas la verificación de negocio de Meta, sin bloquear tu desarrollo.
- El costo es que Flask queda acoplado directamente al formato de eventos
  de Baileys en vez de a un formato intermediario estable — si más
  adelante se migra a Meta Cloud API, `webhook.py`/`whatsapp_client.py`
  necesitan un adaptador.

## JIDs `@lid` — por qué no se puede reconstruir el destino desde el número

WhatsApp no siempre identifica una conversación con el JID clásico
`<numero>@s.whatsapp.net`; algunas cuentas usan direccionamiento
`<id>@lid` ("linked ID"). Baileys entrega el `remoteJid` tal cual sea uno
u otro.

Si el pipeline recorta el `remoteJid` a solo los dígitos (por ejemplo,
para usarlo como identificador de rate limiting o como `telefono_wa` en
`auditoria_bot`) y luego reconstruye el JID de salida asumiendo siempre
`@s.whatsapp.net`, el mensaje se manda a un JID que no es el de la
conversación real. Baileys no valida que el JID exista: `sendMessage`
devuelve éxito igual, pero WhatsApp nunca entrega nada. Esto pasó en
producción (ver `docs/SESION_2026-08-24.md`) y quedó silencioso durante
semanas porque no genera ningún error en los logs.

Por eso `webhook.py` guarda dos cosas por separado:
- `numero` (dígitos, solo para rate limiting/auditoría — no sirve para
  responder).
- `remote_jid` (el JID completo tal cual llegó — el único que hay que
  usar para enviar la respuesta, vía `whatsapp_client.enviar_mensaje_texto`
  → `baileys-bot` `POST /send` con `{"jid": ...}`).

## Por qué separar el LLM de la lógica de reservas

El LLM (`llm_client.py`) debería usarse para:
- Interpretar lenguaje natural ambiguo ("necesito una cámara para el
  viernes en la tarde" → intent + entidades).
- Redactar respuestas naturales a partir de datos ya verificados en la
  base de datos.

El LLM **no** debería:
- Decidir por sí solo si hay disponibilidad (eso lo valida SQL contra
  `reservas`, no una alucinación del modelo).
- Ejecutar acciones directamente (crear/cancelar reservas) sin pasar por
  la whitelist de intents y la lógica de negocio explícita.

Esto ya está implementado así: `llm_client.extraer_datos_reserva` le pide
al LLM que devuelva `{recurso, inicio, fin}` como JSON a partir del texto
libre del usuario, pero **solo el campo `recurso` se usa** -- `inicio`/
`fin` se descartan a propósito (ver "Reserva paso a paso: fecha y hora sin
LLM" más abajo; se detectó al modelo local inventando una hora que el
usuario nunca dio). `recursos_service.py` es quien valida ese `recurso`
contra el catálogo real (si el LLM alucina un nombre que no existe, se
descarta y cae al matcher por palabras clave), quien consulta
disponibilidad, y quien decide si se puede escribir en `reservas`. El LLM
nunca toca la base de datos directamente.

## Inventario: varias unidades del mismo modelo (`recursos_service.py`)

Cada fila de `recursos` con `tipo='equipo'` representa una unidad física
concreta: tiene `placa` (número de inventario, único; 7-8 caracteres
alfanuméricos -- el padre usa 8 dígitos terminados en 0 por convención,
ej. `12345670`, un hijo puede tener la misma placa con el último dígito
distinto de 0 o un código propio como `LEN0022`), `categoria` (ej.
"cámara", "dron" -- para agrupar tipos de equipo, independiente de `tipo`
que solo distingue `equipo`/`espacio`), `marca`, `modelo` e `identificador`
opcional (serie/IMEI). `marca`/`modelo` se repiten libremente entre filas
-- lo único con restricción de unicidad es `placa`. Un `espacio` deja esos
campos en `NULL` (no aplican).

**Accesorios en bundle (`padre_id`):** un equipo puede venir empaquetado
con otros (ej. una cámara con 2 lentes). `recursos.padre_id` referencia al
equipo principal cuando la fila es un accesorio/hijo; `NULL` si no lo es.
`incluido_por_defecto` (solo relevante cuando `padre_id` no es `NULL`)
marca si ese hijo se agrega solo a la reserva del padre o si hay que
pedirlo aparte -- ver "Carrito de reserva" más abajo. Un hijo sigue siendo
un `recurso` normal: se puede reservar solo, sin su padre.

Cuando hay varias unidades del mismo modelo (mismo `nombre`, distinta
`placa`), se tratan como intercambiables:
- `_grupos_activos()` / `_grupo(nombre)`: una fila representante por
  modelo distinto, y todas las unidades activas de ese modelo.
- `buscar_recursos_en_texto` puntúa por grupo (un representante por
  modelo, no por placa) contra `recursos.nombre`, por score de palabras
  (no boolean simple) para que una palabra genérica compartida entre
  varios modelos (ej. "cámara") no gane sobre una palabra distintiva (ej.
  "sony") que solo aparece en uno. Cuando el único match viene de una
  palabra genérica sin nombre completo ni segunda palabra distintiva, se
  marca `es_match_debil` y la respuesta incluye una salvedad ("si no te
  refieres a X, dime el nombre completo").
- `_unidad_libre_en_rango(nombre, inicio, fin)`: recorre las unidades del
  grupo y devuelve la primera sin choque de horario -- el usuario nunca
  elige la placa exacta, solo el modelo; el bot reparte automáticamente
  cualquier unidad libre.
- `_categorias_activas()`, `_grupos_por_categoria()`,
  `_detectar_categoria_en_texto()`: resuelven preguntas por categoría (ej.
  "qué cámaras tenemos") comparando contra `categoria` aunque el texto no
  mencione ningún modelo, y permiten sugerir otras marcas/modelos dentro
  de la misma categoría.
- `evaluar_consulta_disponibilidad` (intent `consultar_disponibilidad`)
  lista el **estado agregado de cada modelo** (`_estado_grupo`: cuántas
  unidades libres de cuántas totales) -- el usuario quiere saber qué hay y
  qué no, así que nunca pregunta "¿cuál de estos?".
- Toda hora se interpreta en `America/Bogota` (zona horaria fija de la
  CPA) y se guarda en UTC (columnas `TIMESTAMPTZ`).
- **La garantía real contra choques de horario es de Postgres, no de la
  app**: `postgres/init/02-schema.sql` agrega la extensión `btree_gist` y
  una restricción `EXCLUDE USING gist (recurso_id WITH =, tstzrange(inicio,
  fin) WITH &&) WHERE (estado = 'confirmada')` sobre `reservas`. Postgres
  rechaza cualquier INSERT/UPDATE que se solape con una reserva confirmada
  existente del mismo recurso, sin importar si la vía es la app, una
  migración, un script manual, o dos requests concurrentes que pasaron el
  chequeo de la aplicación al mismo tiempo. `crear_reservas_grupo` en
  `recursos_service.py` solo atrapa el `IntegrityError` que Postgres lanza
  si eso pasa (revirtiendo el carrito completo, ver "Carrito de reserva y
  accesorios por defecto" más abajo); el chequeo previo con
  `_unidad_libre_en_rango` es solo para dar un mensaje más claro antes de
  intentar el INSERT, no la garantía de consistencia.

## Reserva paso a paso (`recursos_service.py`, wizard)

`gestionar_reserva` (intent `reservar`) no le pide todo al usuario en un
solo mensaje libre -- va preguntando un dato a la vez con menús numerados,
guardando el progreso en `sesion["reserva_wizard"]` (ver "Memoria de
conversación"). Progresión para un ítem (`_siguiente_paso_wizard`):

```
elegir_activo → elegir_marca → elegir_modelo → fecha → hora → confirmar → listo
```

`_avanzar_wizard` salta solo cualquier paso que tenga una única opción
posible (ej. una sola marca dentro de una categoría), avisando al usuario
("solo tenemos una marca de dron, sigo con esa") en vez de preguntar algo
que no tiene sentido elegir. Si el primer mensaje ya deja claro el activo
(vía `llm_client.extraer_datos_reserva`, ver arriba), esos pasos se saltan
también.

La reserva no se limita a un solo activo: `wizard["carrito"]` es la lista
de ítems ya resueltos (ver "Carrito de reserva" más abajo). En cuanto un
ítem termina de resolverse (`elegir_activo → ... → elegir_modelo`),
`_agregar_item_al_carrito` lo mueve al carrito antes de seguir -- por eso
`wizard["nombre_recurso"]` es solo "el ítem que se está resolviendo ahora",
no "el único ítem de la reserva".

### Fecha y hora: formato fijo, sin LLM -- con reconocimiento de lenguaje natural confirmado explícitamente

Los pasos "fecha" y "hora" **no** usan el LLM: piden `dd/mm/aaaa` y
`hh:mm am/pm` exactos, parseados con regex deterministas
(`_parsear_fecha_dd_mm_aaaa`, `_parsear_rango_horario`). Esto es
deliberado -- se detectó que el LLM local, al interpretar fecha/hora en
lenguaje libre, a veces inventa un dato que el usuario nunca dio (ej.
respondió solo "Mañana" y el modelo agregó una hora de la nada). El paso
de fecha además siempre muestra primero "Hoy es {día} {dd/mm/aaaa}..."
para que el usuario note de inmediato si el bot tiene otra fecha en mente.

Si el texto no viene en el formato exacto, antes de rechazarlo se intenta
reconocerlo con patrones deterministas más flexibles (sin LLM tampoco):
`_interpretar_fecha_natural` ("mañana", "pasado mañana", días de la
semana, "en N días", "22 de noviembre") y `_interpretar_hora_relajada`
("de 2pm a 7pm", "desde las 3 hasta las 5pm"). A diferencia del formato
exacto, una interpretación **nunca se acepta directo**: el wizard entra en
un paso de confirmación (`confirmar_fecha_interpretada` /
`confirmar_hora_interpretada`) mostrando exactamente lo que entendió y
pidiendo un sí/no antes de seguir. Si el usuario dice "no", solo se borra
ese dato pendiente y se vuelve a pedir en el formato exacto, sin perder el
resto de lo ya elegido. El atajo global de `gestionar_reserva` que
cancela todo el wizard cuando el usuario escribe la palabra "no" se
desactiva a propósito durante estos dos pasos (ahí "no" significa "esa
interpretación está mal", no "cancela la reserva").

### Carrito de reserva y accesorios por defecto

El último paso (`confirmar`) no es un simple sí/no: manda el resumen (cada
ítem del carrito, quién reserva, inicio, fin) con un menú `Confirmar /
Agregar otro activo o espacio / Quitar un activo de la reserva / Cambiar
fecha / Cambiar hora / Cancelar` -- se comporta como un carrito de compras,
no como una reserva de un solo activo:

- **Accesorios por defecto:** al resolver un activo con hijos marcados
  `incluido_por_defecto=True` (ver "Accesorios en bundle" arriba),
  `_accesorios_por_defecto` los agrega solos al carrito -- el usuario no
  tiene que pedirlos aparte, solo quitarlos si no los quiere.
- **"Agregar otro activo o espacio"** reentra a `elegir_activo` para un
  ítem nuevo en el mismo horario ya elegido; al resolverse, se suma al
  carrito (con sus propios accesorios por defecto si aplica).
- **"Quitar un activo de la reserva"** abre un menú numerado
  (`quitar_item_menu`) con lo que hay en el carrito; quitar un padre
  también quita los accesorios que se agregaron por él. Si el carrito
  queda vacío, vuelve a pedir un activo.
- Elegir "Cambiar fecha" (por ejemplo) borra solo `fecha`/`inicio`/`fin`
  del wizard y hace que `_avanzar_wizard` vuelva a preguntar justo eso,
  conservando el carrito ya armado.

Solo cuando el usuario elige "Confirmar" se llama a
`_crear_reserva_desde_wizard`. Antes de escribir nada,
`_resolver_disponibilidad_carrito` revisa cada ítem: uno que el usuario
**eligió explícitamente** y ya no está disponible (alguien más lo reservó
mientras confirmaba) bloquea todo y pide cambiar la hora o quitarlo; un
**accesorio por defecto** que ya no está disponible se quita del carrito
en silencio (con un aviso) sin bloquear el resto -- nadie pidió ese
accesorio a propósito. Si todo el carrito queda disponible,
`crear_reservas_grupo` crea un `ReservaGrupo` y una `Reserva` por cada
unidad **en una sola transacción**: si la restricción `EXCLUDE` de
Postgres rechaza alguna justo al confirmar (condición de carrera), se
revierte el carrito completo en vez de dejar una reserva parcial.

## Identidad y registro (`registro_service.py`, `usuarios_service.py`)

El primer mensaje de un número nuevo crea su fila de `Usuario` vacía
(`registro_completo=False`, sin nombre/código/cédula). Mientras no esté
completo, **cualquier mensaje** se trata como respuesta a la siguiente
pregunta pendiente del alta -- nombre completo, código/id de estudiante,
cédula, en ese orden -- sin pasar por clasificación de intent, por la
misma razón que la confirmación de reserva: "Juan Pérez" como respuesta a
"¿cuál es tu nombre?" no es un intent de reserva/consulta/ayuda.

El número de celular no se pregunta -- ya se conoce, es `telefono_wa`, el
canal por el que está escribiendo.

Cada respuesta válida se guarda en Postgres al instante (no solo en la
sesión), así que el flujo es resistente a que la sesión de Redis expire o
se pierda a mitad de registro: el siguiente mensaje recalcula el campo que
realmente falta contra la fila real de `Usuario` en vez de asumir ciegamente
que responde a lo último que se preguntó.

## Memoria de conversación (`sesiones.py`)

Cada webhook HTTP es una request sin estado, pero el registro paso a paso
y el wizard de reserva necesitan recordar en qué punto de la conversación
se quedaron entre un mensaje y el siguiente. `sesiones.py` guarda un blob
JSON pequeño por número de WhatsApp en Redis (`sesion:<telefono_wa>`), con
TTL de 7 días que se renueva en cada lectura/escritura -- una conversación
activa no se corta a media pregunta, y una inactiva por más de una semana
se limpia sola sin necesidad de un job de limpieza aparte.

Es un punto de extensión genérico: cualquier flujo futuro que necesite
recordar contexto entre mensajes puede guardar su propio estado ahí bajo
su propia clave, igual que `registro_campo_pendiente` y
`reserva_wizard` (este último, un dict completo con el progreso del
wizard de reserva paso a paso -- ver "Reserva paso a paso" más arriba).

## Analítica en tiempo real

Los endpoints en `app/routes/analytics.py` consultan directamente la
tabla `reservas`. Para volumen bajo (el caso típico de una CPA
universitaria) esto es suficiente sin necesidad de un pipeline de datos
aparte. Si más adelante el volumen crece, considera:
- Vistas materializadas en Postgres para los agregados más pedidos.
- Un job periódico que precalcule métricas en vez de calcularlas on-demand.
