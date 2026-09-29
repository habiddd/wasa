# Arquitectura

## Flujo de un mensaje entrante

1. Usuario escribe por WhatsApp al número de la LACS.
2. `baileys-bot` (servicio Node.js sobre `@whiskeysockets/baileys`, WhatsApp
   Web no oficial) recibe el mensaje vía `messages.upsert` y lo reenvía a
   `POST /webhook/whatsapp` en Flask, con el `remoteJid` completo tal cual
   lo entrega Baileys (ver nota sobre JIDs `@lid` más abajo).
3. Flask, en orden (ver `routes/webhook.py:recibir_webhook`):
   - Aplica **rate limiting** por número (`guardrails.excede_limite_mensajes`).
   - Aplica **filtrado de entrada** contra mensajes sospechosos/inyección
     de prompt (`guardrails.mensaje_es_sospechoso`). La comparación se
     hace sobre una forma *compactada* del mensaje -- sin tildes, sin
     caracteres invisibles y sin nada que no sea letra o dígito -- para
     que meter un espacio o un guion de más no evada el filtro; ver
     `docs/SEGURIDAD.md`.
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
aviso de que la LACS confirmó su reserva, ver "Confirmación de la LACS"), el
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
LACS. Eso sí es un `CHECK` sobre la propia fila
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
  LACS) y se guarda en UTC (columnas `TIMESTAMPTZ`).
- **La garantía real contra choques de horario es de Postgres, no de la
  app**: `postgres/init/02-schema.sql` agrega la extensión `btree_gist` y
  una restricción `EXCLUDE USING gist (recurso_id WITH =, tstzrange(inicio,
  fin) WITH &&) WHERE (estado IN ('confirmada', 'pendiente'))` sobre
  `reservas`. Postgres rechaza cualquier INSERT/UPDATE que se solape con una
  reserva confirmada o pendiente de confirmación del mismo recurso (la
  pendiente también aparta el activo mientras la LACS decide, ver
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
  → hora_inicio → hora_fin → nota_preguntar → [nota_texto] → confirmar → listo

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
Barranquilla. Si la respuesta es sí, la LACS tiene que tramitar un seguro y
la reserva no puede empezar antes del `DIAS_MINIMOS_TRAMITE_SEGURO`-ésimo
(5) día **hábil** (lunes a viernes, sin festivos de Colombia -- paquete
`holidays`, `holidays.country_holidays("CO")`) después de hoy, hora de la
LACS (`ZONA_HORARIA_LACS`); hoy no cuenta como día hábil transcurrido. Por
ejemplo: viernes 18/09/2026 → viernes 25/09/2026 (fin de semana no cuenta);
miércoles 07/10/2026 → jueves 15/10/2026 (salta el lunes 12/10, festivo).
La regla no distingue de qué activo se trate.

Este sí es un seguro de verdad (un trámite de la LACS antes de que los
activos salgan de la ciudad), y no tiene nada que ver con
`recursos.requiere_confirmacion`: esa columna se llamaba `requiere_seguro`
y se renombró justamente porque nunca fue un seguro (ver
`docs/SESION_2026-09-10.md`). Un activo puede necesitar confirmación de la
LACS, seguro por salir de Barranquilla, las dos cosas o ninguna.

- **Orden:** el paso "sale_de_barranquilla" va justo antes de "fecha"
  (`_siguiente_paso_wizard`), ya con el carrito resuelto (después de
  `confirmar_accesorios`/`confirmar_contenidos_espacio` si aplican) -- así
  el mensaje de "fecha" puede mostrar de una vez la fecha mínima si hace
  falta.
- **`fecha_minima_sale_de_barranquilla(hoy=None)`** (pública, en
  `recursos_service.py`) es la única fuente de la fecha mínima -- toma
  `hoy` como parámetro para poder probarla con distintas fechas; con
  `None` usa el día de hoy en la LACS. Se apoya en `sumar_dias_habiles`
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
- **El dashboard solo advierte:** desde `ReservaDetalle.tsx` la LACS puede
  editar la respuesta y el inicio aunque quede antes del plazo del seguro
  -- se le avisa (mismo estilo del aviso de "por confirmar"), pero no se
  bloquea el guardado; la decisión final es suya.

### Nota de la reserva

Texto libre y opcional, dato del grupo completo (no de cada activo dentro
de él), como `tipo_uso`/`modalidad`/`sale_de_barranquilla`. Se guarda en
`reserva_grupos.nota` (`VARCHAR(300)`, `postgres/init/02-schema.sql`); NULL
= sin nota. El límite de caracteres vive en `recursos_service.NOTA_MAX_CARACTERES`
(bot) y `calendario-utils.NOTA_MAX_CARACTERES` (dashboard), ambos en 300
para que coincidan con la columna.

- **Orden en el wizard:** va al final, después de `hora_fin` y antes de
  `confirmar` (`nota_preguntar → [nota_texto]`, ver
  `_siguiente_paso_wizard`) -- a diferencia de `sale_de_barranquilla`, que
  se pregunta antes de la fecha porque mueve la fecha mínima, la nota no
  condiciona ninguna otra validación, así que no hay apuro en pedirla
  antes.
- **Dos pasos:** "nota_preguntar" (sí/no, con `_interpretar_si_no` por el
  mismo motivo que `sale_de_barranquilla`: comparar por substring de la
  etiqueta daría falsos positivos con "no") y, solo si el usuario dijo que
  sí, "nota_texto" (el texto libre en sí, validado contra
  `NOTA_MAX_CARACTERES` y contra que no quede vacío tras `strip()`).
- **`nota_texto` está exenta de las palabras de cancelación:** a
  diferencia de "nota_preguntar" (que solo se agrega a la lista de pasos
  donde "no" no cancela, igual que `sale_de_barranquilla`), en "nota_texto"
  ni siquiera "cancelar"/"cerrar" cortan el wizard -- el usuario está
  escribiendo texto libre para la nota en sí, y una nota que diga "cancelar
  si llueve" no puede matar la reserva. `gestionar_reserva` vacía del todo
  `palabras_cancelar` para ese paso; quien quiera abortar de verdad usa la
  opción "Cancelar" del resumen, dos pasos después.
- **El guardrail de longitud se afloja en "nota_texto":**
  `guardrails.mensaje_es_sospechoso` corta todo mensaje de más de
  `LONGITUD_MAXIMA_MENSAJE` (1000) antes de que llegue al LLM, y respondía
  "No puedo procesar ese mensaje" a una nota larga -- sin decirle al usuario
  que el problema era el largo ni cuál es el máximo. Ahora `routes/webhook.py`
  consulta `recursos_service.espera_texto_libre(sesion)` (una lectura de
  Redis sin efectos, antes de crear la fila de Usuario) y, si el wizard está
  parado en "nota_texto", le pasa `texto_libre=True`: el tope sube a
  `LONGITUD_MAXIMA_TEXTO_LIBRE` (10 000) y la nota llega al wizard, que
  responde `_MENSAJE_NOTA_LARGA` con el máximo concreto y le pide
  reescribirla. Los patrones de inyección se revisan igual en los dos casos,
  y el paso "nota_texto" no usa el LLM.
- **Dónde se ve:** en el resumen/factura del wizard (`_mensaje_resumen`,
  `_crear_reserva_desde_wizard`) y en `mensaje_reserva_confirmada`, y en el
  detalle del dashboard (`ReservaDetalle.tsx`, componente `Detalle`) --
  nunca en las vistas compactas (`VistaMes.tsx`, `VistaTiempo.tsx`,
  `ReservasPanel.tsx`).
- **El dashboard también la crea y edita:** el POST de creación de reservas
  (`api_crear_reserva`) y el PATCH de grupo (`api_actualizar_reserva_grupo`)
  aceptan `nota` como campo opcional, validado con el mismo helper
  (`_validar_nota` en `routes/dashboard.py`). En el PATCH, desmarcar el
  checkbox de "Dejar una nota" manda `null` y borra la nota existente.

### Lista negra de fechas en el wizard

El wizard también rechaza reservas cuyo día de entrega o de devolución cae
en la lista negra de la pestaña "Excepciones" (ver más abajo, sección
"Excepciones: lista negra de fechas"). Los enganches, todos en
`recursos_service.py`:

- **`_error_fecha_bloqueada(wizard, fecha_inicio, fecha_fin=None)`**, justo
  después de `_error_plazo_seguro`: revisa únicamente el día de inicio y el
  de fin (no los días intermedios de un préstamo de varios días) con
  `fechas_bloqueadas_service.motivo_bloqueo`, y arma el mensaje de rechazo
  con la siguiente fecha disponible (`siguiente_fecha_habilitada`),
  respetando el plazo del seguro si el wizard tiene
  `sale_de_barranquilla=True`.
- **Validación central:** al tope del `while` de `_avanzar_wizard`, justo
  después del bloque que revalida el plazo del seguro -- mismo criterio: si
  la fecha puesta (recién escrita o bloqueada desde el dashboard mientras
  la sesión seguía abierta) incumple, se borra y se vuelve a pedir.
- **Rechazo temprano en fechas de lenguaje natural:** igual que con el
  plazo del seguro, en los tres puntos de `_continuar_reserva` donde se
  interpreta una fecha natural (el rango, la fecha única y el reingreso en
  "confirmar_fecha_interpretada") -- así el usuario no llega a ver el "¿Es
  correcto?" de una fecha que de todos modos se va a rechazar.
- **Red de seguridad final:** `_crear_reserva_desde_wizard` repite el
  chequeo justo antes de escribir en la base (la LACS pudo bloquear el día
  entre el resumen y "Confirmar"); si falla, vuelve a llamar
  `_avanzar_wizard` para que la validación central borre la fecha y avise,
  igual que con el plazo del seguro.
- **Mensaje del paso "fecha":** `fechas_bloqueadas_service.texto_reglas()`
  ("No se reciben reservas los sábados, domingos ni festivos") se muestra
  justo donde el usuario va a escribir la fecha, debajo de la línea de
  fecha mínima del seguro si aplica.
- Los días intermedios de un préstamo largo sí pueden caer en domingo o
  festivo (un préstamo viernes → lunes es válido); el plazo del seguro
  (`es_dia_habil`) no cambia con esto.

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
"Confirmación de la LACS" más abajo). Los menús numerados *seleccionables* (`OPCIONES_CONFIRMAR`,
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
un caso de uso de la LACS.

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
LACS; en la lista esas líneas dicen "pendiente de confirmación"). Vive en
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

### Confirmación de la LACS: reservas parciales y pendientes

Un activo con `recursos.requiere_confirmacion` no se presta hasta que el
equipo de la LACS lo aprueba. El estado vive **por línea** (`reservas.estado`),
como el resto: `crear_reservas_grupo` crea esas líneas como `pendiente` y
las demás como `confirmada`. El estado de la reserva completa se deriva de
sus líneas:

| Líneas | Reserva | Factura del bot |
|---|---|---|
| todas `confirmada` | confirmada | *RESERVA CONFIRMADA* |
| `confirmada` + `pendiente` | **parcial** | *RESERVA PARCIAL* + qué quedó confirmado y qué no |
| todas `pendiente` | pendiente | *RESERVA PENDIENTE DE CONFIRMACIÓN* |

- **Una pendiente aparta el activo.** Si no, otro estudiante podría
  reservarlo mientras la LACS decide. Por eso el EXCLUDE
  `reservas_no_solapamiento` y todas las consultas de ocupación
  (`_reserva_solapada`, `_reserva_en_curso`, `_proxima_reserva`, las de
  cancelación) usan `ESTADOS_QUE_OCUPAN = ('confirmada', 'pendiente')`.
- **La LACS confirma desde el dashboard**, en la pestaña Reservas: un
  desplegable de estado (con el conteo de cada uno) reemplaza a las
  pestañas de antes, y la opción "Por confirmar" junta las reservas con
  alguna línea pendiente (la más próxima primero); un checkbox aparte
  "Necesita seguro" filtra (con AND sobre el estado elegido) las que salen
  de Barranquilla, la misma condición del badge "Sale de Barranquilla ·
  seguro". En el modal de detalle cada activo pendiente tiene sus botones
  (ver el punto siguiente). "Confirmar todo" aparece solo si hay más de un
  activo pendiente.
  `POST /dashboard/api/reservas/grupo/<id>/confirmar` pasa las líneas
  pendientes a `confirmada`, hace commit y **después** le escribe al
  estudiante (`recursos_service.mensaje_reserva_confirmada`, con el número
  de reserva, que también va en la factura de cuando reservó, y la línea
  "Sale de Barranquilla: Sí" si aplica -- ver "Cláusula de seguro"). El envío
  queda en `auditoria_bot` con intent `confirmacion_lacs`. Si WhatsApp
  falla, la reserva queda confirmada igual y la respuesta trae
  `notificado: false`, para que el dashboard avise que hay que contactarlo
  por otro medio.
- **Cada activo se confirma o se deniega por separado**, en el mismo
  modal: `POST /dashboard/api/reservas/<id>/confirmar|denegar`. Denegar
  deja la línea `denegada`, que no está en `ESTADOS_QUE_OCUPAN`: el activo
  queda libre en ese horario, pero la fila se conserva para saber qué se
  negó. Cada decisión le manda al estudiante un WhatsApp
  (`mensaje_decision_lacs`, intent `confirmacion_lacs` o `denegacion_lacs`)
  que dice qué se decidió y cómo quedó el resto, para que se entienda solo
  aunque lleguen varios seguidos. Cuando ya no queda nada pendiente, en vez
  de eso va la factura completa de *RESERVA CONFIRMADA*, que aclara lo que
  no se aprobó; si se negó todo, el mensaje dice que la reserva queda
  anulada.
- **Editar no confirma en silencio.** El formulario de edición del modal
  deja el estado sin cambiar cuando la reserva tiene estados mixtos (antes
  lo aplanaba a uno solo al guardar cualquier otro cambio), y un activo que
  la LACS agrega desde el dashboard nace `confirmada` aunque la reserva siga
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

El primer mensaje que recibe un número nuevo abre, además, con un aviso de
que la conversación queda guardada y monitoreada (`MENSAJE_AVISO_PRIVACIDAD`
en `registro_service.py`), antes incluso de la bienvenida. La marca de "ya
fue avisado" es una columna nueva, `usuarios.aviso_privacidad_en
TIMESTAMPTZ`, y **no** un valor en la sesión de Redis: un aviso de
tratamiento de datos tiene que salir exactamente una vez y dejar constancia
de cuándo se informó a esa persona, y ninguna de las dos cosas se puede
garantizar colgándolas de la sesión, que tiene TTL de 7 días y puede
perderse. Si eso pasara a mitad de registro, la bienvenida se repetiría
(como ya hace hoy) pero el aviso no, porque su condición mira Postgres, no
la sesión. Por el mismo motivo el aviso no se manda a los usuarios que ya
existían antes de este cambio: solo se emite desde el flujo de registro, y
un usuario ya registrado nunca vuelve a pasar por ahí.

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
  local de la LACS (`recursos_service.ZONA_HORARIA_LACS`); si el calendario
  agrupara por día con la zona del equipo, el mismo dashboard mostraría
  cosas distintas según quién lo abre. Todo pasa por `Intl.DateTimeFormat`
  con `timeZone` fijo en `calendario-utils.ts`, incluidos los
  `<input type="datetime-local">` del formulario de edición.
- **Un evento agrupa las líneas de un mismo `grupo_id` y horario.** Una
  reserva hecha para cinco activos llena el día con un solo evento. El
  `grupo_id` evita mezclar dos reservas independientes del mismo usuario a
  la misma hora; el filtrado (tipo, recurso, usuario y estado) ocurre
  **antes** de agrupar. El filtro de usuario usa `usuarios.codigo_uninorte`.
- **Los cuatro filtros son multiselección con búsqueda.** El componente
  `FiltroMultiple.tsx` es un popover de Radix con un buscador (no distingue
  tildes ni mayúsculas), casillas y los botones "Todos" y "Ninguno", que
  actúan solo sobre lo que muestra la búsqueda. No se agregó `cmdk` ni
  ninguna otra dependencia. Vacío no significa lo mismo en todos:
  - En **recursos y usuarios**, vacío = sin filtro. La lista de usuarios
    sale de las reservas del periodo cargado, así que un código marcado que
    no aparece en ese periodo se agrega igual a las opciones, para poder
    desmarcarlo.
  - En **tipo y estado** la selección es explícita: vacío = no se muestra
    nada. Tipo trae marcados Activos y Lugares. Estado trae todo menos
    canceladas y denegadas, y el botón dice "Sin canceladas ni denegadas".
  - "Retrasadas" es una casilla más: coincide con `reserva.retrasada`, no
    con `reserva.estado`.
  - Tipo y estado se guardan en `localStorage` como arrays JSON, con las
    claves `tipos-marcados` y `estados`; recursos y usuarios no se guardan.
    Las claves de la versión anterior, `tipo` y `estado`, se ignoran y no
    hay migración.
  - Al desmarcar un tipo se quitan los recursos marcados que no son de ese
    tipo.
- **El modal permite editar los activos reservados.** La lista principal
  muestra solo las líneas de ese grupo, agrupando visualmente los padres y
  sus hijos con indentación. El botón `+` abre un árbol con casillas para
  seleccionar varios activos nuevos; cada línea reservada se puede quitar,
  pero la reserva debe conservar al menos un activo.
- **La edición del grupo es atómica.**
  `PATCH /dashboard/api/reservas/grupo/<grupo_id>` puede cambiar horario,
  estado, conjunto de `recurso_ids` y los datos del grupo (`tipo_uso`,
  `modalidad`, `sale_de_barranquilla`, `nota`) en una sola transacción. Las líneas
  eliminadas, agregadas o modificadas se aplican todas o ninguna. El
  solapamiento no se re-valida en Python: lo garantiza el `EXCLUDE` de
  Postgres descrito más arriba.
- **La eliminación completa requiere confirmación.** El botón del modal
  abre un segundo popup, no elimina directamente. Si se confirma, se borran
  todas las líneas del grupo y el `ReservaGrupo` mediante
  `DELETE /dashboard/api/reservas/grupo/<grupo_id>`; el toast solo informa
  el resultado posterior.
- **Leyenda plegada, detalle en el tooltip.** Cada bloque sigue pintado
  con el color de su estado (`ESTILO_ESTADO.chip`), y al pasar el mouse
  muestra `Reserva #N · estado` más horario, activos y usuario
  (`calendario-utils.tooltipEvento`). El tooltip es el de shadcn, no el
  `title` nativo, porque se colorea con el mismo estado
  (`TooltipEvento.tsx`, tabla `COLOR_TOOLTIP`). Sobre la rejilla hay además
  una leyenda plegable, "Qué significa cada color" (`LeyendaEstados.tsx`, un
  `<details>` nativo cerrado por defecto): los nueve estados con los mismos
  chips del calendario, y en una segunda fila las marcas que no son estado
  propio (Parcial, Estados mixtos, Retrasada, Entrega tardía). Un estado
  nuevo tiene que agregarse en `COLOR_TOOLTIP`, en `ETIQUETA_ESTADO` y en el
  `ORDEN` de la leyenda.
- **Un solo vocabulario de estados.** Las etiquetas visibles salen de
  `calendario-utils.ETIQUETA_ESTADO` (`etiquetaEstado`), y los filtros que
  comparten Calendario y Reservas de `FILTROS_ESTADO_COMUNES`: cada panel
  solo antepone los suyos: Reservas, "Vigentes"/"Todas" y "Por confirmar";
  el Calendario, solo "Por confirmar", porque ahí los atajos son la
  selección por defecto y la casilla de cada estado. Así un
  estado no se llama distinto según la pestaña.
- **Una sola barra de desplazamiento.** En semana y día la rejilla de horas
  mide lo que queda de ventana debajo de ella (`VistaTiempo`, recalculado en
  `resize`, mínimo 240 px), no un `65vh` fijo. Así la página no scrollea
  además de la rejilla. Si cambia el padding inferior de `<main>` (`p-6`),
  hay que ajustar los 25 px que resta la medida.

### Crear reservas desde el dashboard

`POST /dashboard/api/reservas` (`api_crear_reserva`, `app/routes/dashboard.py`)
le permite a la LACS crear una reserva completa sin pasar por WhatsApp --
mismos campos que el wizard del bot (cliente, activos, uso/NRC,
modalidad/integrantes, si sale de Barranquilla, nota, horario), reusando las
validaciones de forma que ya tenía `api_actualizar_reserva_grupo`
(`_validar_tipo_uso`, `_validar_integrantes`, `_validar_nota`) y
`recursos_service` (`ids_ocupados_en_rango`, `advertencias_fechas`).

- **Bloqueos duros vs. avisos.** Activo inexistente o no `disponible`,
  fechas mal formadas o `fin <= inicio`, uso/NRC/modalidad/integrantes
  inválidos, y choque real de horario (`ids_ocupados_en_rango` antes de
  escribir, más el `EXCLUDE` de Postgres como red final) sí devuelven 400/409
  y no crean nada. En cambio, fecha bloqueada, plazo del seguro incumplido e
  inicio ya pasado solo van en `advertencias` de la respuesta (200/201) --
  mismo criterio que "el dashboard solo advierte" del calendario y de
  Excepciones: quien crea la reserva acá ya es la LACS.
- **Nace `confirmada` y con `canal_origen='dashboard'`.** `crear_reservas_grupo`
  gana los parámetros `canal_origen` (por defecto `"whatsapp"`, sin tocar al
  bot) y `aprobada_por_lacs` (por defecto `False`): con `aprobada_por_lacs=True`
  todas las líneas nacen `confirmada` sin importar `requiere_confirmacion` --
  no tiene sentido que la LACS se autoconfirme algo que acaba de crear ella
  misma. `canal_origen` no tiene CHECK en Postgres, así que `'dashboard'` es
  un valor válido sin migración.
- **Cliente existente o nuevo.** `usuario_id` reusa un `Usuario`; `cliente_nuevo`
  (`{nombre, telefono, codigo_uninorte?, cedula?}`) pasa por
  `usuarios_service.preparar_usuario_dashboard`, que valida con las mismas
  reglas del registro por WhatsApp (`registro_service.validar_campo`), reusa
  la fila si el teléfono ya existe (sin pisar datos que el cliente ya había
  dado) y solo hace `add`/`flush` -- nunca `commit` -- para que la
  transacción de la reserva decida si el alta se guarda o se revierte junto
  con el choque de disponibilidad. Con datos incompletos (sin ID Uninorte o
  cédula) el usuario queda con `registro_completo=False`, igual que uno a
  medio registrar por el bot; `GET /api/usuarios` lo deja afuera salvo que se
  pida con `?incluir_incompletos=1` (lo usa el selector de cliente del
  formulario).
- **Aviso por WhatsApp opcional.** El campo `notificar` (booleano, por
  defecto `False` en el formulario) decide si se le manda al cliente el
  mismo mensaje de confirmación que usa el bot
  (`recursos_service.mensaje_reserva_confirmada`); la respuesta trae
  `notificado` (`true`/`false`/`null` si no se pidió avisar) para que el
  dashboard muestre un aviso si el envío falló.
- **`GET /dashboard/api/reservas/ocupados?inicio&fin`** devuelve los IDs de
  recurso ocupados en ese rango (`ids_ocupados_en_rango`); el formulario lo
  usa para destildar en vivo un activo que se volvió ocupado mientras se
  completaban los demás campos.
- **Formulario** (`features/reservas/NuevaReservaDialog.tsx`, botón "Nueva
  reserva" de `ReservasPanel` y de `CalendarioPanel`): Dialog centrado con una sección por paso del
  bot separadas por `Separator` (para quién → uso/NRC → grupo/integrantes →
  espacios y activos → sale de Barranquilla → horario → aviso por WhatsApp).
  En la UI se habla de "usuario", no de "cliente" (el campo de la API sigue
  siendo `cliente_nuevo`).
  - `SelectorCliente.tsx`: "Usuario existente" (búsqueda local sin tildes por
    nombre, teléfono, ID Uninorte o cédula) o "Usuario nuevo".
  - `SelectorActivos.tsx` se usa dos veces, como listas desplegables
    (`Collapsible`, cerradas al abrir): **Espacios** (cada espacio con sus
    contenidos y los accesorios de estos) y **Activos** (todos los equipos,
    también los que están dentro de un espacio, con "· en <espacio>"). Las
    dos comparten un solo arreglo de ids, así que marcar en una marca en la
    otra y cada activo se envía una sola vez (además el formulario
    deduplica y el backend responde 400 ante ids repetidos). Marcar un nodo
    incluye sus descendientes disponibles; cualquier hijo se puede quitar
    aparte, y desmarcar un nodo quita lo que cuelga de él. Los accesorios
    de un equipo del espacio (que también tienen `espacio_id`) se muestran
    solo bajo su equipo. El conteo de seleccionados va solo en la nota bajo
    las dos listas.
- **Al crear desde el Calendario**, el panel mueve el ancla al día de inicio
  de la reserva nueva (puede caer fuera del periodo visible), recarga el
  rango y abre su detalle. Si los filtros del calendario (tipo, recurso,
  usuario, estado) la ocultan, la reserva se crea igual pero el modal no se
  abre.

### Filtro temporal del panel de Reservas

`GET /dashboard/api/reservas?ventana=24h|7d|30d|1a|todas` (`api_reservas`,
`app/routes/dashboard.py`) añade un tercer modo al endpoint de reservas, para
el selector de periodo del panel de Reservas (`ReservasPanel.tsx`,
`useReservasVentana`).

- **El recorte es solo hacia atrás.** `WHERE fin > now() - intervalo`, sin
  cota superior: una reserva futura aparece siempre, en cualquier ventana. El
  panel sirve sobre todo para vigilar lo que viene -- si "últimas 24 h"
  escondiera lo de mañana, el filtro sería inútil. Por eso la ventana no es un
  rango cerrado como el `desde/hasta` del calendario, que sí tiene ambas
  cotas.
- **Se pide al servidor, no se filtra en cliente sobre el mismo payload.**
  Medido en el contenedor: `/api/reservas` completo son 87 filas, 49,9 KB,
  9,7 ms, de los cuales el SQL son solo 0,06 ms -- el costo dominante es
  `_serializar_reserva` en Python, y ese costo se paga cada 30 s por cada
  pestaña del dashboard abierta. Pedir solo la ventana visible reduce a la vez
  la serialización en Flask y el `JSON.parse` + reagrupado en el navegador.
- **Estrechar la ventana no pide nada; ensanchar sí.** `useReservasVentana`
  guarda la última respuesta junto con la ventana con la que se pidió. Los
  datos de una ventana más estrecha que la cacheada son un subconjunto de la
  que ya está en memoria, así que se recortan en cliente al instante (misma
  regla `fin > ahora - delta` que el backend). Al ensanchar, sigue mostrando
  lo cacheado mientras llega la respuesta nueva (`refrescando`), sin pantalla
  en blanco ni skeleton de cero.
- **Polling adaptativo: 30 s para 24h/7d/30d, 120 s para 1a/todas.** El
  histórico viejo es inmutable; refrescarlo cada 30 s es gasto puro.
- **`?ventana=` devuelve un objeto (`{reservas, truncado, total, ventana}`),
  no una lista.** Los otros dos modos del mismo endpoint -- sin parámetros
  (pestaña Sesiones, `fetchSesiones`) y `?desde/?hasta` (calendario,
  `fetchReservasRango`) -- siguen devolviendo una lista tal cual, para no
  romper a esos dos consumidores. `ventana` y `desde/hasta` son mutuamente
  excluyentes (400 si llegan juntos).
- **"Todas" tiene un tope de seguridad (`MAX_RESERVAS_VENTANA` = 5000), no un
  recorte esperado.** El endpoint sin parámetros ya cortaba en 500 en
  silencio; eso deja de ser aceptable cuando el filtro invita a pedir el
  histórico entero, así que `truncado` en la respuesta le avisa al panel
  (línea discreta bajo los filtros) en vez de mentir con una lista incompleta
  sin decirlo.
- **`idx_reservas_fin` (`postgres/init/02-schema.sql`)** existe para cuando la
  tabla de reservas crezca: hoy, con unas pocas decenas de filas, Postgres
  sigue prefiriendo un seq scan y el índice no cambia nada medible.
- **`&incluir_abiertas=1` agrega los préstamos que siguen afuera.** Con él, el
  corte pasa a `fin > now() - intervalo OR estado IN ('entregada',
  'no_devuelta')`: un equipo que no ha vuelto importa aunque su `fin` quede
  antes de la ventana. Solo lo usa la vista Hoy
  (`useReservasVentana("30d", true)`); el recorte en cliente al estrechar la
  ventana (`recortarAVentana`) respeta la misma regla.
- **Buscador en cliente.** Sobre la ventana ya cargada, el panel filtra por
  estudiante, teléfono, código, número de reserva, activo o placa, sin
  distinguir tildes ni mayúsculas (`normalizar`: NFD sin diacríticos, en
  minúsculas). No pide nada al servidor; si no encuentra nada, sugiere
  limpiar la búsqueda o ampliar el periodo, porque lo buscado puede estar
  fuera de la ventana.

### Vista Hoy (`features/hoy/HoyPanel.tsx`)

Pestaña de inicio del dashboard (la primera del menú, y la que abre por
defecto): qué tiene que hacer ahora la persona del mostrador. No trae datos
propios: es la ventana de 30 días del panel de Reservas, con
`incluir_abiertas`, agrupada en eventos (`agruparEnEventos`) y repartida en
cuatro bloques, en este orden en pantalla:

1. **Por entregar hoy**: tiene líneas `confirmada` y empieza hoy.
2. **Por recibir hoy**: tiene líneas `entregada` y termina hoy.
3. **Por confirmar**: solicitud pendiente cuyo horario no ha pasado.
4. **Retrasados y no devueltos**, al final: `entregada` ya vencida
   (`evento.retrasada`) o con alguna línea `no_devuelta` cuyo activo **sigue**
   en `no_devuelto`.

- **El orden de pantalla no es el orden de clasificación.** `bloqueDe` revisa
  primero los atrasados y un evento cae en el primer bloque que le
  corresponde, así nunca aparece dos veces ni un vencido se cuela en "Por
  recibir". `BLOQUES` solo decide cómo se pinta.
- **Un no devuelto sale de Hoy cuando su activo sale de `no_devuelto`.** Que
  la LACS lo pase a otro estado desde Activos (con el motivo que pide
  `MotivoNoDevueltoDialog`) significa que apareció o que se dio de baja;
  dejarlo en la lista sería ruido. La reserva sigue `no_devuelta` para la
  historia; solo deja de mostrarse. El estado del activo sale de
  `useActivosContext()` (ya cargado para el resto del dashboard), no del
  payload de reservas, así que el backend no cambió.
- **Con varios activos, basta uno perdido.** El evento sigue en el bloque
  mientras alguna de sus líneas `no_devuelta` apunte a un activo aún en
  `no_devuelto`; el badge "No devuelta" sigue la misma regla.
- **Activo desconocido cuenta como perdido.** Mientras carga el catálogo, el
  mapa de estados está vacío: tratar eso como resuelto haría que las filas
  desaparecieran y volvieran al cargar.
- **Límites conocidos.** Las reservas se refrescan cada 30 s, pero los
  activos no: si otro equipo cambia el estado del activo, en esta pantalla la
  fila se va al volver a abrir Hoy. Y si un activo aparece, se presta y se
  vuelve a perder, la reserva vieja `no_devuelta` reaparecería; resolverlo
  bien requiere cruzar con `recurso_estado_historial.reserva_id` en el
  servidor.
- **Cada fila lleva su acción.** El botón del acta (`BotonActaPdf principal`)
  abre directamente la firma del momento que toca: entrega o devolución.

### Acta de entrega y devolución (PDF)

`GET /dashboard/api/reservas/grupo/<grupo_id>/pdf` (`api_acta_reserva_grupo`,
`app/acta_pdf.py`) devuelve el acta que se firma al retirar y al regresar los
activos de una reserva. Si la reserva ya tiene actas guardadas sirve **la
última** con `send_file`, sin volver a pasar por reportlab; si todavía no hay
ninguna, la genera en el momento. `?base=1` fuerza siempre el acta recién
generada. Servir el archivo guardado no es solo correcto (es el documento que
se firmó, no una reconstrucción) sino además más barato que lo que había
antes, que regeneraba el PDF en cada apertura del modal.

- **Se genera en Flask, no en el navegador.** El acta es un documento
  administrativo que se firma en papel y se archiva: generarla en el
  backend con reportlab la saca de la misma fuente de verdad que ya sirve
  `/api/reservas` (mismos objetos `Reserva`/`ReservaGrupo`, sin duplicar
  formateo en JS) y deja el resultado versionado con el código del repo, en
  vez de depender de qué build del bundle tenga cacheado el navegador de
  quien la imprime. `acta_pdf.generar_acta` no importa nada de Flask (recibe
  objetos del modelo y devuelve bytes): la ruta solo resuelve el `grupo_id`
  con `_reservas_del_grupo` y envuelve el PDF en la respuesta HTTP.
- **Un PDF por `grupo_id`, no por línea de activo.** Es la reserva completa
  la que se entrega y se devuelve en una sola sesión (aunque el carrito
  tenga varios equipos), así que el acta junta todas sus líneas en una
  tabla y un solo bloque de firmas al final. `grupo_id` inexistente
  responde 404 con el mismo formato de error que el resto de rutas de
  grupo, en vez de un 500 por lista vacía.
- **Rejilla de firmas 2x2 que no se parte entre páginas.** ENTREGA arriba,
  DEVOLUCIÓN abajo; Usuario / Administrador (LACS) en columnas. Es una sola
  `Table` de reportlab envuelta en `KeepTogether`, para que si no alcanza el
  espacio en la página actual, salte completa a la siguiente en vez de
  partir un cuadro de firma entre dos hojas.
- **La cédula del titular va impresa, no escrita a mano.** La primera versión
  la dejaba en blanco a propósito, con el argumento de que escribirla a mano
  junto a la firma es lo que le da valor probatorio al acta. Quien usa el
  dashboard pidió lo contrario después de probarlo: ahora se imprime desde
  `usuario.cedula` al generar el acta, así que también sale puesta en la copia
  que se imprime para firmar en papel. Si ese usuario no tiene cédula cargada
  (la columna es nullable), la línea vuelve a salir con sus guiones. La del
  administrador sí queda en blanco en el acta base: no está en la base de
  datos, la teclea quien firma (ver más abajo).
- **Se ve antes de descargarla.** El botón no baja el archivo de una: abre
  un modal (`features/reservas/BotonActaPdf.tsx`) con el PDF en un `iframe`
  y un botón "Descargar PDF", para revisar que los datos estén bien antes de
  imprimir. Está en tres sitios: la tabla y las tarjetas de "Reservas" (como
  icono) y el pie del detalle de la reserva (como botón "Ver acta"), que es
  el mismo `ReservaDetalle` en "Calendario" y en "Reservas" -- de ahí que el
  componente reciba `variante` y que `features/calendario` lo importe desde
  `features/reservas`. El blob se pide en cada apertura y se suelta al cerrar
  -- el object URL está atado al blob con un `useEffect`, no al clic, para
  que se revoque también si la fila se desmonta en una recarga del panel. El
  botón y el modal van envueltos en un `<span>` que corta la propagación:
  los eventos de un portal de React burbujean por el árbol de React, así que
  sin eso un clic dentro del modal llegaría a la fila y abriría además el
  detalle de la reserva.
- **Fechas en hora local de la LACS.** Usa el mismo
  `recursos_service.ZONA_HORARIA_LACS` que el resto del proyecto y
  normaliza a UTC antes de convertir los `datetime` que puedan llegar
  naive de la BD, igual que `_serializar_reserva`.
- **Nota del administrador (opcional).** `reserva_actas.nota` (VARCHAR(300),
  validada con `_validar_nota`, la misma función y el mismo límite que la
  nota de la reserva) guarda una observación libre por ACTA -- una para la
  entrega, otra para la devolución, no una sola para todo el histórico. El
  acta base reserva dos cajas, "Observaciones de la entrega (LACS)" y
  "Observaciones de la devolución (LACS)", con sus propias anclas
  `entrega_nota`/`devolucion_nota` (ver `acta_pdf._ALTO_NOTA`); quien firma
  en pantalla la escribe en `FirmarActaDialog` y `pdf-lib` la dibuja ahí
  mismo al componer el PDF (ver `actas-pdf.ts`, que además reemplaza por
  "?" cualquier carácter fuera de WinAnsi -- emoji o comilla tipográfica --
  porque Helvetica no lo dibuja). Un acta **subida** solo guarda la nota en
  la base: en papel queda escrita a mano dentro de la misma caja, no hay
  nada que estampar. Un acta **anterior** a este cambio no trae esas anclas
  -- la nota igual se guarda en el historial, solo que no se imprime (mismo
  criterio que la fecha/C.C. cuando falta su ancla).

### Ciclo de estados de una reserva

El acta dejó de ser un papel suelto: es **el comprobante que mueve la reserva
de un estado al siguiente**. Sin acta registrada no hay cambio de estado.

```
pendiente ──(la LACS aprueba)──────────────────> confirmada
confirmada ──(acta de ENTREGA)─────────────────> entregada
confirmada ──(NO_SHOW_MARGEN_MIN sin acta, automático)──> no_show
confirmada ──(manual, desde el selector)───────> no_show
no_show ──(acta de ENTREGA, dentro de la ventana)──> entregada [+ entrega_tardia]
entregada ──(acta de DEVOLUCIÓN, a tiempo)──────> completada
entregada ──(acta de DEVOLUCIÓN, tarde)─────────> devuelta_tarde
entregada + fin ya pasó ──> se MUESTRA "Retrasada" (derivado, no se guarda)

Por LÍNEA, según los equipos marcados al firmar:
confirmada/no_show ──(acta de ENTREGA, equipo NO marcado)──> cancelada
entregada ──(acta de DEVOLUCIÓN, equipo NO marcado)──> no_devuelta  [+ recurso.estado = no_devuelto]
```

- **El acta mueve solo los equipos marcados.** En el primer paso del
  diálogo de firma el administrador marca qué equipos se entregan o se
  devuelven, y el navegador manda sus ids (`Reserva.id`) en el campo
  `equipos` del `POST .../actas`. `actas_service.aplicar_transicion` cambia
  solo esas líneas. En la **entrega**, las no marcadas pasan a `cancelada`:
  no se las llevó, y así no quedan pidiendo otra acta de entrega. En la
  **devolución**, las no marcadas pasan a `no_devuelta` y su activo a
  `no_devuelto` (`actas_service.RECURSO_NO_DEVUELTO`): nada queda pendiente,
  la reserva se cierra igual que si todo hubiera vuelto. Una devolución sin
  nada marcado es válida (no devolvió ninguno). Un partial return posterior
  (el equipo aparece después de todo) no lo resuelve el bot: la LACS lo
  cambia a mano en Activos. Si llega un id que no está en los estados que ese
  momento puede mover, el servidor lo rechaza. Esa validación es lo que
  impide **devolver un equipo que nunca se entregó**, y no depende de la
  interfaz. Un acta **subida** no manda `equipos` y mueve todas las líneas,
  como antes.

- **`entregada` ocupa el activo.** Está en
  `recursos_service.ESTADOS_QUE_OCUPAN` junto a `confirmada` y `pendiente`, y
  también dentro del `EXCLUDE` de no solapamiento en
  `postgres/init/02-schema.sql`. Las dos listas tienen que decir lo mismo: si
  se cambia una hay que cambiar la otra, o la base dejaría pasar solapamientos
  que la aplicación cree estar impidiendo.
- **`retrasada` no es un estado, es un cálculo.** `actas_service.esta_retrasada`
  lo deriva de `estado == 'entregada' AND fin < ahora`, y
  `_serializar_reserva` lo expone como el campo booleano `retrasada`. Se
  descartó guardarlo en una columna justamente para no meter un cron ni un
  worker que recorriera las reservas vencidas: un estado persistido que
  depende del reloj se desincroniza en cuanto ese proceso falla, y aquí no
  hace falta. El panel lo pinta como un badge rojo **encima** del de
  `entregada`, no en lugar de él: el estado real sigue siendo "entregada" y el
  retraso es información añadida.
- **`devuelta_tarde` sí se guarda.** Es un estado final alternativo a
  `completada`, y se fija en el instante de la devolución comparando con `fin`
  (`actas_service.aplicar_transicion`). Se persiste porque la métrica del
  retraso tiene que sobrevivir a la devolución: una vez el equipo vuelve,
  `retrasada` deja de ser verdad por definición, y sin este estado el dato se
  perdería.
- **Los cuatro estados que exige acta están cerrados en la API.**
  `_ESTADOS_SOLO_CON_ACTA = {"entregada", "completada", "devuelta_tarde", "no_devuelta"}`:
  los tres endpoints de edición (`PATCH /reservas/<id>`,
  `PATCH /reservas/grupo/<id>` y `PATCH /reservas/bloque`) responden **409** si
  alguien intenta ponerlos a mano, y el selector de estado del detalle ya no
  los ofrece. Es una regla de negocio explícita, no una limitación técnica: la
  única puerta a esos estados es registrar el comprobante. La salida de
  emergencia, si hace falta, es subir un PDF firmado desde fuera.
- **Agregar un activo a una reserva ya entregada.** El `PATCH` de grupo con
  `recurso_ids` hace nacer la línea nueva como `confirmada`, no heredando el
  estado del resto: si no, un equipo añadido después quedaría marcado como
  entregado sin que nadie hubiera firmado nunca por él.
- **`no_show` ahora es automático (y sigue admitiendo el manual).** Ver la
  sección siguiente, "No-show automático y avisos de recogida", para el
  cómo y el porqué. Sigue disponible también desde el selector del
  dashboard para el caso en que la LACS quiera marcarlo (o revertirlo) a
  mano.
- **`entrega_tardia` es una bandera, no un estado nuevo.** Un grupo en
  `no_show` puede recibir su acta de ENTREGA mientras su ventana siga
  abierta (`fin` todavía no llegó): el estudiante se demoró más del margen
  de no-show pero sigue dentro de su horario, así que se le entrega igual.
  Vive en la columna `Reserva.entrega_tardia`, aparte de `estado`, para que
  la marca sobreviva al paso siguiente (la devolución, que sí cambia
  `estado`) -- si fuera un estado se perdería al devolver el equipo. El
  horario NO se extiende por la demora: `fin` no se toca, así que una
  reserva de 4 horas recogida con 1 hora de retraso deja 3 horas de uso
  real, no 4. Desde el 28/09/2026 también se marca cuando la entrega llega
  después de `inicio + NO_SHOW_MARGEN_MIN` con la línea todavía en
  `confirmada` (el barrido de no-show no alcanzó a correr, o la LACS lo
  revirtió a mano): antes esas recogidas tardías quedaban en `false`.

**Límite conocido:** una reserva `entregada` y retrasada deja de bloquear su
activo, porque el `EXCLUDE` trabaja sobre `tstzrange(inicio, fin)` y ese rango
ya pasó. El bot podría prestarle ese equipo a otra persona mientras sigue
físicamente fuera. El dashboard lo señala con el badge "Retrasada", pero nada
lo impide a nivel de datos.

### No-show automático y avisos de recogida y devolución (`app/avisos_reservas.py`, `app/ticker.py`)

Una reserva `confirmada` que nadie recoge no se queda así para siempre:
`NO_SHOW_MARGEN_MIN` minutos después de `inicio` (default 30) pasa sola a
`no_show`, con tres avisos de WhatsApp al estudiante por el camino:

- **`previo`**: `AVISO_PREVIO_MIN` minutos ANTES de `inicio` (default 30).
- **`hora`**: a la hora exacta de `inicio`.
- **`no_show`**: a los `NO_SHOW_MARGEN_MIN` minutos de `inicio` sin acta de
  entrega -- este es el que además cambia el estado. Le dice al estudiante
  que la reserva se liberó y que, si todavía está dentro de su horario,
  puede pasar por la LACS igual (ver `entrega_tardia` arriba).
- **`devolucion`** (aparte del ciclo, sobre reservas `entregada`):
  `AVISO_DEVOLUCION_MIN` minutos ANTES de `fin` (default 30) se le pide al
  estudiante que devuelva. No cambia estado; pasado `fin` ya no se manda, y el
  texto lleva los minutos que de verdad quedan. Respeta el mismo corte de
  frescura `AVISO_VENTANA_MIN`, pero sin crear filas `vencido`. Probado con
  `flask-app/verificar_aviso_devolucion.py`.

**Qué lo empuja: un ticker más un barrido perezoso, no un cron.** Se
descartó sumar APScheduler, Celery o un contenedor nuevo -- el volumen de
esta app no lo justifica. En cambio:

- **El ticker** (`app/ticker.py`): un `threading.Thread(daemon=True)` que se
  lanza en `create_app` y despierta cada `BARRIDO_INTERVALO_SEG` segundos
  (default 60) a correr `avisos_reservas.barrer()`. Duerme primero y barre
  después, para no pegarle a la base mientras Postgres todavía está
  levantando en el arranque del contenedor.
- **El barrido perezoso**: `GET /dashboard/api/reservas` (el listado que el
  dashboard consulta cada 30 s) también llama a `barrer()` de paso, como
  segunda vía -- útil, por ejemplo, justo después de levantar el contenedor
  y antes de que el ticker complete su primer sueño.

Ambos llaman exactamente a la misma función `barrer()`.

**Dos capas contra avisos duplicados.** `gunicorn` corre con `--workers 2`
(ver `flask-app/Dockerfile`), así que hay DOS procesos, cada uno con su
propio hilo ticker, más el barrido perezoso disparándose desde cualquier
request:

1. **`avisos_reservas.intentar_lock`**: un lock de Redis (`SET NX EX`) que
   hace que, en el caso normal, un solo proceso corra el barrido por
   intervalo. Es la OPTIMIZACIÓN -- ahorra trabajo repetido.
2. **El `UNIQUE (grupo_id, tipo)` de `reserva_avisos`** (ver
   `postgres/init/02-schema.sql`): la garantía REAL. Si el lock vence a
   mitad de una corrida larga, o Redis está caído (`intentar_lock` devuelve
   `False` y loguea, nunca lanza), dos workers pueden llegar a intentar el
   mismo aviso -- el segundo `INSERT` revienta con `IntegrityError`, ese
   worker hace `rollback()` y sigue con el resto del lote.

**Qué garantiza exactamente el `UNIQUE`, y qué no.** Garantiza que un aviso
se REGISTRA una sola vez. No garantiza del todo que el WhatsApp se MANDE una
sola vez: en `barrer()` el envío ocurre antes del `commit`, porque la fila
guarda el resultado del envío (`enviado`, `motivo='error_envio'`). Queda una
carrera estrecha -- los dos workers tendrían que pasar el lock a la vez, es
decir con Redis caído o con el lock vencido a mitad de corrida -- en la que
ambos mandarían el mensaje y solo uno insertaría la fila. El estudiante
recibiría el aviso dos veces; el estado y el histórico quedarían correctos
igual.

Se dejó así a propósito: invertir el orden (insertar primero, mandar
después) cerraría la carrera pero haría imposible registrar si Baileys
aceptó el envío, que es justo lo que hace depurable el camino
`motivo='error_envio'`. Entre "un estudiante puede recibir un recordatorio
repetido en un escenario de Redis caído" y "nunca sabemos si los avisos
están saliendo", se eligió lo primero. Si algún día hace falta cerrarla,
la forma es insertar la fila con `enviado=false` dentro de su propia
transacción, mandar, y hacer un `UPDATE` después.

**Corte de frescura (`AVISO_VENTANA_MIN`, default 120 min).** Un aviso solo
se ENVÍA si su momento ideal cayó dentro de los últimos `AVISO_VENTANA_MIN`
minutos. Existe porque el barrido tiene que poder correr después de que el
contenedor estuvo caído un rato sin mandar avisos falsos: un "te recuerdo
que recoges en 30 min" que sale dos horas tarde no es un aviso tardío, es
información incorrecta. Si el momento ya venció, la fila de
`reserva_avisos` se crea igual con `enviado=false, motivo='vencido'`, para
no reintentarlo nunca en el siguiente tick. El cambio de estado a
`no_show` SÍ se aplica aunque el aviso esté vencido: el estado es un hecho
(el activo se liberó), el mensaje es una cortesía con fecha de vencimiento.

**Reversión manual = aviso resuelto para siempre.** Como la fila
`(grupo_id, 'no_show')` en `reserva_avisos` queda para siempre, si la LACS
revierte a mano un `no_show` a `confirmada` desde el dashboard, el barrido
**no** lo vuelve a marcar ni a re-notificar: el `UNIQUE` se lee como "este
aviso ya se resolvió una vez para este grupo", no como "todavía falta
mandarlo".

**Perillas (todas en `docker-compose.yml`, servicio `flask-app`):**

| Variable | Default | Qué hace |
| --- | --- | --- |
| `NO_SHOW_MARGEN_MIN` | `30` | Minutos después de `inicio` para marcar `no_show`. El deadline real es `min(inicio + margen, fin)`: una reserva más corta que el margen no puede vencer después de haber terminado. |
| `AVISO_PREVIO_MIN` | `30` | Minutos antes de `inicio` para el aviso `previo`. |
| `AVISO_DEVOLUCION_MIN` | `30` | Minutos antes de `fin` para el aviso `devolucion` (reservas `entregada`). |
| `AVISO_VENTANA_MIN` | `120` | Corte de frescura (arriba). |
| `BARRIDO_INTERVALO_SEG` | `60` | Cada cuánto despierta el ticker. También es el TTL del lock de Redis. |
| `BARRIDO_ACTIVO` | `1` | `0` apaga el hilo ticker. **No apaga el barrido perezoso**: el listado de reservas lo sigue llamando, así que esto NO es un interruptor para dejar de mandar WhatsApp. Para eso, ver `docs/SEGURIDAD.md`. |

### Firmar el acta en pantalla

`features/reservas/FirmarActaDialog.tsx` es un asistente de cuatro pasos:

1. **Equipos y nota.** Una casilla "Todos los equipos entregados/devueltos",
   marcada por defecto (un solo chulito para todo el caso común), y una por
   equipo para quitar o poner. La maestra se ve a medias si la selección es
   mixta. Sin nada marcado no se puede avanzar. Debajo de un separador va la
   nota opcional del administrador.
2. **Firma del usuario.**
3. **Datos y firma del administrador.**
4. **Confirmar.** Muestra el PDF ya compuesto, el estado al que pasará la
   reserva y la fecha y hora exactas que van a quedar impresas.

La lista de equipos del paso 1 sale de `lineas` en `/acta/estado` (todas las
líneas del grupo, desde el servidor), **no** de la prop `reservas` que llega
del calendario. El calendario oculta las canceladas y aplica filtros, y una
lista filtrada causó dos errores reales: marcas corridas a la fila
equivocada, y el riesgo de cancelar en la entrega una línea que no aparecía
en pantalla. Solo se listan las líneas que ese momento puede mover:
`confirmada`/`no_show` en la entrega, `entregada` en la devolución.

- **El PDF firmado se compone en el navegador, con `pdf-lib`.** Es una
  decisión deliberada de quien mantiene el proyecto, tomada sabiendo el
  compromiso: el servidor guarda como comprobante un PDF cuyo contenido no
  puede verificar. Las razones fueron que al dashboard solo entra el
  administrador, que la vía de "subir un PDF firmado desde otro dispositivo"
  acepta un PDF arbitrario de todas formas, y que el costo de CPU debía
  quedarse fuera de los contenedores porque el despliegue puede escalar. Ver
  `docs/SEGURIDAD.md` para el análisis completo de esa decisión.
- **A mano solo va la firma.** La cédula del usuario la imprime el servidor;
  la del administrador se teclea en un campo del paso 2 y la estampa el
  navegador; la fecha y hora se ponen solas. Hubo una iteración intermedia en
  la que el lienzo cubría el cuadro entero para escribir la cédula a pulso, y
  se descartó tras probarla: escribir números con el ratón es incómodo y el
  resultado se ve peor que el texto impreso.
- **El navegador recuerda al administrador.** Nombre y cédula se precargan
  desde `localStorage` (`dashboard-lacs-monitor-acta`) y se guardan solo
  después de registrar un acta con éxito. Se pueden editar. Ojo: en un
  equipo de mostrador compartido, el siguiente monitor verá la cédula del
  anterior.
- **No se pierde una firma por un clic de más.** Si ya hay un trazo, Escape,
  el clic fuera del diálogo y "Cancelar" abren "¿Descartar las firmas?",
  con el foco en "Seguir firmando".
- **La fecha se congela al abrir el diálogo**, no al componer el PDF ni al
  guardarlo, y se replica en los dos cuadros del momento que se firma. Así la
  devolución lleva la fecha del momento en que se pidió firmarla, no la que
  quedó impresa en la entrega. Formato `22/09/2026, 3:45 p. m.`, construido
  con `Intl.DateTimeFormat("es-CO")` fijando la zona de la LACS -- no con
  `getHours()`, que daría la hora del navegador si alguien abre el dashboard
  desde otro huso.
- **El lienzo muestra de fondo la franja real del acta.** `recorte-acta.ts`
  renderiza el PDF con pdf.js y recorta la zona de firma, para que la persona
  vea la raya sobre la que firmar en vez de un rectángulo vacío. Esa zona se
  definió a propósito incluyendo la raya y la etiqueta "Firma": si cubriera
  solo el hueco en blanco de encima, el recorte saldría blanco y pdf.js no
  aportaría nada.
- **El documento se va firmando por encima.** La devolución se estampa sobre
  el PDF que ya trae las firmas de entrega, no sobre el acta base. Efecto
  secundario visible: la cabecera del acta de devolución sigue diciendo
  "Estado: Confirmada", porque ese texto quedó congelado cuando se firmó la
  entrega. Es correcto para un comprobante -- registra lo que se firmó, no lo
  que pasó después.
- **La firma se exporta sin recortar al trazo.** El PNG del lienzo va completo,
  con fondo transparente, y se calca 1:1 sobre la zona de firma. Recortarlo al
  bounding box de la tinta (como hacía la primera versión) movería la firma del
  sitio donde la persona efectivamente firmó.

### Anclas de firma: cómo sabe el navegador dónde estampar

El bloque de firmas **flota**: cuántos activos tenga la reserva decide en qué
punto de la página (y en qué página) acaba cayendo. Así que las coordenadas no
se pueden calcular de antemano ni cablear a mano.

`acta_pdf._Ancla` es un `Flowable` de tamaño cero que, cuando reportlab lo
coloca durante el `build`, anota su posición con `canv.absolutePosition(0, 0)`
más el número de página. `generar_acta_con_anclas` devuelve `(bytes, anclas)`,
y `GET /reservas/grupo/<id>/acta/estado` se las entrega al navegador junto con
el momento pendiente y el historial.

Son **12 anclas**, tres por cada uno de los cuatro cuadros
(`entrega_usuario`, `entrega_admin`, `devolucion_usuario`, `devolucion_admin`):

| Clave | Qué marca |
|---|---|
| `{clave}` | La zona de firma: el hueco en blanco, la raya y la etiqueta "Firma" |
| `{clave}_fecha` | El hueco escribible de la línea "Fecha y hora ___" |
| `{clave}_cc` | El hueco escribible de la línea "C.C. ___" |

Dos detalles que cuestan un bug si se ignoran:

- **Las anclas de fecha y C.C. vienen ya desplazadas** más allá de su etiqueta,
  con el `ancho` restante. La primera versión las entregaba apuntando al inicio
  de la línea y era el navegador quien medía el ancho de la palabra `"Fecha "`
  para correrse -- con esa cadena literal duplicada en Python y en TypeScript.
  Ahora el desplazamiento lo calcula el backend con
  `pdfmetrics.stringWidth`, y cambiar la etiqueta del acta no puede desalinear
  nada del otro lado.
- **reportlab y pdf-lib comparten sistema de coordenadas** (origen en la
  esquina inferior izquierda, en puntos), así que las anclas se usan tal cual
  al estampar, sin invertir el eje Y. El canvas 2D del navegador sí tiene el
  origen arriba, y por eso la inversión aparece **solo** en `recorte-acta.ts`,
  donde se recorta la imagen de fondo.

El navegador escribe la fecha y la cédula del administrador **blanqueando
antes el hueco** con un rectángulo blanco: sin eso, el texto cae encima de los
guiones preimpresos y queda tachado e ilegible.

**Anclas de equipos.** La tabla de activos tiene dos columnas de casillas
`[  ]`: "Entr." y "Dev." (la columna "Ubicación" se quitó para hacerles
sitio). Cada fila aporta `entrega_linea_<id>` y `devolucion_linea_<id>`,
con `id` = `Reserva.id` de la línea, y el navegador dibuja ahí
(`actas-pdf.ts`, `estamparMarcas`):

- un **chulito** en la columna del momento, para cada equipo marcado;
- en la **entrega**, una **X** en "Entr." y en "Dev." para cada equipo no
  marcado, porque nunca se va a devolver;
- en la **devolución**, una **X** en "Dev." para cada equipo no marcado
  (queda `no_devuelta`).

Las marcas se dibujan con dos `drawLine`, no con los caracteres "✓" o "✗",
porque Helvetica (WinAnsi) no tiene esos glifos. Las anclas van **por id,
no por número de fila**: la primera versión usaba `entrega_item_N` con N
calculado en el navegador sobre una lista filtrada, y las marcas caían en la
fila equivocada. Las actas firmadas con esa versión (o antes) no traen
anclas `_linea_`: el diálogo avisa que las marcas no se imprimirán, y el
cambio de estado se aplica igual.

En `acta_pdf.py`, el `ALIGN CENTER` del `TableStyle` **no** debe cubrir las
columnas Entr./Dev.: `_Ancla` mide 0 de ancho, y la tabla la correría al
centro de la celda. El centrado visual de `[  ]` lo da el propio
`Paragraph` (`_CELDA_CENTRADA`).

### Histórico de actas (`reserva_actas` + volumen `actas_pdf`)

Cada firma y cada subida genera un **archivo nuevo**; nunca se sobrescribe
nada. El acta "vigente" es simplemente la fila más reciente del grupo.

- Los PDF viven en el volumen `actas_pdf`, montado en `/app/actas` dentro de
  `flask-app` (`ACTAS_DIR`), organizados en `<grupo_id>/<timestamp>-<momento>-<origen>.pdf`.
  Es un volumen con nombre y no un bind mount porque son el histórico
  probatorio: tienen que sobrevivir a un `docker compose down` y a cualquier
  reconstrucción de la imagen.
- `reserva_actas` guarda los metadatos: `momento` (`entrega`/`devolucion`),
  `origen` (`firmada` en pantalla / `subida` desde fuera), la ruta relativa,
  `sha256`, `bytes`, las `anclas` (JSONB) que se usaron para ese archivo y
  `firmado_por`. La FK es `ON DELETE CASCADE` hacia `reserva_grupos` para que
  borrar una reserva siga funcionando; los archivos en disco **no** se borran,
  que es justamente el punto de un histórico.
- Las anclas se guardan **por versión** y no se recalculan: la firma siguiente
  se estampa sobre ese archivo concreto, así que tiene que usar sus
  coordenadas aunque la reserva haya cambiado después.
- `POST /reservas/grupo/<id>/actas` (multipart) valida el momento contra
  `momento_siguiente` (409 si no coincide), la cabecera `%PDF-` y el tamaño, y
  hace el guardado y la transición de estado en **un solo `commit`**. Si el
  commit falla, borra el archivo recién escrito para no dejar huérfanos.
- `GET /reservas/grupo/<id>/actas/<acta_id>/pdf` descarga una versión
  histórica; comprueba que el acta pertenezca a ese grupo y que la ruta
  resuelta quede dentro de `ACTAS_DIR`.

Dos ajustes de infraestructura que hicieron falta y son fáciles de perder:

- `client_max_body_size 25m` en la `location /dashboard/api/` de
  `dashboard-frontend/nginx.conf`. El límite por defecto de nginx es 1 MB y
  rechazaría con un 413 cualquier acta con firmas incrustadas, antes de que
  llegara a Flask.
- El tipo MIME de `.mjs`, añadido a la tabla global de nginx desde el
  `Dockerfile` del frontend. nginx 1.27 no lo trae y servía el worker de
  pdf.js como `application/octet-stream`; el navegador rechaza un módulo ES
  con ese MIME, pdf.js caía a su "fake worker" y el fondo del lienzo salía en
  blanco, con un único error en consola como toda pista. Se añade a la tabla
  global y no a una `location` con su propio bloque `types` porque ese bloque
  **reemplaza** la tabla heredada en lugar de ampliarla.

### Historial de estados (`reserva_estado_historial` + vista `reserva_puntualidad`)

`reservas.estado` solo dice cómo está una línea **ahora**. Para métricas de
meses o años (quién recoge tarde, quién devuelve tarde) está
`reserva_estado_historial`: una fila por cada alta, cambio de `estado`, cambio
de `inicio`/`fin` y borrado de una línea de `reservas`.

- La llena un **trigger de Postgres** (`registrar_historial_reserva`), no la
  app: cubre el bot, el dashboard, el barrido de no-show y cualquier `psql` a
  mano sin tocar ninguno de esos caminos.
- **Sin foreign keys**: sobrevive al borrado de la reserva, del grupo y del
  usuario (`api_eliminar_reserva_grupo` y el quitar un activo de un grupo
  ya no borran el rastro).
- Cada fila copia `inicio`/`fin`/`usuario_id`/`recurso_id` **del momento del
  cambio**: el retraso se mide contra el horario vigente en ese instante.
  `estado_anterior NULL` = alta; `estado_nuevo NULL` = línea borrada.
- **Solo lectura para la app**: la función es `SECURITY DEFINER` y a
  `APP_DB_USER` se le revocan `INSERT/UPDATE/DELETE/TRUNCATE` sobre la tabla.
- `cambiado_en` es `now()`, la hora de **inicio de la transacción**: todos los
  cambios de un mismo `commit` comparten hora.
- Backfill único al crearla: las entregas y devoluciones anteriores se
  reconstruyeron desde la primera acta de cada momento en `reserva_actas`.

La vista `reserva_puntualidad` deja una fila por entrega y por devolución con
el `retraso` ya calculado (entrega contra `inicio`, devolución contra `fin`;
positivo = tarde):

```sql
SELECT usuario_id, COUNT(*) FILTER (WHERE retraso > interval '0') AS recogio_tarde
FROM reserva_puntualidad
WHERE evento = 'entrega' AND ocurrido_en > now() - interval '1 year'
GROUP BY usuario_id;
```

### Activos no devueltos (`recursos.estado = 'no_devuelto'` + `recurso_estado_historial`)

Un equipo que no vuelve en la acta de devolución (el administrador lo dejó
sin marcar) se saca del catálogo del bot sin que nadie tenga que acordarse de
hacerlo a mano: su línea de reserva pasa a `no_devuelta`
(`actas_service.ESTADO_NO_DEVUELTA`) y su `recursos.estado` a `no_devuelto`
(`actas_service.RECURSO_NO_DEVUELTO`), en la misma transacción que cierra el
resto de la devolución.

- **Se comporta como `no_disponible` para todo lo automático.** El bot solo
  ofrece equipos con `estado = 'disponible'` (`recursos_service`), y crear una
  reserva a mano desde el dashboard sobre un activo `no_devuelto` lo bloquea
  igual que cualquier otro estado fuera de catálogo.
- **El bot dice que no está.** `recursos_service.modelos_no_disponibles_en_texto`
  busca entre los modelos que no tienen ninguna unidad `disponible`. Para el
  estudiante, `no_devuelto`, `no_disponible` y `en_mantenimiento` son iguales y
  no se le dice el motivo. Si el texto nombra uno de esos modelos con más
  puntaje que el mejor modelo disponible:
  - `evaluar_consulta_disponibilidad` responde *"X: no está disponible por
    ahora"*;
  - `_iniciar_reserva` no deja reservarlo.

  Así no se cambia en silencio por el modelo más parecido (la EOS R por la
  EOS R6). Si hay empate, la consulta lista los dos modelos y la reserva
  sigue como siempre.
- **Solo la LACS lo saca de ahí, desde Activos.** El único camino de entrada
  es el acta de devolución; `PATCH /api/activos/<id>` responde **409** si
  alguien intenta poner `no_devuelto` a mano
  (`routes/dashboard.api_actualizar_activo`), pero sí permite moverlo a
  cualquier otro estado, o reenviar `no_devuelto` sin cambiarlo (el
  formulario de edición manda el estado actual aunque no se toque). La UI lo
  muestra en la lista de estados posibles pero lo deja deshabilitado como
  opción a elegir (`ActivoEstadoSelect.tsx`, `ActivoEditForm.tsx`); el alta de
  un activo nuevo ni lo ofrece (`ActivoNuevoForm.tsx`).
- **Salir de `no_devuelto` pide un motivo.** A cualquier estado, también
  `disponible`: `devuelto_tarde`, `encontrado` u `otro` (este con nota
  obligatoria). Sin motivo el PATCH responde **400**, y en Activos lo pide
  `MotivoNoDevueltoDialog.tsx`. La API lo pasa al trigger con `set_config(...,
  true)` y queda en `recurso_estado_historial.motivo`/`nota`, junto con
  `reserva_id`: la reserva que dejó el activo sin devolver, tanto en la fila
  de entrada como en la de salida. Ver la consulta de métricas en
  docs/SESION_2026-09-28.md.
- **`recurso_estado_historial` es el mismo patrón que
  `reserva_estado_historial`:** trigger `SECURITY DEFINER` sobre
  `UPDATE ON recursos`, sin FKs, y de solo lectura para la app
  (`INSERT/UPDATE/DELETE/TRUNCATE` revocados a `APP_DB_USER`). Solo registra
  `UPDATE`s (altas y bajas de activos no importan acá). La reserva que causó
  un `no_devuelto` se encuentra cruzando `recurso_id` + `cambiado_en` con
  `reserva_estado_historial.estado_nuevo = 'no_devuelta'` (van en la misma
  transacción, mismo timestamp).
- **`reserva_puntualidad` excluye `no_devuelta` a propósito**: mezclar "nunca
  volvió" con el retraso de las que sí volvieron contaminaría la métrica de
  puntualidad. El conteo de no devueltas es una consulta aparte:

```sql
SELECT usuario_id, count(*) FROM reserva_estado_historial
WHERE estado_nuevo = 'no_devuelta' GROUP BY 1;
```

### Conversación de una sesión (Usuarios → Sesiones)

`GET /dashboard/api/reservas/<reserva_id>/conversacion` (`api_conversacion_reserva`,
`app/routes/dashboard.py`) reconstruye el hilo de WhatsApp que terminó en una
reserva, para verlo desde un modal al hacer clic en una fila de la pestaña
"Sesiones" (`features/usuarios/ConversacionSesionDialog.tsx`).

- **Aparte del listado, pedido solo al abrir el modal.** `GET /api/reservas`
  ya devuelve hasta 500 filas para pintar la tabla de "Sesiones"; meter ahí
  el hilo completo de cada una multiplicaría esa respuesta por nada, porque
  el hilo solo interesa cuando alguien abre una sesión concreta. El
  frontend monta `ConversacionSesionDialog` únicamente mientras hay una
  sesión seleccionada, así que su `useEffect` llama al endpoint al abrir y
  se desmonta sin dejar nada pendiente al cerrar -- el polling de 30 s del
  listado nunca lo toca.
- **Hace falta una heurística porque no hay FK.** `auditoria_bot` no guarda
  `reserva_id` (ver "Flujo de un mensaje entrante" más arriba): el único nexo
  entre una reserva y sus mensajes es `telefono_wa` + tiempo. El endpoint
  ancla la búsqueda en `Reserva.creado_en` y expande hacia atrás y hacia
  adelante mientras los mensajes consecutivos del mismo teléfono queden
  separados por menos de 30 minutos (`HUECO_CONVERSACION`); un hueco mayor
  se toma como el arranque de otra conversación. Como tope de seguridad,
  nunca mira más allá de 24 h del ancla ni devuelve más de 100 mensajes
  (si hay que recortar, se quedan los más cercanos al ancla y la respuesta
  marca `"truncada": true`).
- **Límites conocidos de la heurística.** Si la misma persona hace dos
  reservas seguidas dentro de la misma conversación, ambas muestran el
  mismo hilo -- es correcto, es literalmente la misma conversación. Los
  mensajes bloqueados antes de identificar al usuario pueden haberse
  auditado bajo un LID distinto a `usuarios.telefono_wa` y no aparecer en
  el hilo.
- **Reservas de `canal_origen='dashboard'` no tienen hilo.** El endpoint
  responde 200 con `"motivo": "sin_conversacion"` (no es un error) y el
  modal lo muestra como estado vacío explícito, en vez de intentar buscar
  mensajes que nunca existieron.

### Métricas (`features/metricas/`)

Todo se calcula en el momento con SQL sobre las tablas que ya existen. No hay
tablas de agregados ni jobs que precalculen nada. Los endpoints viven en
`app/routes/dashboard.py`:

| Endpoint | Qué devuelve |
|---|---|
| `GET /api/metricas` | la subpestaña General |
| `GET /api/metricas/activo/<id>` | las métricas de un solo activo (404 si no existe) |
| `GET /api/metricas/activo/<id>/timeline` | los eventos del activo, del más nuevo al más viejo |
| `GET /api/metricas/uso` | equipos pedidos por asignatura (por NRC) y en reservas particulares (por estudiante) |
| `GET /api/metricas/excel` | lo mismo que `/api/metricas`, pero en `.xlsx` |
| `GET /api/datos/excel` | todas las tablas de `public`, una por hoja, sin filtro |

**Periodo.** Todos, menos `/api/datos/excel`, aceptan `periodo=30|90|todo|rango`.
Con `rango` se mandan también `desde` y `hasta` en formato `YYYY-MM-DD`,
como fechas civiles de la LACS, y los dos días se incluyen. Por dentro,
`hasta` se vuelve exclusivo: las 00:00 locales del día siguiente. Un periodo
inválido responde 400 con el mensaje de error. `_periodo_metricas()` hace ese
parseo y `_en_periodo(col)` arma el filtro SQL. `col` siempre es un literal
del código y las fechas van como parámetros. Las reservas entran al periodo
según su `inicio`, y los agrupamientos por día y por hora se hacen en la zona
de la LACS, no en UTC.

**Definiciones.**

- **Préstamo**: una reserva en `entregada`, `completada`, `devuelta_tarde`
  o `no_devuelta` (`_PRESTADAS`). El equipo salió.
- **Dado de baja**: un activo en `no_disponible`. Se cuentan las transiciones
  del periodo, tomadas de `recurso_estado_historial`, y el total de hoy.
- **Mantenimiento**: se cuenta igual que dado de baja.
- **Puntualidad**: sale de la vista `reserva_puntualidad`.
- **Seguro**: son los grupos con `sale_de_barranquilla = TRUE`.

**Timeline.** Es un `UNION ALL` de cuatro fuentes:
`reserva_estado_historial` (alta, cambio de estado, cambio de horario y
borrado de la reserva), `recurso_estado_historial`, el alta del activo y las
`reserva_actas` de los grupos donde estuvo el activo. Devuelve como máximo 500
eventos (`_LIMITE_TIMELINE`); si hay más, marca `truncado`. El endpoint
acepta `periodo` como los demás, pero el frontend lo pide siempre con
`periodo=todo` (`TimelineActivo.tsx`): el timeline es la historia completa
del activo, y en esa vista el selector de periodo se oculta. Las métricas del
activo sí usan el mismo periodo que General (un solo estado en `MetricasPanel`).

**Por uso.** `/api/metricas/uso` trae una fila por línea de equipo del
periodo (los espacios no cuentan), en cualquier estado, con el `tipo_uso` y el
`nrc` de su `reserva_grupos`. `_resumen_uso` las agrega en Python por clave:
el NRC en `asignatura` y el usuario del grupo en `particular`. Por cada clave
devuelve reservas (grupos distintos), equipos pedidos, préstamos (`_PRESTADAS`),
estudiantes distintos y sus 5 equipos más pedidos. Por sección, devuelve el top
10 de claves y los 10 equipos más pedidos. Las reservas se agregan en Python y
no en SQL porque un grupo con varios equipos cuenta una vez por clave, pero
una vez por equipo en el conteo de cada equipo. El Excel de métricas no lo
incluye.

**Frontend.** Detalles que no salen del SQL:

- `reservas_por_dia` solo trae los días con reservas. `conDiasVacios`
  rellena con 0 los días intermedios, para que el eje sea continuo y una
  racha sin reservas se vea como tal.
- Los intents `*_cpa` y `*_lacs` comparten etiqueta; `sumarPorEtiqueta`
  los junta en una barra en vez de mostrar "Confirmación de la LACS" dos
  veces.
- Las cifras van en `Cifras`/`Cifra` (una superficie dividida en celdas,
  no una tarjeta por número), con columnas fijadas según cuántas cifras
  lleva cada grupo para que ninguna fila quede con una sola.

**Excel** (`openpyxl`).

- **Periodo:** el botón del panel arma la URL con el filtro activo
  (`urlExcelMetricas(filtro)`: `periodo` y, en personalizado, `desde` y
  `hasta`), así que el archivo trae el mismo periodo que la página. El
  subtítulo de cada hoja lo repite ("Periodo: últimos 90 días · generado
  el…").
- **Métricas** (`app/metricas_excel.py`): `api_metricas_excel` llama a
  `api_metricas()` y `api_metricas_uso()` con el mismo query string, así que
  los números son los de pantalla, y `libro_metricas` arma siete hojas:
  Resumen, Estados, Cuándo se reserva, Top activos y usuarios, Por uso (NRC
  y particulares), Inventario y seguro, y Bot y hábitos.
  - **Tablas:** encabezado grafito, filas cebra y barras de datos
    (formato condicional) en las columnas de conteo.
  - **Estados:** cada fila lleva el tinte de su color (verde, ámbar o rojo,
    como los chips del panel); los fallidos y los no devueltos mayores que
    cero van en rojo.
  - **Gráficos:** barras nativas de Excel con el valor rotulado (los ceros
    no se rotulan) y la barra más alta en grafito pleno. El de Estados usa el
    color de cada estado. Por día rellena los días sin reservas, igual que el
    panel.
  - **Ejes en openpyxl 3.1:** hay que poner `delete = False` o los ejes no
    salen. En las barras horizontales, `x_axis.scaling.orientation =
    "maxMin"` pone el primero de la tabla arriba y `y_axis.crosses = "max"`
    deja el eje de valores abajo. Si ese cruce va en `x_axis`, las barras
    crecen desde la derecha y salen en blanco.
  - Por uso trae, por cada NRC o estudiante, los equipos que más pide como
    texto ("Trípode ×10 · iMac ×7"), además del gráfico de equipos pedidos.
- **Raw** (modo `write_only`): hace `SELECT *` de cada tabla **y vista**
  de `public`; hoy la única vista es `reserva_puntualidad`, que es
  calculada. Los nombres salen de `information_schema`, no del usuario. La
  primera hoja, "Índice", dice qué es cada hoja (tabla o vista) y cuántas
  filas y columnas trae. Cada hoja tiene el encabezado fijo, filtro y anchos
  según el contenido. Las listas (`calendario_reglas.dias_semana_bloqueados`)
  y el `jsonb` (`reserva_actas.anclas`) van como texto JSON. Los PDF de las
  actas no están en la base, sino en disco (`reserva_actas.archivo` es la
  ruta), así que el Excel trae la ruta y no el archivo.
- **Tablas grandes:** Excel admite 1.048.576 filas por hoja
  (`_MAX_FILAS_HOJA` = 1.048.575 de datos más el encabezado). Una tabla más
  grande sigue en `auditoria_bot (2)`, `auditoria_bot (3)`…, cada una con su
  encabezado, y la columna "Hojas" del Índice dice en cuáles quedó. El nombre
  se recorta para no pasar los 31 caracteres de Excel
  (`_nombre_parte`).
- **Memoria:** las filas se leen por lotes de 5.000 con un cursor del lado del
  servidor (`stream_results`), se escriben en modo `write_only` y el archivo
  se arma en un temporal en disco, no en memoria. Así, una tabla de un millón
  de filas no tumba el contenedor. El Índice se crea primero y sus filas se
  escriben al final, con lo que de verdad se exportó. En `write_only`,
  openpyxl no deja buscar hojas por nombre (`wb[...]`, `move_sheet`), así
  que se guardan las hojas mismas.
- **Verificación:** `flask-app/verificar_excel_raw.py` comprueba que cada
  tabla y vista coincida con `count(*)`. Luego baja el límite a 100 filas
  para forzar el corte de `auditoria_bot` con los datos de hoy y revisa que
  entre todas las hojas estén todos los `id`, sin repetir ni perder ninguno.
  No escribe en la base:
  `docker exec -i lacs_flask_app python - < flask-app/verificar_excel_raw.py`.
- `_celda` escribe todo texto como texto (`data_type = "s"`). Sin eso, un
  mensaje de WhatsApp en `auditoria_bot` que empiece por `=` se ejecutaría
  como fórmula al abrir el archivo. También quita los caracteres de control
  que Excel rechaza.
- Las fechas con zona horaria se escriben en hora local de la LACS; Excel no
  guarda zonas.
- El frontend descarga con un `<a href download>`. El navegador ya manda la
  autenticación básica del panel, así que no hace falta `fetch` ni blob.

## Excepciones: lista negra de fechas

Pestaña "Excepciones" del dashboard (`features/excepciones/`): días en que
el bot de WhatsApp no acepta reservas, editables desde una vista calendario
y una vista lista.

- **Dos tablas** (`postgres/init/02-schema.sql`): `calendario_reglas` --
  una sola fila (`id=1`) con `dias_semana_bloqueados` (`SMALLINT[]`, 0 =
  lunes ... 6 = domingo, igual que `date.weekday()` en Python y
  `diaDeSemana()` en el frontend) y `festivos_co` (booleano); y
  `fechas_excepcion` -- una fila por día concreto (`fecha` como PK), con
  `tipo` ('bloqueo' | 'desbloqueo') y `motivo` opcional. Los festivos de
  Colombia NO se guardan: se calculan en caliente con el paquete `holidays`
  (`holidays.country_holidays("CO")`), igual que la cláusula de seguro.
- **Precedencia** (`app/fechas_bloqueadas_service.py`, función `_describir`):
  1) la excepción manual del día, si existe (`bloqueo` cierra, `desbloqueo`
  abre); 2) si no hay excepción, festivo de Colombia con `festivos_co=True`;
  3) si no, el día de la semana está en `dias_semana_bloqueados`. Un día sin
  ninguna de las tres es un día normal.
- **Servicio único:** todo el que necesite saber si una fecha se puede
  reservar (el bot, el dashboard) pasa por
  `app/fechas_bloqueadas_service.py` -- nunca se reimplementa la lógica de
  precedencia en otro sitio. Expone `describir_fecha`, `es_fecha_bloqueada`,
  `motivo_bloqueo` (cláusula en español lista para WhatsApp),
  `siguiente_fecha_habilitada`, `dias_en_rango` (solo los días "relevantes":
  con regla, festivo o excepción -- los días normales no se listan, para no
  mandar 365 filas al frontend) y `texto_reglas` (resumen para el mensaje
  del paso "fecha" del wizard).
- **Endpoints** (`app/routes/dashboard.py`, todos bajo `/dashboard/api/`):
  `GET /excepciones?desde&hasta` (reglas + días relevantes del rango),
  `GET`/`PUT /excepciones/reglas`, `POST /excepciones/fechas` (upsert de una
  fecha o un rango `desde..hasta`) y `DELETE /excepciones/fechas/<fecha>`
  (quita la excepción; el día vuelve a seguir las reglas).
- **El bot solo valida el día de inicio y el de fin** (entrega y
  devolución, cuando la LACS tiene que estar abierta) de cada reserva -- los
  días intermedios de un préstamo de varios días sí pueden caer en domingo
  o festivo (un préstamo viernes → lunes es válido). Ver la sección "Lista
  negra de fechas en el wizard" más arriba para los puntos de enganche.
- **El plazo del seguro no cambia:** `es_dia_habil` /
  `fecha_minima_sale_de_barranquilla` siguen contando solo lunes a viernes
  sin festivos, independientemente de las reglas de esta pestaña.
- **El dashboard no bloquea ediciones**, mismo criterio que con el plazo
  del seguro: `ReservaDetalle.tsx` avisa (texto en `text-warning`) si el
  inicio o el fin editado cae en un día de la lista negra, pero no impide
  guardar.
- **Valores por defecto:** `{5, 6}` (sábados y domingos) más festivos
  activados -- con esto el bot empieza a rechazar sábados además de
  domingos y festivos, ver `docs/SESION_2026-09-18.md`.

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

`App.tsx` ya no tiene cabecera sobre el contenido: el `<main>` empieza
arriba de todo. El botón de abrir/cerrar el sidebar (`SidebarTrigger`) vive
en el `SidebarHeader` y sigue visible con el sidebar colapsado a íconos; el
de tema (`ThemeToggle`) vive en el `SidebarFooter`. Los dos usan
`variant="outline"` para no confundirse con las pestañas. En móvil el
sidebar es un panel flotante, así que hay un segundo `SidebarTrigger` con
`md:hidden` arriba del `<main>`.

## Analítica en tiempo real

Los endpoints en `app/routes/analytics.py` consultan directamente la
tabla `reservas`. Para volumen bajo (el caso típico de una LACS
universitaria) esto es suficiente sin necesidad de un pipeline de datos
aparte. Si más adelante el volumen crece, considera:
- Vistas materializadas en Postgres para los agregados más pedidos.
- Un job periódico que precalcule métricas en vez de calcularlas on-demand.
