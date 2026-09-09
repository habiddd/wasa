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
     también antes de clasificar intent. Lo mismo aplica a un **wizard de
     cancelación en curso** (`sesion["cancelacion_wizard"]`, ver
     "Cancelar una reserva ya confirmada" más abajo).
   - Clasifica el **intent** (`llm_client.clasificar_intent`) y lo valida
     contra la whitelist (`guardrails.intent_permitido`).
   - Ejecuta la lógica de negocio correspondiente:
     - `consultar_disponibilidad`, `reservar` y `cancelar` van a
       `recursos_service.py`, que consulta/escribe `recursos`/`reservas` de
       verdad -- nunca al LLM en modo libre (ver "Por qué separar el LLM de
       la lógica de reservas").
     - Solo `ayuda` sigue delegando en el LLM conversacional
       (`llm_client.generar_respuesta`).
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
usuario nunca dio) y de hecho hoy no tienen ningún llamador real en
`recursos_service.py` (el LLM los sigue calculando porque el prompt se los
pide, pero se descartan siempre). `recursos_service.py` es quien valida
ese `recurso` contra el catálogo real antes de confiarlo
(`_resolver_recurso_para_reserva`), quien consulta disponibilidad, y quien
decide si se puede escribir en `reservas`. El LLM nunca toca la base de
datos directamente.

**Ni siquiera el campo `recurso` se acepta a ciegas.** Dos casos reales
encontrados probando el bot (no solo en teoría):

- Un mensaje que no menciona ningún activo (ej. "Reservar" a secas) podía
  volver con un `recurso` inventado -- el prompt le pide `null` en ese
  caso, pero en la práctica no siempre lo respeta. Fix:
  `_puntaje_nombre_en_texto(nombre, texto_norm)` exige que el nombre que
  devuelve el LLM tenga respaldo real en el texto del usuario (al menos
  una palabra clave, o el nombre completo, presente en el mensaje) antes
  de confiarlo; si no, se trata como si hubiera dicho `null`.
- Cuando el matcheo por palabra clave (`buscar_recursos_en_texto`) ya
  encuentra **varios** candidatos empatados (ej. "cámara canon" con dos
  modelos Canon en el catálogo, EOS R y EOS R6 -- nada en el texto
  distingue cuál), el LLM no se consulta para desempatar: manda la
  ambigüedad real y se le pregunta al usuario. El LLM solo se consulta
  cuando el matcheo por palabra clave no encontró absolutamente nada.

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
solo mensaje libre -- va preguntando un dato a la vez con menús numerados
(o mensajes tipo factura, ver más abajo), guardando el progreso en
`sesion["reserva_wizard"]` (ver "Memoria de conversación"). Progresión
para un ítem (`_siguiente_paso_wizard`):

```
elegir_activo → elegir_marca → elegir_modelo → [confirmar_accesorios]
  → fecha → hora_inicio → hora_fin → confirmar → listo
```

`confirmar_accesorios` es condicional: solo aparece si el activo recién
resuelto trae hijos marcados como incluidos por defecto (ver "Carrito de
reserva" más abajo). `_avanzar_wizard` además salta solo cualquier paso
que tenga una única opción posible (ej. una sola marca dentro de una
categoría), avisando al usuario ("solo tenemos una marca de dron, sigo con
esa") en vez de preguntar algo que no tiene sentido elegir. Si el primer
mensaje ya deja claro el activo (vía `llm_client.extraer_datos_reserva`,
ver arriba), esos pasos se saltan también; si dos activos empatan (ej.
"cámara canon" con dos modelos Canon en catálogo), se pre-llenan
`categoria` y `marca` si todos los candidatos las comparten, y el wizard
arranca directo en el primer paso que sí hace falta preguntar (ver
"Por qué separar el LLM..." más arriba).

La reserva no se limita a un solo activo: `wizard["carrito"]` es la lista
de ítems ya resueltos (ver "Carrito de reserva" más abajo). En cuanto un
ítem termina de resolverse (`elegir_activo → ... → elegir_modelo`),
`_mover_item_resuelto_al_carrito` lo mueve al carrito antes de seguir --
por eso `wizard["nombre_recurso"]` es solo "el ítem que se está resolviendo
ahora", no "el único ítem de la reserva".

### Fecha y hora: formato fijo, sin LLM -- con reconocimiento de lenguaje natural confirmado explícitamente

Los pasos de fecha y hora **no** usan el LLM: piden `dd/mm/aaaa` y
`hh:mm am/pm` exactos, parseados con regex deterministas. Esto es
deliberado -- se detectó que el LLM local, al interpretar fecha/hora en
lenguaje libre, a veces inventa un dato que el usuario nunca dio (ej.
respondió solo "Mañana" y el modelo agregó una hora de la nada). El paso
de fecha además siempre muestra primero "Hoy es {día} {dd/mm/aaaa}..."
para que el usuario note de inmediato si el bot tiene otra fecha en mente.

**La reserva puede durar más de un día.** `wizard["fecha_fin"]` acompaña a
`wizard["fecha"]` (igual a `fecha` para una reserva de un solo día).
`_parsear_rango_fecha_dd_mm_aaaa` acepta el formato exacto `dd/mm/aaaa a
dd/mm/aaaa`; en lenguaje natural, `_interpretar_rango_fecha_natural`
reconoce conectores ("desde X hasta Y", "entre X y Y", "del X al Y", "de X
a Y") y le pasa cada lado a los mismos intérpretes de una fecha única.
Se prueba **antes** que la fecha única suelta a propósito: con "desde
mañana hasta el viernes", el intérprete de fecha única encuentra la
palabra "viernes" dentro de la frase y la interpretaría completa como esa
sola fecha, perdiendo el "desde mañana".

**La hora de inicio y la de fin se piden en mensajes separados**
(`hora_inicio` y `hora_fin`), nunca juntas en un mismo mensaje tipo "3pm a
5pm" -- pedido explícito para no confundir al usuario con dos datos a la
vez, sea la reserva de un día o de varios. Cada paso usa
`_parsear_hora_individual` (una sola hora, ej. "3:00pm") sobre la fecha
que le corresponde (`fecha` para el inicio, `fecha_fin` para el fin).

Si el texto no viene en el formato exacto (fecha o una hora), antes de
rechazarlo se intenta reconocer con patrones deterministas más flexibles
(sin LLM tampoco): `_interpretar_fecha_natural` ("mañana", "pasado
mañana", días de la semana, "en N días", "22 de noviembre") y
`_interpretar_hora_individual_relajada` ("a las 3pm", "las 15 horas"). A
diferencia del formato exacto, una interpretación **nunca se acepta
directo**: el wizard entra en un paso de confirmación
(`confirmar_fecha_interpretada` / `confirmar_hora_inicio_interpretada` /
`confirmar_hora_fin_interpretada`) mostrando exactamente lo que entendió y
pidiendo un menú numerado (1. Sí / 2. No, ver `OPCIONES_CONFIRMAR_FECHA`/
`OPCIONES_CONFIRMAR_HORA`) antes de seguir. Si el usuario dice "no", solo
se borra ese dato pendiente y se vuelve a pedir en el formato exacto, sin
perder el resto de lo ya elegido. El atajo global de `gestionar_reserva`
que cancela todo el wizard cuando el usuario escribe la palabra "no" se
desactiva a propósito durante estos pasos (ahí "no" significa "esa
interpretación está mal", no "cancela la reserva") -- y también durante
`confirmar_accesorios` (ver más abajo), donde "no" significa "no quiero
esos accesorios".

**Si en vez de "1"/"2" el usuario vuelve a escribir una fecha/hora** (ej.
repetir "mañana" en lugar de responder "1"), los tres pasos de
confirmación reintentan la interpretación con el mismo criterio del paso
original antes de rechazarla: si da el mismo valor que ya estaba
pendiente, se toma como una confirmación implícita (repetirlo es
reafirmarlo, no una respuesta inválida); si da un valor distinto, se
reemplaza lo pendiente y se le vuelve a preguntar (nunca se acepta sin
pasar por el menú); solo si el texto no se reconoce como fecha/hora en
absoluto se muestra "No reconocí esa opción."

**Cortar el wizard en cualquier momento:** las palabras "cancelar",
"cancela" y "cerrar" (`PALABRAS_CANCELAR`) se revisan en `gestionar_reserva`
**antes** de despachar al paso actual, así que funcionan sin importar en
qué parte del flujo esté el usuario -- solo borran `sesion["reserva_wizard"]`;
nunca queda un activo/espacio a medio reservar porque nada se escribe en
`reservas` hasta el paso final "confirmar".

### Mensajes tipo factura

Los pasos "activo seleccionado" (`confirmar_accesorios`), fecha, hora y el
resumen final comparten una plantilla fija en código (no generada por el
LLM): título en `*negrita*`, un divisor (`――――――――――――――――――`), cada
activo del carrito en `*negrita*` con sus hijos incluidos por defecto
sangrados debajo con viñeta (`_formatear_item_factura`), otro divisor, y
los datos del paso. El aviso de seguro (`_nota_seguro`) es una línea
aparte con el nombre del activo, no un sufijo pegado a la línea del
activo -- si hay más de uno en el carrito que requiere seguro, hace falta
saber cuál es cuál. Los menús numerados *seleccionables* (`OPCIONES_CONFIRMAR`,
el menú de quitar un ítem) no llevan este formato: agregarle `*negrita*`
ahí rompería el reconocimiento de "escribe el nombre de la opción en vez
del número" (`_interpretar_opcion` compara substrings literales).

### Carrito de reserva y accesorios por defecto

El último paso (`confirmar`) no es un simple sí/no: manda el resumen (cada
ítem del carrito, quién reserva, inicio, fin) con un menú `Confirmar /
Agregar otro activo o espacio / Quitar un activo de la reserva / Cambiar
fecha / Cambiar hora / Cancelar` -- se comporta como un carrito de compras,
no como una reserva de un solo activo:

- **Accesorios por defecto van incluidos de una vez, no hay que pedirlos.**
  Al resolver un activo con hijos marcados `incluido_por_defecto=True`
  (ver "Accesorios en bundle" arriba), se agregan todos al carrito
  inmediatamente y el wizard entra al paso `confirmar_accesorios`: un
  mensaje tipo factura que los lista numerados (más una última opción
  "Ninguno") y pregunta **cuáles quitar**, no cuáles incluir --
  `_interpretar_seleccion_multiple` acepta números sueltos, varios
  separados por coma ("1,2"), o las palabras "todos"/"ninguno". Los
  índices que el usuario responde se **restan** del carrito. Diseño
  invertido a propósito respecto a un primer intento (pedirle al usuario
  que aprobara la inclusión): el usuario prefirió que el valor por
  defecto ya esté puesto y que la única acción posible sea excluir.
- **"Agregar otro activo o espacio"** reentra a `elegir_activo` para un
  ítem nuevo en el mismo horario ya elegido; al resolverse, pasa por el
  mismo `confirmar_accesorios` si aplica.
- **"Quitar un activo de la reserva"** abre un menú numerado
  (`quitar_item_menu`) con lo que hay en el carrito; quitar un padre
  también quita los accesorios que se agregaron por él. Si el carrito
  queda vacío, vuelve a pedir un activo.
- Elegir "Cambiar fecha" (por ejemplo) borra `fecha`/`fecha_fin`/`inicio`/
  `fin` del wizard y hace que `_avanzar_wizard` vuelva a preguntar justo
  eso, conservando el carrito ya armado.

**Disponibilidad: siempre se pregunta, nunca se decide en silencio.**
`_resolver_disponibilidad_carrito` ya no distingue accesorio automático de
ítem elegido a mano -- cualquiera sin unidad libre en el horario elegido
se reporta, y el wizard se detiene en el paso `resolver_disponibilidad`
(mensaje tipo factura con la lista de lo no disponible) ofreciendo
"Quitar esos activos y continuar con el resto" o "Cancelar esta reserva y
elegir otro activo". Se usa tanto al fijar la hora de fin por primera vez
como en la verificación final antes de confirmar (por si algo cambió justo
mientras el usuario decidía). Quitar arrastra los accesorios automáticos
del activo quitado, igual que el menú "Quitar un activo de la reserva".

Solo cuando el usuario elige "Confirmar" (y el carrito pasa esa revisión
de disponibilidad sin nada pendiente) se llama a
`_crear_reserva_desde_wizard`, que le pasa el carrito final a
`crear_reservas_grupo`: crea un `ReservaGrupo` y una `Reserva` por cada
unidad **en una sola transacción**. Si la restricción `EXCLUDE` de
Postgres rechaza alguna justo al confirmar (condición de carrera), se
revierte el carrito completo en vez de dejar una reserva parcial.

El id de ese `ReservaGrupo` es el **número de reserva**: lo que identifica
de forma única una reserva *hecha*, que puede cubrir varios activos (una
cámara y sus lentes comparten número). `reservas.grupo_id` es `NOT NULL`
desde la migración de `docs/SESION_2026-09-09.md` (sección 7), que además
le asignó grupo a las filas anteriores al carrito; así ninguna reserva
puede quedar sin número, ni las que crea el bot ni las de `seed.py`.

### Cancelar una reserva ya confirmada

Distinto de "cortar el wizard" de arriba (que aborta una reserva que
**todavía no se escribió** en la base): esto es el intent `cancelar`,
para anular una reserva que ya quedó `confirmada`. Vive en
`gestionar_cancelacion` (`recursos_service.py`), con su propio wizard corto
en `sesion["cancelacion_wizard"]` que webhook.py trata igual que
`reserva_wizard` -- mientras esté activo, la respuesta del usuario (un
número) va directo ahí, no a clasificación de intent.

**Solo el usuario que reservó puede cancelar esa reserva.** El único punto
de entrada real es `usuario`, que llega ya resuelto por el número de
WhatsApp que mandó el mensaje (`usuarios_service`, antes de esta función);
nada del texto del chat puede afirmar "soy tal usuario" o "cancela la
reserva con id X". Por eso toda consulta filtra siempre por
`Reserva.usuario_id == usuario.id`:

- `_reservas_cancelables_de(usuario.id)` -- la única lista que se le
  muestra al usuario, ya filtrada por dueño (y por `estado='confirmada'`
  y `fin` en el futuro), agrupada por `grupo_id` igual que se reservó (un
  padre y sus accesorios se cancelan juntos).
- El `UPDATE ... SET estado='cancelada'` final repite el mismo filtro por
  `usuario_id`, sobre **todos** los ids que el usuario haya elegido -- ese
  es el que de verdad importa, porque es el que ejecuta el cambio; el
  filtro al listar solo evita mostrarle a alguien una lista ajena, no
  evitaría por sí solo que alguien cancelara algo que no es suyo si se
  saltara el menú.

**Selección múltiple.** El paso "elegir reserva" reutiliza
`_interpretar_seleccion_multiple` (la misma función del paso
`confirmar_accesorios`): un número, varios separados por coma ("1,3") o
"todos" -- se puede cancelar más de una reserva en la misma conversación
sin repetir todo el flujo por cada una. Las reservas elegidas se
reagrupan (`_agrupar_por_reserva_grupo`) para el paso de confirmación, así
que un grupo elegido a medias sigue mostrándose junto.

El paso de confirmación (`confirmar_cancelacion`) es un menú numerado
(`OPCIONES_CONFIRMAR_CANCELACION`: "1. Sí, cancelar" / "2. No, dejar la
reserva como está"), no una pregunta de sí/no en texto libre -- mismo
mecanismo (`_interpretar_opcion`) que el resto de los menús del bot, a
pedido explícito para que la respuesta sea inequívoca por número.

Mensaje tipo factura para listar (`_mensaje_elegir_reserva_a_cancelar`) y
para confirmar (`_mensaje_confirmar_cancelacion`), mismo criterio que el
resto del wizard de reserva: nunca se cancela de una sola respuesta, hay
un paso `confirmar_cancelacion` explícito de por medio. `PALABRAS_CANCELAR`
("cancelar", "cancela", "cerrar") aborta el wizard de cancelación en
cualquier paso sin tocar la base, igual que en `gestionar_reserva`.

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
su propia clave, igual que `registro_campo_pendiente`, `reserva_wizard`
(dict completo con el progreso del wizard de reserva paso a paso -- ver
"Reserva paso a paso" más arriba) y `cancelacion_wizard` (mismo patrón
para cancelar una reserva ya confirmada -- ver "Cancelar una reserva ya
confirmada" más arriba).

## Dashboard interno: por qué React + shadcn/ui con un build separado

El panel de monitoreo (`/dashboard`, ver `README.md` sección 5 y
`flask-app/app/routes/dashboard.py`) empezó como una sola plantilla Jinja
(`dashboard.html`) con HTML/CSS/JS vanilla, sin build step -- suficiente
mientras era un puñado de tarjetas y un par de tablas. Al pedirse una
migración a [shadcn/ui](https://ui.shadcn.com/) (ver
`docs/SESION_2026-09-08.md`, sección 3) hubo que decidir entre dos caminos:

- Imitar la estética de shadcn con CSS a mano, sin tocar el resto del
  stack (Jinja + JS vanilla).
- Migrar de verdad a los componentes de shadcn, que son React sobre
  Tailwind + Radix UI y requieren un bundler -- no hay forma de usarlos
  tal cual sobre una plantilla Jinja.

Se optó por la segunda porque era lo que se pidió explícitamente (no una
imitación visual). El costo es un segundo proyecto con su propio ciclo de
build (Node) junto al backend Python:

- **`dashboard-frontend/`** vive en la raíz del repo, al mismo nivel que
  `flask-app/` y `baileys-bot/`, no anidado dentro del backend: nada del
  frontend se importa desde Python ni al revés, así que colgarlo de
  `flask-app/` solo hacía que un proyecto "100% Python" pareciera tener un
  subproyecto Node adentro. Como hermano, cada contexto de build de
  Compose coincide con un directorio de primer nivel.
- **`dashboard-frontend/`** es una SPA de React (Vite + TS +
  Tailwind v4 + shadcn/ui) independiente del resto de la app Flask. No
  usa server-side rendering ni comparte código Python -- habla con el
  backend solo por los mismos endpoints JSON de siempre
  (`/dashboard/api/*`, sin cambios de contrato).
- **Contenedor propio, servido por Nginx.** `dashboard-frontend` es un
  servicio aparte en `docker-compose.yml` (no un stage dentro de la imagen
  de `flask-app`). Su `Dockerfile` es multi-stage: un stage `node:20-slim`
  corre `npm ci && npm run build`, y el stage final es `nginx:alpine`
  sirviendo el `dist/` resultante. `flask-app` quedó de nuevo como un
  proyecto 100% Python -- no necesita Node para construirse, y
  `dashboard.py` ya no sirve HTML/assets, solo la API bajo
  `/dashboard/api/*`.
- **Nginx conecta ambos contenedores.** `dashboard-frontend/nginx.conf`
  sirve el shell estático en `/dashboard/` y hace `proxy_pass` de
  `/dashboard/api/` hacia `http://flask-app:5000/dashboard/api/` sobre la
  red interna `backend` de Compose -- el navegador solo ve un origen
  (el puerto publicado por `dashboard-frontend`, `8080` por defecto).
- **Autenticación en dos capas, sin duplicar credenciales en un tercer
  formato.** Nginx exige Basic Auth (`auth_basic` con un `.htpasswd`
  generado al arrancar el contenedor, en `docker-entrypoint.sh`, a partir
  de las mismas `DASHBOARD_USER`/`DASHBOARD_PASSWORD`) para servir el
  shell estático -- así el navegador sigue pidiendo login apenas se
  navega a `/dashboard/`, igual que cuando Flask servía el HTML
  directamente. Las llamadas a `/dashboard/api/*` viajan con ese mismo
  header `Authorization` (Nginx lo reenvía tal cual al hacer proxy) y
  `flask-app` las revalida con su propio `before_request` -- el chequeo de
  Flask no se quitó, queda como defensa en profundidad por si algo llega
  a `flask-app:5000` sin pasar por Nginx.
- **En desarrollo**, `dashboard-frontend/` corre con su propio servidor de
  Vite (`npm run dev`, puerto 5173) con un proxy configurado hacia
  `http://localhost:5000` para `/dashboard/api/*` -- así los cambios de UI
  recargan al instante sin tener que reconstruir ninguna imagen Docker.

## Calendario del dashboard: un evento por reserva, no por activo

El panel "Calendario" (`dashboard-frontend/src/features/calendario/`, ver
`docs/SESION_2026-09-09.md` secciones 5-7) muestra las reservas de activos
y lugares en vistas de mes, semana y día. Cuatro decisiones que conviene
tener presentes antes de tocarlo:

- **Se pide por rango, no todo.** `GET /dashboard/api/reservas` acepta
  `?desde/?hasta` y devuelve las reservas que **solapan** ese rango (no las
  que empiezan dentro), porque una reserva de varios días tiene que
  aparecer aunque haya empezado antes del periodo visible. Sin parámetros
  el endpoint sigue devolviendo las últimas 500 por `creado_en`, que es lo
  que consume el panel "Sesiones".
- **Los días se calculan en `America/Bogota`, nunca en la zona del
  navegador.** Las reservas se guardan en UTC pero el negocio opera en hora
  local de la CPA (`recursos_service.ZONA_HORARIA_CPA`); si el calendario
  agrupara por día con la zona del equipo, el mismo dashboard mostraría
  cosas distintas según quién lo abre. Todo pasa por `Intl.DateTimeFormat`
  con `timeZone` fijo en `calendario-utils.ts`, incluidos los
  `<input type="datetime-local">` del formulario de edición.
- **Un evento agrupa las reservas del mismo usuario y horario**, no las del
  mismo `grupo_id`. Una reserva hecha para cinco activos llenaba el día de
  cajas idénticas. Se agrupa por `(usuario_id, inicio, fin)` a propósito:
  cubre también el caso de dos confirmaciones distintas del mismo usuario a
  la misma hora (se ven como un evento con dos números de reserva), y hace
  que una línea movida a otro horario salga como evento aparte. El filtrado
  (tipo, recurso, estado) ocurre **antes** de agrupar.
- **Editar un evento de varios activos es atómico.** El modal usa
  `PATCH /dashboard/api/reservas/bloque` con los ids de todas sus líneas:
  el horario y el estado se aplican a todas o a ninguna, en una sola
  transacción. El solapamiento no se re-valida en Python -- lo garantiza el
  `EXCLUDE` de Postgres descrito más arriba, y el backend solo traduce esa
  violación a un mensaje legible.

## Analítica en tiempo real

Los endpoints en `app/routes/analytics.py` consultan directamente la
tabla `reservas`. Para volumen bajo (el caso típico de una CPA
universitaria) esto es suficiente sin necesidad de un pipeline de datos
aparte. Si más adelante el volumen crece, considera:
- Vistas materializadas en Postgres para los agregados más pedidos.
- Un job periódico que precalcule métricas en vez de calcularlas on-demand.
