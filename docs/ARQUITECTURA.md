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
     "Cancelar una reserva ya confirmada" más abajo), y a un **menú de
     fallback en curso** (`sesion["menu_fallback"]`, ver "Menú de fallback"
     más abajo).
   - Clasifica el **intent** (`llm_client.clasificar_intent`) y lo valida
     contra la whitelist (`guardrails.intent_permitido`). Si el intent no
     está permitido (en la práctica, `"otro"`), en vez de bloquear con un
     mensaje fijo se le ofrece al usuario el menú de fallback (ver más
     abajo) -- `guardrails.registrar_interaccion` sigue auditando
     `motivo_bloqueo="intent_no_permitido"` solo si el usuario agota los
     reintentos del menú sin elegir nada reconocible.
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

Para escribirle a alguien **sin un mensaje suyo al que responder** (el
aviso de que la CPA confirmó su reserva, ver "Confirmación de la CPA"), el
JID completo se guarda en `usuarios.jid_wa` con cada mensaje entrante
(`usuarios_service.obtener_o_crear_usuario`) y se lee con
`whatsapp_client.jid_de`. Solo si no hay uno guardado (usuarios que no han
escrito desde que existe la columna) se rearma con el número, que no llega
a las cuentas `@lid`. Toda reserva nueva la crea un mensaje del estudiante,
así que las pendientes siempre tienen su JID.

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

**Ni siquiera el campo `recurso` se aceptaba a ciegas**, y esa desconfianza
se apretó tanto que terminó dejando la llamada entera sin efecto. Los dos
guards, encontrados probando el bot (no solo en teoría):

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

Los dos juntos son contradictorios -- "sin candidatos" implica puntaje 0
para todos los nombres, así que el primer guard nunca podía pasar cuando el
segundo dejaba entrar. Por eso `_iniciar_reserva` ya no hace esa llamada;
ver "El wizard de reservas no usa el LLM" más abajo.

### Clasificación de intent en tres capas (`llm_client.clasificar_intent`)

El modelo en uso es `lfm2.5-2.6b` (LM Studio). Razona antes de responder
y **no se le puede apagar el razonamiento por API** -- probados
`chat_template_kwargs.enable_thinking=false`, `reasoning_effort` y
`/nothink` en el system prompt, los tres siguen gastando ~190 tokens. Una
clasificación cuesta entre 13 y 25 segundos. Eso condiciona todo el
diseño: la meta no es solo acertar, es llamar al modelo lo menos posible.

1. **Ruta rápida, sin red (`_clasificar_sin_llm`).** Si el mensaje trae
   una señal inequívoca -- un marcador de reserva, de cancelación, de
   disponibilidad, de ayuda, o es un saludo suelto -- se responde al
   instante. Medido sobre una batería de 20 mensajes realistas, 11
   resolvieron aquí (ver `docs/PRUEBAS_CHAT_2026-09-09.md`). Estos son
   justamente los casos que cualquier modelo acierta, así que no se pierde
   precisión.

   Excepción deliberada: `MARCADORES_DESISTIR` ("ya no", "no voy a",
   "desisto"...). "Ya no voy a necesitar la reserva de mañana" contiene la
   subcadena `reserv`, y sin esta excepción la ruta rápida abriría el
   wizard de **reservas** justo cuando el usuario quiere lo contrario. Con
   una de esas presentes, la ruta rápida se abstiene y decide el LLM (que
   sí responde `cancelar`, verificado).

2. **El LLM, validado después (`_validar_intent_de_accion`).** Cuando el
   modelo responde `reservar`, `cancelar` o `consultar_disponibilidad`, se
   le exige que el mensaje tenga **señal de dominio**: un verbo de
   reserva, un equipo, un espacio, una fecha/hora, o un nombre real del
   catálogo (`recursos_service.listar_terminos_catalogo()`, que
   `webhook.py` le pasa al clasificador -- desde plurales/descripción
   también incluye las palabras clave de las descripciones, de 4 letras o
   más). Si no hay nada de eso, el
   mensaje no puede haber sido una petición por más que el LLM lo diga, y
   se enruta a `saludo` (si era conversación) o a `otro`.

   Esto es **mucho más holgado** que el guard anterior
   (`_corroborar_o_heuristico`), que exigía la palabra clave literal de la
   *misma categoría*. Aquel guard existía porque `liquid/lfm2.5-1.2b`
   tenía un sesgo medido hacia `reservar`/`cancelar` ante cualquier
   mensaje ambiguo; con `lfm2.5-2.6b` ese sesgo desaparece ("buenos días"
   → `otro`, donde el 1.2b decía `reservar`), así que la rigidez ya solo
   estorbaba: "tienen algo para grabar el jueves" no contiene ninguna
   palabra clave de `reservar` y terminaba en el menú de fallback aunque
   el modelo lo hubiera entendido perfecto.

3. **Heurístico de respaldo (`_clasificar_intent_heuristico`).** Si el LLM
   no responde, se pasa de 45 s, o agota el presupuesto de tokens
   (`finish_reason == "length"`), responde el heurístico por palabras
   clave. Los topes son deliberados: toda clasificación que el modelo
   acierta llegó en 30 s o menos, y pasado eso lo que hay es un modelo
   enredado que además concluye mal -- cortar da mejor respuesta y menos
   espera.

`ayuda`, `saludo` y `otro` quedan fuera de la validación del punto 2 a
propósito: equivocarse ahí como mucho repite el texto de ayuda, contesta
un saludo, o abre el menú de fallback -- nunca arranca un wizard ni le
afirma al usuario una disponibilidad falsa.

### Conversación normal: el intent `saludo`

Un saludo, una cortesía o una pregunta sobre el bot ("hola", "gracias",
"quién eres") no son un pedido, pero tampoco son un mensaje
incomprensible: responderlos con el menú de fallback se siente como un
portazo. Por eso existe `saludo`, permitido en `ALLOWED_INTENTS`.

`_es_charla_social` decide **restando**: al texto se le quitan las frases
y palabras sociales conocidas y, si lo que queda es vacío o puro relleno,
era un saludo suelto. Restar en vez de buscar coincidencias evita el falso
positivo obvio -- "hola, necesito una cámara" contiene "hola" pero deja
residuo ("necesito camara"), así que sigue siendo un pedido.

`llm_client.responder_charla` contesta en dos velocidades: un saludo pelado
("hola", "buenos días") sale de un texto fijo rotado entre varias
redacciones -- instantáneo, y con este modelo eso importa, porque generar
una respuesta cuesta 10-20 s y es una eternidad para contestar "hola". Lo
demás que sea social pero no un saludo pelado ("cómo estás", "quién eres")
sí pasa por el LLM, con el texto fijo como respaldo si falla. Ninguna de
las dos rutas consulta la base de datos ni afirma nada sobre
disponibilidad.

**Con esto, al menú de fallback solo llega lo que de verdad no se
entiende.**

### El wizard de reservas no usa el LLM

`_iniciar_reserva` **ya no llama** a `llm_client.extraer_datos_reserva`. De
los tres campos que devuelve, `inicio` y `fin` nunca se usaron (la fecha y
la hora se preguntan en pasos de formato fijo, ver más abajo), y `recurso`
se consumía por una rama inalcanzable: `_resolver_recurso_para_reserva`
solo mira el nombre del LLM cuando `buscar_recursos_en_texto` no encontró
ningún candidato, y a la vez le exige `_puntaje_nombre_en_texto(...) > 0`
-- pero `candidatos` **es** el conjunto de nombres con puntaje > 0, así que
las dos condiciones no pueden cumplirse juntas.

Costaba 54-78 s por cada reserva iniciada, sin poder cambiar el resultado.
La función y su prompt siguen en `llm_client.py` por si se quieren usar de
verdad; ya no están en el camino crítico. Detalle y verificación en
`docs/PRUEBAS_CHAT_2026-09-09.md`, sección 5.

## Inventario: varias unidades del mismo modelo (`recursos_service.py`)

Cada fila de `recursos` con `tipo='equipo'` representa una unidad física
concreta: tiene `placa` (número de inventario, único; 7-8 caracteres
alfanuméricos -- el padre usa 8 dígitos terminados en 0 por convención,
ej. `12345670`, un hijo puede tener la misma placa con el último dígito
distinto de 0 o un código propio como `LEN0022`), dos niveles de
clasificación -- `categoria` (agrupación gruesa, por ahora un solo valor,
"grabación audiovisual") y `subcategoria` (ej. "cámara", "dron" -- el tipo
concreto de equipo, independiente de `tipo` que solo distingue
`equipo`/`espacio`; es el nivel sobre el que trabaja el asistente de
reserva del bot, ver más abajo) --, `marca`, `modelo` e `identificador`
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

**Ubicación física (`espacio_id`):** además de `padre_id` (accesorios en
bundle), `recursos.espacio_id` es una SEGUNDA jerarquía auto-referencial,
completamente independiente de la primera -- dice en qué lugar está
físicamente un activo (ej. la Sala 4 tiene 3 iMac y 2 MacBook), no de qué
otro equipo depende. Un activo puede tener padre y no tener lugar, tener
lugar y no tener padre, ambos o ninguno; nada las ata entre sí a nivel de
esquema. El destino de `espacio_id` debe ser una fila con `tipo='espacio'`,
pero eso se valida en el dashboard (`flask-app/app/routes/dashboard.py`,
`_validar_espacio`), no con un `CHECK` de Postgres -- un `CHECK` no admite
subqueries, y un trigger daría un error genérico en vez de uno que el
dashboard pueda traducir a un mensaje legible. Un espacio, en cambio, no
tiene lugar ni padre -- no se anidan lugares, no es un caso de uso de la
CPA. Eso sí es un `CHECK` sobre la propia fila
(`recursos_espacio_sin_padre_ni_lugar`); el dashboard además exige que el
padre de un activo sea un equipo, y oculta Padre y Lugar en el formulario
de un espacio. Como un espacio nunca tiene lugar, la cadena de lugares no
pasa de un nivel y no hace falta chequear ciclos (con `padre_id` sí).
Regla de propagación: si a un activo con hijos (por `padre_id`)
se le cambia el lugar, sus hijos que estaban en el mismo lugar que él se
mueven con él por defecto (`propagar_espacio`, default `true` en el PATCH);
los hijos que alguien ya había puesto a mano en otro lugar no se tocan, y
esa excepción manual queda pegajosa ante cambios futuros del padre.
`recursos_service.py` (el bot) SÍ usa esta jerarquía al reservar un
espacio -- ver "Contenidos de un espacio (`espacio_id`)" más abajo.

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
  refieres a X, dime el nombre completo"). Cada palabra del nombre tolera
  plural regular (`_regex_palabra`: "imacs" → iMac, "lentes" → Lente…) sin
  relajar el `\b`, así "eos r" no empata con "eos r6". Suma
  `BONO_MODELO_COMPLETO` si el texto trae el `modelo` completo ("eos r6"),
  porque lo que distingue un modelo de otro suele tener menos de 4 letras.
- **Búsqueda por niveles:** nombre (+modelo) → subcategoría → descripción
  (`buscar_recursos_por_descripcion`). Cada nivel solo se mira si el
  anterior no encontró nada, así que la descripción nunca altera un
  resultado por nombre ni `es_match_debil`. En la descripción cuentan
  palabras de 3 letras o más ("mac", "usb"), sin
  `PALABRAS_VACIAS_DESCRIPCION`. Si empatan más de
  `MAX_MODELOS_POR_DESCRIPCION` modelos, el nivel se descarta.
  Consecuencia: la `descripcion` de un activo ahora es vocabulario de
  búsqueda del bot, y conviene escribirla desde el dashboard con las
  palabras con que lo pediría un estudiante (sinónimos, usos).
- `_unidad_libre_en_rango(nombre, inicio, fin)`: recorre las unidades del
  grupo y devuelve la primera sin choque de horario -- el usuario nunca
  elige la placa exacta, solo el modelo; el bot reparte automáticamente
  cualquier unidad libre.
- `_subcategorias_activas()`, `_grupos_por_subcategoria()`,
  `_detectar_subcategoria_en_texto()`: resuelven preguntas por subcategoría
  (ej. "qué cámaras tenemos") comparando contra `subcategoria` aunque el
  texto no mencione ningún modelo, y permiten sugerir otras marcas/modelos
  dentro de la misma subcategoría.
- `evaluar_consulta_disponibilidad` (intent `consultar_disponibilidad`)
  lista el **estado agregado de cada modelo** (`_estado_grupo`: cuántas
  unidades libres de cuántas totales) -- el usuario quiere saber qué hay y
  qué no, así que nunca pregunta "¿cuál de estos?". Si ningún nivel
  identifica nada, se envía el enlace de `CATALOGO_URL` (variable de
  `.env`, cableada en `docker-compose.yml` y `config.py`), que invita a ver
  el catálogo y a escribir el nombre del equipo o espacio. Si está vacía,
  se conserva el listado del estado de todo el inventario.
- Toda hora se interpreta en `America/Bogota` (zona horaria fija de la
  CPA) y se guarda en UTC (columnas `TIMESTAMPTZ`).
- **La garantía real contra choques de horario es de Postgres, no de la
  app**: `postgres/init/02-schema.sql` agrega la extensión `btree_gist` y
  una restricción `EXCLUDE USING gist (recurso_id WITH =, tstzrange(inicio,
  fin) WITH &&) WHERE (estado IN ('confirmada', 'pendiente'))` sobre
  `reservas`. Postgres rechaza cualquier INSERT/UPDATE que se solape con una
  reserva confirmada o pendiente de confirmación del mismo recurso (la
  pendiente también aparta el activo mientras la CPA decide, ver
  `ESTADOS_QUE_OCUPAN` en `recursos_service.py`), sin importar si la vía es la app, una
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
`sesion["reserva_wizard"]` (ver "Memoria de conversación"). Antes de tocar
ningún activo, `_avanzar_wizard` resuelve un puñado de preguntas que son de
la reserva completa, no de un ítem en particular:

```
tipo_uso → [nrc] → modalidad → [tamano_grupo → integrante_id ×(N-1) → confirmar_grupo]
  → elegir_activo → ... → listo
```

`modalidad` se pregunta **siempre** (particular o de asignatura, da igual)
-- es independiente de `tipo_uso`: un grupo puede ser particular o de
asignatura, y una reserva particular puede ser en grupo. Ver "Modalidad de
la reserva: individual o en grupo" más abajo para el detalle de esos
sub-pasos. Progresión para un ítem, una vez resueltas esas preguntas
(`_siguiente_paso_wizard`):

```
elegir_activo → elegir_marca → elegir_modelo → [confirmar_accesorios]
  → [confirmar_contenidos_espacio] → sale_de_barranquilla → fecha
  → hora_inicio → hora_fin → confirmar → listo

hora_fin/confirmar → [resolver_disponibilidad → resumen | elegir_alternativa]
```

`confirmar_accesorios` es condicional: solo aparece si el activo recién
resuelto trae hijos marcados como incluidos por defecto (ver "Carrito de
reserva" más abajo). `confirmar_contenidos_espacio` también es condicional:
solo aparece si el ítem recién resuelto es un espacio con contenidos (ver
"Contenidos de un espacio" más abajo); un equipo normal nunca pasa por
ahí. `_avanzar_wizard` además salta solo cualquier paso
que tenga una única opción posible (ej. una sola marca dentro de una
subcategoría), avisando al usuario ("solo tenemos una marca de dron, sigo
con esa") en vez de preguntar algo que no tiene sentido elegir. Si el
primer mensaje ya deja claro el activo (por palabra clave,
`buscar_recursos_en_texto`), esos pasos se saltan también; si dos activos
empatan (ej. "cámara canon" con dos modelos Canon en catálogo), se
pre-llenan `subcategoria` y `marca` si todos los candidatos las comparten,
y el wizard arranca directo en el primer paso que sí hace falta preguntar
(ver "Por qué separar el LLM..." más arriba). Si el activo solo se
reconoce por la descripción ("quiero reservar un mac"), nunca se resuelve
directo: esos candidatos solo prellenan `subcategoria` y `marca`, y
únicamente si todos son equipos de una misma subcategoría. Un espacio no
tiene subcategoría: antes "reservar audio" prellenaba "interfaz" y
descartaba la Cabina de audio 2 sin avisar.

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

### Cláusula de seguro: salida de Barranquilla

Antes de pedir la fecha, el wizard pregunta si los activos van a salir de
Barranquilla. Si la respuesta es sí, la CPA tiene que tramitar un seguro y
la reserva no puede empezar antes del `DIAS_MINIMOS_TRAMITE_SEGURO`-ésimo
(5) día **hábil** (lunes a viernes, sin festivos de Colombia -- paquete
`holidays`, `holidays.country_holidays("CO")`) después de hoy, hora de la
CPA (`ZONA_HORARIA_CPA`); hoy no cuenta como día hábil transcurrido. Por
ejemplo: viernes 18/09/2026 → viernes 25/09/2026 (fin de semana no cuenta);
miércoles 07/10/2026 → jueves 15/10/2026 (salta el lunes 12/10, festivo).
La regla no distingue de qué activo se trate.

Este sí es un seguro de verdad (un trámite de la CPA antes de que los
activos salgan de la ciudad), y no tiene nada que ver con
`recursos.requiere_confirmacion`: esa columna se llamaba `requiere_seguro`
y se renombró justamente porque nunca fue un seguro (ver
`docs/SESION_2026-09-10.md`). Un activo puede necesitar confirmación de la
CPA, seguro por salir de Barranquilla, las dos cosas o ninguna.

- **Orden:** el paso "sale_de_barranquilla" va justo antes de "fecha"
  (`_siguiente_paso_wizard`), ya con el carrito resuelto (después de
  `confirmar_accesorios`/`confirmar_contenidos_espacio` si aplican) -- así
  el mensaje de "fecha" puede mostrar de una vez la fecha mínima si hace
  falta.
- **`fecha_minima_sale_de_barranquilla(hoy=None)`** (pública, en
  `recursos_service.py`) es la única fuente de la fecha mínima -- toma
  `hoy` como parámetro para poder probarla con distintas fechas; con
  `None` usa el día de hoy en la CPA. Se apoya en `sumar_dias_habiles`
  (`@lru_cache`, cuenta días hábiles uno por uno desde `desde`, sin
  incluirlo) y en `es_dia_habil` (lunes a viernes y no festivo). Ni el bot
  ni el dashboard recalculan el plazo por su cuenta: el dashboard pide la
  fecha vigente a `GET /dashboard/api/seguro/fecha-minima` y usa
  `fecha_minima_seguro` (ya calculada desde `creado_en`) que trae cada
  reserva en `GET /dashboard/api/reservas`.
- **Validación central:** vive al tope del `while` de `_avanzar_wizard`
  (`_error_plazo_seguro`), antes de calcular el siguiente paso -- corre en
  cada vuelta, así que cubre tanto la primera fecha que el usuario escribe
  como un cambio posterior ("Cambiar fecha" o "Cambiar si sale de
  Barranquilla" desde el resumen). Si la fecha puesta queda antes del
  plazo, se borra (`fecha`/`fecha_fin`/`inicio`/`fin`) y se avisa en vez de
  dejarla pasar. `_crear_reserva_desde_wizard` repite el chequeo justo
  antes de escribir en la base, por si el tiempo pasó mientras la sesión
  estaba pausada.
- **Rechazo temprano en fechas de lenguaje natural:** una fecha en
  `dd/mm/aaaa` (o un rango en ese formato) se aplica directo al wizard y
  cae en la validación central de arriba. Una fecha en lenguaje natural
  ("mañana", "el viernes") en cambio solo queda *pendiente* hasta que el
  usuario confirma "Entendí tal fecha, ¿es correcto?" -- ahí la validación
  central todavía no la ve, así que `_continuar_reserva` la rechaza antes,
  con `_error_plazo_seguro`, en los tres puntos donde se interpreta una
  fecha natural: el rango (`_interpretar_rango_fecha_natural`), la fecha
  única (`_interpretar_fecha_natural`) y cuando el usuario reescribe la
  fecha estando ya en el paso "confirmar_fecha_interpretada". Si el
  usuario confirma con "1"/"sí" una fecha que ya pasó ese chequeo, sigue
  cubierta por la validación central al aplicarla.
- **Parser determinista:** `_interpretar_si_no` nunca usa el LLM (mismo
  criterio que fecha/hora, ver más arriba) -- entiende el número del menú,
  sí/no explícitos, duda ("no sé", "tal vez" -> vuelve a preguntar) y
  algunas palabras de contexto ("salen para Santa Marta" -> sí, "se quedan
  en Barranquilla" -> no). No se reutiliza `_interpretar_opcion` para este
  paso: compara por substring de la etiqueta, y eso daría falsos positivos
  con "no".
- **Exención en `gestionar_reserva`:** "sale_de_barranquilla" está en la
  lista de pasos donde "no" no cancela el wizard (junto a
  `confirmar_accesorios`/`confirmar_contenidos_espacio` y las
  confirmaciones de fecha/hora interpretadas) -- ahí "no" responde "se
  quedan en Barranquilla", no "cancela la reserva". "cancelar"/"cerrar"
  siguen cortando el flujo igual que en cualquier otro paso.
- **`sale_de_barranquilla` NULL en reservas viejas:** la columna
  (`postgres/init/02-schema.sql`) es NULL solo en reservas creadas antes de
  esta pregunta; el bot siempre guarda TRUE/FALSE. Una sesión de Redis
  vieja, parada en "confirmar" sin este dato, recibe la pregunta antes de
  crear la reserva en vez de guardarla con el campo vacío.
- **Renombre de la columna:** nació como `sale_area_metropolitana` (cuando
  la pregunta era por el área metropolitana con días calendario); la
  migración renombra en vez de recrear si encuentra la columna vieja sin la
  nueva. `_continuar_reserva` y la exención de `gestionar_reserva` también
  aceptan "area_metropolitana" como alias del nombre del paso, por si una
  sesión de Redis quedó parada ahí justo antes del despliegue.
- **El dashboard solo advierte:** desde `ReservaDetalle.tsx` la CPA puede
  editar la respuesta y el inicio aunque quede antes del plazo del seguro
  -- se le avisa (mismo estilo del aviso de "por confirmar"), pero no se
  bloquea el guardado; la decisión final es suya.

### Mensajes tipo factura

Los pasos "activo seleccionado" (`confirmar_accesorios`), fecha, hora y el
resumen final comparten una plantilla fija en código (no generada por el
LLM): título en `*negrita*`, un divisor (`――――――――――――――――――`), cada
activo del carrito en `*negrita*` con sus hijos incluidos por defecto
sangrados debajo con viñeta (`_formatear_item_factura`), otro divisor, y
los datos del paso. La factura final lleva el número de reserva, y su
título y aviso dependen de los activos con `requiere_confirmacion`
(`_estado_de_reserva_creada`): ninguno -> *RESERVA CONFIRMADA*; algunos ->
*RESERVA PARCIAL*, con una línea de lo confirmado y otra de lo pendiente;
todos -> *RESERVA PENDIENTE DE CONFIRMACIÓN*. El aviso va en líneas aparte
con el nombre de cada activo, no como sufijo pegado a su línea -- si hay
más de uno en el carrito, hace falta saber cuál espera y cuál no (ver
"Confirmación de la CPA" más abajo). Los menús numerados *seleccionables* (`OPCIONES_CONFIRMAR`,
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
  (`quitar_item_menu`) con lo que hay en el carrito; quitar un ítem
  también quita, en cascada, todo lo que se agregó por él (sus accesorios;
  si es un lugar, sus activos y los hijos de esos activos -- ver
  `_quitar_del_carrito`). Si el carrito queda vacío, vuelve a pedir un
  activo.
- Elegir "Cambiar fecha" (por ejemplo) borra `fecha`/`fecha_fin`/`inicio`/
  `fin` del wizard y hace que `_avanzar_wizard` vuelva a preguntar justo
  eso, conservando el carrito ya armado.
- **"Ver alternativas a X"** es condicional: una opción por cada activo
  raíz que se quitó del carrito en la última pausa de disponibilidad
  (`wizard["alternativas_para"]`, ver "Alternativas a un activo no
  disponible" más abajo). `_opciones_confirmar` las inserta justo después
  de "Agregar otro activo o espacio" -- no aparece si no se quitó nada, o
  ya se resolvió (se confirmó, se agregó otro activo aparte, se cambió
  fecha/hora, o se eligió/descartó una alternativa para ese activo).

### Contenidos de un espacio (`espacio_id`)

Cuando el ítem recién resuelto es un LUGAR (`tipo='espacio'`, ej. "Cabina
de audio 2"), el fold-in de `_avanzar_wizard` hace lo mismo que con los
accesorios de `confirmar_accesorios`, pero sobre la jerarquía `espacio_id`,
no `padre_id` -- son independientes (ver "Ubicación física" más arriba):
**todo lo que vive en el lugar entra al carrito de una**, y el usuario
quita lo que no necesite.

`_contenidos_de_espacio(nombre_espacio)` arma un árbol: las RAÍCES son los
equipos activos del lugar cuyo padre por `padre_id` no está también en el
lugar, y debajo de cada una van sus hijos por `padre_id` (recursivo). Solo
equipos: un lugar dentro de otro lugar no se ofrece, a propósito -- no es
un caso de uso de la CPA.

**Unidades concretas, no modelos.** Los accesorios normales se reservan
por modelo (cualquier unidad libre, `_unidad_libre_en_rango`). Los
contenidos de un lugar no: cada ítem guarda `unidades` (ids), así que "la
sala 4 con sus iMac" son las 3 iMac que están en esa sala -- se muestran
como "iMac 24 M3 ×3" y se reservan las 3, no una iMac cualquiera del
inventario. `_resolver_disponibilidad_carrito` exige que todas esas
unidades estén libres; si solo algunas lo están, lo dice ("1 de 3
unidades") y "Quitar esos activos y volver al resumen" deja al ítem con
las libres en vez de descartar el modelo entero.

El paso `confirmar_contenidos_espacio`
(`_mensaje_confirmar_contenidos_espacio`) numera TODAS las líneas, padres
e hijos, con los hijos sangrados bajo su padre, más "Ninguno (dejar todos
incluidos)" y la pista de responder "todos" si solo se necesita el lugar.
Los números que responde el usuario se **quitan**; quitar un padre
arrastra lo que va debajo (`_quitar_del_carrito`, en cascada por `por`),
quitar solo un hijo (ej. el brazo del micrófono) deja al padre. Un "sí" a
secas se repregunta en vez de tomarse como "todos". Si el lugar no tiene
contenidos, el wizard sigue directo a pedir fecha.

Cada ítem lleva en `por` quién lo trajo (el lugar para las raíces, el
activo padre para sus hijos); con eso `_niveles` calcula la sangría en
todos los mensajes tipo factura (fecha, horas, resumen, confirmación): el
lugar en negrita, sus activos con `•`, los hijos de esos activos con `◦`.

Por el mismo motivo que `confirmar_accesorios` (ahí "no" responde a
"¿quieres quitar alguno?"), `confirmar_contenidos_espacio` está excluido
del atajo global que cancela el wizard al ver la palabra "no" -- si no lo
estuviera, "no" cancelaría toda la reserva en vez de seguir a pedir fecha.

**Disponibilidad: siempre se pregunta, nunca se decide en silencio.**
`_resolver_disponibilidad_carrito` ya no distingue accesorio automático de
ítem elegido a mano -- cualquiera sin unidad libre en el horario elegido
se reporta, y el wizard se detiene en el paso `resolver_disponibilidad`
(mensaje tipo factura con la lista de lo no disponible) ofreciendo
"Quitar esos activos y volver al resumen" (o "...y elegir otro" si quitarlos
deja el carrito vacío -- no hay resumen al que volver, ver
`OPCIONES_NO_DISPONIBLE_CARRITO_VACIO`) o "Cancelar toda la reserva". Se usa
tanto al fijar la hora de fin por primera vez como en la verificación final
antes de confirmar (por si algo cambió justo mientras el usuario decidía).
Quitar arrastra los accesorios automáticos del activo quitado, igual que el
menú "Quitar un activo de la reserva".

**Un "Confirmar" nunca se ejecuta solo: cualquier pausa por disponibilidad
lo invalida.** `_pausar_por_no_disponibles` pone `wizard["confirmado"] =
False` como primera línea, antes de decidir nada más -- si no lo hiciera,
el bug original de esta sección se repite: el usuario confirma, algo del
carrito ya no está disponible, elige "quitar", y como `confirmado` seguía
en `True` el wizard saltaba directo a crear la reserva sin volver a
mostrar el resumen (`_siguiente_paso_wizard` ve `confirmado=True` y
devuelve `"listo"`). Con `confirmado` en `False`, el paso "quitar" del
resumen vuelve a pasar por `confirmar`, así que el usuario siempre ve el
carrito real (ya sin lo que se quitó) antes de que se escriba nada en
`reservas`. Lo mismo pasa si el carrito queda vacío al quitar: el wizard
vuelve a `elegir_activo` en vez de "terminar" con nada reservado.

### Alternativas a un activo no disponible

Cuando el paso `resolver_disponibilidad` quita del carrito un activo que
el usuario pidió directamente (una raíz, `item.get("por") is None` -- no
un accesorio ni el contenido de un lugar), el bot le ofrece reemplazos de
la misma clase que sí están libres en ese horario, en vez de dejarlo
buscar otro desde el menú general.

- **"Misma clase" es `tipo` + `categoria` + `subcategoria`, no solo
  `subcategoria`.** Un Micrófono Rode NTG3 (subcategoria "microfono",
  categoria "grabación audiovisual") y un Shure SM7B (subcategoria
  "microfono", categoria "producción de audio") comparten subcategoría
  pero no son alternativas entre sí -- uno es para cámara, el otro para
  cabina. Incluir `tipo` en la clase le da a un ESPACIO (`categoria`/
  `subcategoria` en NULL) otros espacios como alternativa, sin necesitar
  un caso aparte para `tipo='espacio'`.
- **Solo raíces, nunca accesorios ni contenidos de un lugar.** Un lente
  Sony no le sirve a un cuerpo Canon, y ofrecer un reemplazo de un
  accesorio suelto no tendría con qué encajar -- si el reemplazo de la
  raíz trae sus propios accesorios por defecto, esos se resuelven igual
  que con cualquier activo nuevo (`confirmar_accesorios`).
- **Solo se revisa el activo principal, no sus accesorios, al armar la
  lista de alternativas** (`_alternativas_disponibles`): si el reemplazo
  elegido trae accesorios distintos que resultan ocupados, eso lo atrapa
  la próxima pausa de disponibilidad (al confirmar) -- la garantía real
  contra choques sigue siendo el `EXCLUDE` de Postgres, esto solo evita
  ofrecer algo obviamente ocupado.
- **Ciclo de vida de `wizard["alternativas_para"]`** (lista de nombres de
  raíces pendientes de alternativa): la pone "quitar" con las raíces que
  acaba de sacar del carrito; se le quita una cuando el usuario elige una
  alternativa para ella o no se encontró ninguna; se vacía por completo al
  Confirmar, Agregar otro activo, Cambiar fecha, Cambiar hora, "Elegir
  otro tipo de activo" y al cancelar el wizard; se conserva al usar
  "Quitar un activo de la reserva" y al volver al resumen sin elegir
  ninguna alternativa. Toda lectura usa `wizard.get("alternativas_para")
  or []` porque una sesión de Redis de antes de este campo no lo trae.
- **Carrito vacío tras quitar:** sin resumen al que volver, se ofrece la
  alternativa directamente (`_ofrecer_alternativas`) en vez de mostrar el
  menú de confirmar; si tampoco hay alternativa para esa raíz, se prueba
  con la siguiente raíz pendiente antes de rendirse y volver a
  `elegir_activo`.
- **Por qué el paso `elegir_alternativa` NO está en la lista de pasos
  donde "no" no cancela** (`gestionar_reserva`, mismo mecanismo que
  `confirmar_accesorios`/`confirmar_contenidos_espacio`): sus opciones son
  nombres de activos, "Volver al resumen" o "Elegir otro tipo de activo",
  ninguna empieza por "no". Si se agregara, un "no" del usuario dejaría de
  cancelar y caería en el `_interpretar_opcion` de ese paso, que matchea
  por substring de la etiqueta normalizada -- y "no" es substring de
  "canon" ("Cámara Canon EOS R6"), así que terminaría "eligiendo" esa
  cámara en vez de cancelar.

Solo cuando el usuario elige "Confirmar" (y el carrito pasa esa revisión
de disponibilidad sin nada pendiente) se llama a
`_crear_reserva_desde_wizard`, que le pasa el carrito final a
`crear_reservas_grupo`: crea un `ReservaGrupo` y una `Reserva` por cada
unidad **en una sola transacción**. Si la restricción `EXCLUDE` de
Postgres rechaza alguna justo al confirmar (condición de carrera), se
revierte el carrito completo en vez de dejar una reserva parcial. El
`ReservaGrupo` también guarda `tipo_uso` ('particular' o 'asignatura') y,
si aplica, el `nrc` de la asignatura -- primera pregunta del wizard, ver
"Uso: particular o asignatura" en `recursos_service.py`.

El id de ese `ReservaGrupo` es el **número de reserva**: lo que identifica
de forma única una reserva *hecha*, que puede cubrir varios activos (una
cámara y sus lentes comparten número). `reservas.grupo_id` es `NOT NULL`
desde la migración de `docs/SESION_2026-09-09.md` (sección 7), que además
le asignó grupo a las filas anteriores al carrito; así ninguna reserva
puede quedar sin número, ni las que crea el bot ni las de `seed.py`.

### Modalidad de la reserva: individual o en grupo

`ReservaGrupo.modalidad` ('individual' o 'grupo') es **independiente** de
`tipo_uso`: un grupo puede ser particular o de asignatura, y viceversa --
por eso se pregunta en un paso aparte, siempre, sin importar lo que se
haya respondido en `tipo_uso`/`nrc`. `tamano_grupo` **incluye** a quien
hace la reserva; sus integrantes van en `reserva_grupo_integrantes` (un
código uninorte por fila, columna `orden`, `orden=1` es siempre el
titular) -- no son una FK a `usuarios`, es texto libre: un integrante no
tiene por qué estar registrado en el bot.

El sub-flujo, solo cuando `modalidad='grupo'`:

```
tamano_grupo → integrante_id ×(N-1) → confirmar_grupo
```

`tamano_grupo` pide un número entre `TAMANO_GRUPO_MIN` (2) y
`TAMANO_GRUPO_MAX` (10). Al aceptarlo, el titular se prellena como
integrante 1 con su propio `codigo_uninorte` (ya lo tiene del registro,
`registro_completo` lo exige) y el wizard pide uno por uno los `N-1`
restantes (`integrante_id`, validados con
`registro_service.validar_codigo_uninorte` -- el mismo criterio que se le
exigió al titular al registrarse), rechazando un ID repetido dentro del
mismo grupo. Con la lista completa, `confirmar_grupo` muestra un resumen
tipo factura (`_lineas_grupo`) y deja corregir los integrantes desde cero,
o volver a "reservo solo" sin perder el resto del wizard. Desde el menú
final de confirmación, "Cambiar grupo / integrantes" reabre este mismo
sub-flujo. `crear_reservas_grupo` inserta los `ReservaGrupoIntegrante` en
la misma transacción que el `ReservaGrupo` y las reservas -- un choque de
disponibilidad al confirmar revierte también la lista de integrantes.

### Cancelar una reserva ya confirmada

Distinto de "cortar el wizard" de arriba (que aborta una reserva que
**todavía no se escribió** en la base): esto es el intent `cancelar`,
para anular una reserva que ya quedó `confirmada` (o `pendiente` de la
CPA; en la lista esas líneas dicen "pendiente de confirmación"). Vive en
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
  muestra al usuario, ya filtrada por dueño (y por estado vigente,
  `ESTADOS_QUE_OCUPAN`, y `fin` en el futuro), agrupada por `grupo_id` igual que se reservó (un
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

### Confirmación de la CPA: reservas parciales y pendientes

Un activo con `recursos.requiere_confirmacion` no se presta hasta que el
equipo de la CPA lo aprueba. El estado vive **por línea** (`reservas.estado`),
como el resto: `crear_reservas_grupo` crea esas líneas como `pendiente` y
las demás como `confirmada`. El estado de la reserva completa se deriva de
sus líneas:

| Líneas | Reserva | Factura del bot |
|---|---|---|
| todas `confirmada` | confirmada | *RESERVA CONFIRMADA* |
| `confirmada` + `pendiente` | **parcial** | *RESERVA PARCIAL* + qué quedó confirmado y qué no |
| todas `pendiente` | pendiente | *RESERVA PENDIENTE DE CONFIRMACIÓN* |

- **Una pendiente aparta el activo.** Si no, otro estudiante podría
  reservarlo mientras la CPA decide. Por eso el EXCLUDE
  `reservas_no_solapamiento` y todas las consultas de ocupación
  (`_reserva_solapada`, `_reserva_en_curso`, `_proxima_reserva`, las de
  cancelación) usan `ESTADOS_QUE_OCUPAN = ('confirmada', 'pendiente')`.
- **La CPA confirma desde el dashboard**, en la pestaña Reservas: el filtro
  "Por confirmar" junta las reservas con alguna línea pendiente (la más
  próxima primero) y en el modal de detalle cada activo pendiente tiene
  sus botones (ver el punto siguiente). "Confirmar todo" aparece solo si
  hay más de un activo pendiente.
  `POST /dashboard/api/reservas/grupo/<id>/confirmar` pasa las líneas
  pendientes a `confirmada`, hace commit y **después** le escribe al
  estudiante (`recursos_service.mensaje_reserva_confirmada`, con el número
  de reserva, que también va en la factura de cuando reservó, y la línea
  "Sale de Barranquilla: Sí" si aplica -- ver "Cláusula de seguro"). El envío
  queda en `auditoria_bot` con intent `confirmacion_cpa`. Si WhatsApp
  falla, la reserva queda confirmada igual y la respuesta trae
  `notificado: false`, para que el dashboard avise que hay que contactarlo
  por otro medio.
- **Cada activo se confirma o se deniega por separado**, en el mismo
  modal: `POST /dashboard/api/reservas/<id>/confirmar|denegar`. Denegar
  deja la línea `denegada`, que no está en `ESTADOS_QUE_OCUPAN`: el activo
  queda libre en ese horario, pero la fila se conserva para saber qué se
  negó. Cada decisión le manda al estudiante un WhatsApp
  (`mensaje_decision_cpa`, intent `confirmacion_cpa` o `denegacion_cpa`)
  que dice qué se decidió y cómo quedó el resto, para que se entienda solo
  aunque lleguen varios seguidos. Cuando ya no queda nada pendiente, en vez
  de eso va la factura completa de *RESERVA CONFIRMADA*, que aclara lo que
  no se aprobó; si se negó todo, el mensaje dice que la reserva queda
  anulada.
- **Editar no confirma en silencio.** El formulario de edición del modal
  deja el estado sin cambiar cuando la reserva tiene estados mixtos (antes
  lo aplanaba a uno solo al guardar cualquier otro cambio), y un activo que
  la CPA agrega desde el dashboard nace `confirmada` aunque la reserva siga
  pendiente: lo agregó ella misma. Poner "confirmada" a mano en el
  formulario sí confirma, pero sin avisarle al estudiante; el formulario lo
  advierte.
- Las reservas creadas antes de este cambio con activos que requieren
  confirmación quedan como estaban (`confirmada`): no se migraron.

### Menú de fallback (`menu_service.py`)

Cuando `clasificar_intent` no ubica el mensaje en ningún intent permitido
(en la práctica, siempre cae en `"otro"`), en vez de cortar la
conversación con un mensaje fijo se le ofrece al usuario un menú de tres
opciones -- "Reservar" / "Consultar" / "Cancelar" -- para no perder el
mensaje sin intentar ayudar.

Ojo con **qué llega hasta aquí**: desde que existe el intent `saludo`, los
saludos y las cortesías ya no caen en este menú, se contestan como
conversación (ver "Conversación normal" más arriba). A este menú solo llega
lo que de verdad no se entendió.

Vive en `menu_service.py`, un módulo puro (sin DB, sin
LLM, sin importar `recursos_service.py`) para no añadir superficie al
archivo de negocio más grande y crítico del bot; `webhook.py` es quien
decide a qué servicio despachar según la acción que devuelve.

Estado en sesión bajo `sesion["menu_fallback"]`:
`{"paso": "elegir_opcion" | "esperando_consulta", "intentos": int}`. Igual
que `reserva_wizard`/`cancelacion_wizard`, mientras esté activo la
respuesta del usuario se interpreta contra el paso del menú, no contra
clasificación de intent.

- **`elegir_opcion`**: `"1"`/palabras de reservar arrancan
  `recursos_service.gestionar_reserva` con el texto tal cual lo escribió
  el usuario (si ya menciona el activo, p. ej. "quiero reservar la sala de
  edición", el wizard salta esos pasos solo, igual que hace hoy con el
  intent `reservar` normal). `"2"`/palabras de preguntas piden qué activo
  consultar y pasan al paso `esperando_consulta`. `"3"`/palabras de
  cancelar arrancan `recursos_service.gestionar_cancelacion`. Una opción no
  reconocida reintenta el menú hasta `MAX_REINTENTOS` (2) veces; agotados
  los reintentos, se rinde con el mismo mensaje estático que antes
  bloqueaba directo, auditado igual que antes
  (`motivo_bloqueo="intent_no_permitido"`).
- **`esperando_consulta`**: cualquier texto (salvo una palabra de salida o
  de cancelar) se pasa tal cual a
  `recursos_service.evaluar_consulta_disponibilidad` -- función pura ya
  usada por el intent `consultar_disponibilidad` normal, así que no hay
  lógica de disponibilidad nueva, solo un enrutamiento distinto hacia ella.
  Es de un solo turno: siempre limpia el estado al responder, no puede
  quedar en bucle. Responder `"cancelar"` a "¿sobre qué equipo quieres
  saber?" no es nombrar un equipo: se enruta a la cancelación, no a una
  consulta que no encontraría nada.
- Palabras de salida (`"salir"`, `"cerrar"`, `"nada"`, `"ninguna"`,
  `"olvídalo"`) cortan el menú en cualquier paso. `"cancelar"` ya **no**
  está entre ellas: desde que existe la opción 3, dentro del menú esa
  palabra significa "quiero cancelar una reserva", que es justo lo que hace
  esa opción.

**Invariante importante:** `menu_service.procesar_respuesta` siempre
devuelve la sesión con `menu_fallback` en `None` antes de que `webhook.py`
se la pase a `gestionar_reserva` -- si no, como ese wizard hace
`{**sesion, "reserva_wizard": wizard}` en cada paso, `menu_fallback`
quedaría propagándose y el bloque de `webhook.py` que lo lee interceptaría
cada mensaje futuro del wizard de reservas. No agregar `"otro"` a
`ALLOWED_INTENTS` como atajo alternativo a este menú: eso enrutaría
cualquier mensaje fuera de alcance a `llm_client.generar_respuesta`,
eliminando el guardrail principal del bot.

**Auditoría:** mostrar el menú, un reintento, o salir del menú se registran
con `permitido=True` (ya no es un bloqueo, es ayuda ofrecida) e
`intent_detectado="menu_fallback"`; elegir una opción audita el intent real
elegido (`"reservar"` / `"consultar_disponibilidad"` / `"cancelar"`). Solo la rendición
final tras agotar reintentos conserva `permitido=False` y
`motivo_bloqueo="intent_no_permitido"`. Efecto esperado en el dashboard:
`total_bloqueadas_30d` (`dashboard.py`) baja, porque ya no se cuenta cada
mensaje inclasificable como bloqueado.

### Oferta de reserva tras una consulta (`menu_service.py`)

Cuando el bot responde una consulta de disponibilidad, no corta ahí la
conversación: le ofrece al usuario iniciar una reserva para no obligarlo a
escribir un mensaje nuevo desde cero. Pedido explícito del usuario: el
wizard tiene que arrancar **desde su primer paso** (el menú de
subcategorías de equipo + espacios), nunca precargado con el activo que se
acababa de consultar.

Se activa desde `webhook.py` en las dos rutas que responden disponibilidad
-- el intent `consultar_disponibilidad` normal y la acción `"consultar"`
del menú de fallback -- llamando a `menu_service.activar_oferta(sesion,
respuesta)`, que pega un texto corto al final de la respuesta ("¿Quieres
reservar algo? Responde *sí* y te ayudo 🙂") y marca
`sesion["oferta_reserva"] = {"activa": True}`.

Es de un solo turno, sin reintentos: el próximo mensaje se interpreta con
`menu_service.procesar_respuesta_oferta`, misma forma de retorno
`(accion, payload, nueva_sesion, auditoria)` que `procesar_respuesta`.

- **Acepta** ("sí", "1", "dale", "listo", "claro", "ok", "bueno",
  "quiero"): arranca `recursos_service.gestionar_reserva` con el propio
  texto del usuario (p. ej. "si"). Como ese texto no nombra ningún activo,
  `_iniciar_reserva` no resuelve ningún candidato y el wizard abre en su
  primer menú -- que es justo el efecto pedido, sin necesidad de ninguna
  lógica especial de "saltar al inicio".
- **Rechaza** ("no", "nada", "gracias", "después", "luego"): responde algo
  breve y cordial, sesión limpia.
- **Ninguna de las dos** (el usuario escribió otra cosa, p. ej. cambió de
  tema): `webhook.py` limpia la oferta pero **no retorna** -- deja que el
  mensaje siga su curso normal hacia clasificación de intent. Igual que en
  el menú de fallback, no tiene sentido dejar al usuario atrapado
  respondiendo "¿sí o no?" a algo que ya no preguntó.

**Misma invariante que el menú de fallback:** `procesar_respuesta_oferta`
siempre devuelve la sesión con `oferta_reserva` en `None` antes de
pasarla a `gestionar_reserva` -- si no, se propagaría en cada paso del
wizard (`{**sesion, "reserva_wizard": wizard}`) y el bloque de `webhook.py`
que lee `sesion.get(menu_service.CLAVE_OFERTA)` interceptaría cada mensaje
futuro del wizard durante los 7 días de TTL de la sesión.

**Auditoría:** ofrecer, aceptar o rechazar se registran con
`permitido=True` (no son bloqueos); aceptar audita el intent real
`"reservar"`.

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
"Reserva paso a paso" más arriba), `cancelacion_wizard` (mismo patrón
para cancelar una reserva ya confirmada -- ver "Cancelar una reserva ya
confirmada" más arriba) y `menu_fallback` (menú de "Reservas"/"Preguntas"
cuando no se reconoce el intent -- ver "Menú de fallback" más arriba).

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
- **Un evento agrupa las líneas de un mismo `grupo_id` y horario.** Una
  reserva hecha para cinco activos llena el día con un solo evento. El
  `grupo_id` evita mezclar dos reservas independientes del mismo usuario a
  la misma hora; el filtrado (tipo, recurso, usuario y estado) ocurre
  **antes** de agrupar. El filtro de usuario usa `usuarios.codigo_uninorte`.
- **El modal permite editar los activos reservados.** La lista principal
  muestra solo las líneas de ese grupo, agrupando visualmente los padres y
  sus hijos con indentación. El botón `+` abre un árbol con casillas para
  seleccionar varios activos nuevos; cada línea reservada se puede quitar,
  pero la reserva debe conservar al menos un activo.
- **La edición del grupo es atómica.**
  `PATCH /dashboard/api/reservas/grupo/<grupo_id>` puede cambiar horario,
  estado, conjunto de `recurso_ids` y los datos del grupo (`tipo_uso`,
  `modalidad`, `sale_de_barranquilla`) en una sola transacción. Las líneas
  eliminadas, agregadas o modificadas se aplican todas o ninguna. El
  solapamiento no se re-valida en Python: lo garantiza el `EXCLUDE` de
  Postgres descrito más arriba.
- **La eliminación completa requiere confirmación.** El botón del modal
  abre un segundo popup, no elimina directamente. Si se confirma, se borran
  todas las líneas del grupo y el `ReservaGrupo` mediante
  `DELETE /dashboard/api/reservas/grupo/<grupo_id>`; el toast solo informa
  el resultado posterior.

## Tablas responsivas del dashboard: lista en tarjetas + columna que absorbe el espacio

Las cuatro pestañas con tabla (Reservas, Usuarios, Sesiones, Métricas)
comparten el mismo patrón, nacido en `ReservasPanel.tsx` y extendido a las
demás en `docs/SESION_2026-09-14.md`. Antes de tocar cualquiera de estas
tablas, o de agregar una nueva, conviene seguir el mismo esquema en vez de
inventar uno distinto:

- **`@container` en la raíz del panel**, no `@media`: así se adapta al
  espacio real disponible (sidebar retraído/expandido, panel dentro de una
  tarjeta como "Por usuario" en Sesiones), no al ancho de la ventana.
- **Lista en tarjetas por debajo de `@2xl`, tabla desde ahí.** Dos
  componentes (`ListaX` / `TablaX`), alternados con `@2xl:hidden` /
  `hidden @2xl:block`. La lista no necesita columnas: cada tarjeta apila
  el dato principal, un subtexto con lo que en la tabla sería una columna
  aparte, y el estado/badge.
- **Columnas menos críticas se ocultan progresivamente**
  (`hidden @Nxl:table-cell`) y su contenido baja como subtexto de la
  primera columna mientras están ocultas (`@Nxl:hidden` en un `<p>`
  adentro de esa celda) -- nunca desaparece del todo, solo cambia de
  lugar.
- **Una sola columna absorbe el espacio sobrante** (`w-full` en su
  `<th>` + `whitespace-normal` en su celda), la de contenido más
  largo/variable de esa tabla (`Activos` en Reservas, `Nombre` en
  Usuarios, `Recurso` en Sesiones, `Motivo bloqueo` en Métricas). Sin
  esto, como `Table` (`components/ui/table.tsx`) fija `width: 100%` en el
  `<table>`, el layout automático reparte el sobrante por igual entre
  columnas cortas (fechas, "sí"/"no", badges) y cada celda queda con
  relleno en blanco en vez de que el espacio se note en un solo lugar.
  **Cuidado:** si otra columna de esa misma tabla también tiene
  `whitespace-normal` sin una condición de ancho (por ejemplo para
  envolver un subtexto en pantallas angostas), hay que apagarlo desde el
  mismo breakpoint en que la columna absorbente aparece
  (`whitespace-normal @Nxl:whitespace-nowrap`) -- si no, esa otra columna
  se encoge a su palabra más larga y el texto se parte en varias líneas
  (pasó con "Usuario" en Sesiones al agregarle `w-full` a "Recurso", ver
  `docs/SESION_2026-09-14.md` sección 4).
- **Desde `@6xl` sube el tamaño de letra y el padding de fila**
  (`@6xl:text-base`, `@6xl:py-3`): en un monitor muy ancho hay espacio de
  sobra para leer más cómodo, no solo para que la tabla se vea ancha.

Esto solo tiene margen para respirar porque el `<main>` de
`App.tsx` (el que envuelve las cuatro pestañas) dejó de estar fijo en
`max-w-6xl`: desde 1800 px de viewport ya no tiene límite
(`min-[1800px]:max-w-none`, con `max-w-7xl` de paso intermedio entre 1536
y 1799 px). Por debajo de 1536 px el comportamiento no cambió. Sin este
cambio, ninguna de las columnas-absorbentes de arriba tiene sobrante que
absorber.

## Analítica en tiempo real

Los endpoints en `app/routes/analytics.py` consultan directamente la
tabla `reservas`. Para volumen bajo (el caso típico de una CPA
universitaria) esto es suficiente sin necesidad de un pipeline de datos
aparte. Si más adelante el volumen crece, considera:
- Vistas materializadas en Postgres para los agregados más pedidos.
- Un job periódico que precalcule métricas en vez de calcularlas on-demand.
