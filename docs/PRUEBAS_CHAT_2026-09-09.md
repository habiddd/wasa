# Reporte de pruebas: holgura conversacional del bot y cambio a `lfm2.5-2.6b`

**Fecha:** 9 de septiembre de 2026
**Alcance:** clasificación de intents, respuesta a conversación normal, menú
de fallback, y latencia del chat con el modelo nuevo.
**Entorno:** todo dentro de Docker (`docker compose build flask-app && docker
compose up -d flask-app`), contra el LM Studio real del host
(`http://host.docker.internal:1234`). No se ejecutó nada en el host.

**Objetivo del pedido:** que el chat tenga más holgura, que entienda cuando
el usuario intenta hablar normalmente, y que el menú que ya existe se use
solo cuando de verdad no se entiende. Se contaba con que `lfm2.5-2.6b` es
mejor que el `1.2b` pero más lento.

---

## Resumen ejecutivo

| Qué se midió | Antes | Después |
|---|---|---|
| Clasificación correcta (batería de 20 mensajes, LLM real) | — | **20/20** |
| Lógica pura (sin red) | — | **40/40** |
| Saludo (`"hola"`) | menú de fallback | respuesta conversacional, **0.0 s** |
| Empezar una reserva (`"quiero reservar una cámara canon eos r"`) | **76 s** | **~0 s** de lógica |
| Peor caso de clasificación | 150 s de timeout | **45 s**, acotado |
| Mensajes que ni tocan el LLM | 0 | **11 de 20** |

Tres hallazgos que no estaban en el pedido y salieron de las pruebas:

1. `lfm2.5-2.6b` **sí arregla** el sesgo del `1.2b` que motivó el guard
   rígido de la sesión anterior, pero **no se le puede apagar el
   razonamiento** por API: cuesta 13-25 s por clasificación.
2. La ruta rápida nueva confundía "desistir" con "reservar"
   (`"ya no voy a necesitar la reserva de mañana"`). Corregido.
3. **`extraer_datos_reserva` era código muerto caro**: costaba 54-78 s por
   cada reserva iniciada y no podía cambiar el resultado. Quitado del camino
   crítico. Este es, de lejos, el cambio que más holgura le dio al chat.

---

## 1. El modelo nuevo sí arregla el problema de fondo

La sesión anterior documentó que `liquid/lfm2.5-1.2b` clasificaba
`"buenos días"` como `reservar`, y por eso se le puso un guard que exigía que
el usuario hubiera escrito literalmente una palabra clave de la categoría.
Primera prueba: comparar los dos modelos con el mismo prompt.

| Modelo | Mensaje | Tiempo | Tokens de razonamiento | Respuesta |
|---|---|---|---|---|
| `liquid/lfm2.5-1.2b` | `"buenos dias"` | 3.8 s | 0 | `reservar` ❌ |
| `liquid/lfm2.5-1.2b` | `"necesito una camara para el viernes"` | 0.5 s | 0 | `reservar` ✅ |
| `lfm2.5-2.6b` | `"buenos dias"` | 25.6 s | 189 | `otro` ✅ |
| `lfm2.5-2.6b` | `"necesito una camara para el viernes"` | 15.0 s | 182 | `reservar` ✅ |

**Conclusión:** el sesgo desaparece. Eso habilita aflojar el guard, que era
justo lo que le quitaba holgura al chat. El costo es la latencia.

### 1.1 No se puede apagar el razonamiento (se intentó de tres formas)

| Intento | Tiempo | Tokens de razonamiento |
|---|---|---|
| Sin nada (baseline) | 16.5 s | 189 |
| `chat_template_kwargs: {"enable_thinking": false}` | 17.1 s | 189 |
| `/nothink` al final del system prompt | 13.1 s | 177 |
| `reasoning_effort: "none"` | 13.8 s | 189 |
| `reasoning_effort: "low"` | 12.8 s | 189 |

Ninguno funciona: LM Studio los acepta sin error y el modelo razona igual.
**La latencia hay que asumirla o evitarla llamando menos al modelo.** Se
tomó el segundo camino (sección 3).

---

## 2. Pruebas de lógica pura — 40/40

Sin red, dentro del contenedor. Cubren las funciones nuevas de
`llm_client.py`.

| Grupo | Casos | Resultado |
|---|---|---|
| `_clasificar_sin_llm` (ruta rápida) | 17 | 17/17 |
| `_es_charla_social` (saludo puro vs. pedido con saludo) | 11 | 11/11 |
| `_tiene_senal_de_dominio` | 7 | 7/7 |
| `_extraer_intent_de_texto` (respuestas crudas del LLM) | 7 | 7/7 |
| `_limpiar_para_whatsapp` | 3 | 3/3 |
| Heurístico de respaldo (LLM caído) | 5 | 5/5 |

Casos que valen la pena resaltar porque son trampas reales:

- `"hola, necesito una cámara"` **no** es saludo (queda residuo
  `"necesito camara"` al restar las frases sociales) → sigue siendo un pedido.
- `"también quiero el trípode"` no se confunde con `"bien"`, ni
  `"los reyes magos"` con `"ey"`: las palabras sociales de un solo término se
  comparan token a token, no por substring.
- `"No es cancelar, es reservar"` → `reservar`. Antes ganaba la primera
  categoría de la tupla `INTENTS_VALIDOS` (siempre `reservar`), sin importar
  cuál fuera la conclusión; ahora gana la última mencionada.
- `"consultar disponibilidad"` (con espacio, como a veces lo escribe el
  modelo) se reconoce igual que `consultar_disponibilidad`.

---

## 3. Clasificación contra el LLM real — 20/20

Con `lfm2.5-2.6b`, dentro del contexto de Flask, pasándole los 29 términos
reales del catálogo. `rápido` = resuelto sin llamar al modelo.

| Mensaje | Intent | Vía | Tiempo |
|---|---|---|---|
| `hola` | `saludo` | rápido | 0.0 s |
| `buenos dias` | `saludo` | rápido | 0.0 s |
| `como estas?` | `saludo` | rápido | 0.0 s |
| `quien eres?` | `saludo` | rápido | 0.0 s |
| `gracias, muy amable` | `saludo` | LLM | 19.3 s |
| `que puedes hacer` | `ayuda` | rápido | 0.0 s |
| `ayuda` | `ayuda` | rápido | 0.0 s |
| `necesito una camara para el viernes` | `reservar` | LLM | 9.4 s |
| `quiero reservar la sala de edicion 1` | `reservar` | rápido | 0.0 s |
| `me apartas el dron el lunes a las 3pm` | `reservar` | rápido | 0.0 s |
| `tienen algo para grabar video el jueves?` | `consultar_disponibilidad` | LLM | 13.3 s |
| `que tienen disponible` | `consultar_disponibilidad` | rápido | 0.0 s |
| `quiero saber si hay una camara libre` | `consultar_disponibilidad` | LLM | 19.9 s |
| `y el dron dji esta libre?` | `consultar_disponibilidad` | rápido | 0.0 s |
| `cancela mi reserva` | `cancelar` | rápido | 0.0 s |
| `ya no voy a necesitar la reserva de manana` | `cancelar` | LLM | 16.8 s |
| `cual es la capital de mongolia` | `otro` | LLM | 15.0 s |
| `asdfgh` | `otro` | LLM | 26.7 s |
| `esto no tiene sentido` | `otro` | LLM | 45.0 s (tope) |
| `me haces un poema sobre el mar` | `otro` | LLM | 29.4 s |

**20/20 correctos. 11 de 20 se resolvieron sin tocar el modelo.**

Vale la pena mirar dos filas concretas:

- `"tienen algo para grabar video el jueves?"` → `consultar_disponibilidad`.
  Este mensaje es exactamente el que el guard viejo mandaba al menú de
  fallback: no contiene ninguna palabra clave de ninguna categoría. Ahora
  pasa porque el guard solo exige *señal de dominio* ("grabar", "video",
  "jueves"), no la palabra exacta de la categoría.
- `"que puedes hacer"` → `ayuda`, instantáneo. La sesión anterior lo dejó
  anotado como pendiente sin arreglar ("el LLM responde `otro` y el
  heurístico solo reconoce 'ayuda'/'help'"). Ya está resuelto.

### 3.1 Los tres fallos de la primera ronda y qué se hizo

La primera corrida dio **15/18**. Los tres fallos se investigaron
consultando el endpoint del LLM directamente, para separar "el modelo se
equivoca" de "nuestro código se equivoca":

| Caso | Qué pasó | Qué respondía el LLM en crudo | Diagnóstico y arreglo |
|---|---|---|---|
| `"ya no voy a necesitar la reserva de manana"` → `reservar` | La ruta rápida vio la subcadena `"reserv"` en la palabra *reserva* y disparó sin preguntar | `cancelar` ✅ | **Bug nuestro.** El modelo acertaba y nosotros lo atropellábamos. Se agregó `MARCADORES_DESISTIR` ("ya no", "no voy a", "desisto"…): con una de esas presente, la ruta rápida se abstiene y decide el LLM |
| `"esto no tiene sentido"` → `ayuda` en 72.6 s | El modelo se enredó: 952 tokens de razonamiento y una conclusión mala | `ayuda` ❌ | **El modelo se equivoca.** Se bajó `max_tokens` de 2000 a 800 y el timeout de 150 s a 45 s; al agotarse el presupuesto responde el heurístico, que acierta (`otro`) |
| `"me sirve algo para grabar un podcast el martes"` → `consultar_disponibilidad` | — | `consultar_disponibilidad` | **Expectativa mal puesta.** La frase es genuinamente ambigua y responder con disponibilidad es correcto. Se corrigió el caso de prueba, no el código |

### 3.2 Por qué el peor caso ahora está acotado en 45 s

Toda clasificación que el modelo **acierta** llegó en 30 s o menos. Pasado
ese punto lo que hay es un modelo enredado que además concluye mal. Cortar
en 45 s y responder con el heurístico da **mejor respuesta y menos espera**:

```
WARNING in llm_client: El LLM agotó el presupuesto de tokens razonando
sobre 'esto no tiene sentido'; se usa el heurístico
OK  esto no tiene sentido  -> otro  [45.1s]
```

Antes ese mensaje podía tener al usuario esperando hasta 150 s.

---

## 4. Pruebas end-to-end por el webhook real

`POST /webhook/whatsapp` con el payload real de `messages.upsert`, usuario de
pruebas `000000000000`, revisando Redis y `auditoria_bot` entre paso y paso.

> El `status: error_envio` que devuelven todas es esperado: el número de
> pruebas no existe en WhatsApp, así que Baileys no puede entregar. La
> respuesta que se iba a enviar queda igual en `auditoria_bot`, que es lo
> que se verificó. Esos ~10 s fijos de cada llamada son el intento de envío
> fallido, no lógica del bot.

| Mensaje | `status` | `intent` auditado | Respuesta |
|---|---|---|---|
| `hola` | ok | `saludo` | *"¡Hola! Aquí estoy 🙂 Te ayudo a reservar equipos o espacios de la CPA, a consultar si algo está disponible, o a cancelar una reserva. Cuéntame qué necesitas."* |
| `cual es la capital de mongolia` | `menu_fallback` | `menu_fallback` | El menú de tres opciones |
| `3` (dentro del menú) | ok | `cancelar` | Lista de reservas activas del usuario |
| `cerrar` | ok | `cancelar` | *"No cancelé ninguna reserva."* |
| `quiero reservar una camara canon eos r` | ok | `reservar` | Activo resuelto + accesorios por defecto |
| `cancelar` (dentro del wizard) | ok | `reservar` | *"Reserva cancelada, no se guardó nada."* |

**Esto es exactamente lo que se pidió:** un saludo se contesta como una
persona, y el menú aparece solo cuando de verdad no se entiende el mensaje.

Verificaciones de estado, entre pasos:

- La invariante de la sesión anterior se sostiene con la opción 3 nueva:
  después de elegirla, la sesión queda
  `{"menu_fallback": null, "cancelacion_wizard": {...}}` — el menú **no** se
  propaga dentro del wizard.
- Al salir, la sesión queda limpia: `{"menu_fallback": null,
  "cancelacion_wizard": null}` y `{"reserva_wizard": null}`.
- **La reserva real de prueba del 22/09 no se tocó:** las filas 47, 48 y 49
  siguen `confirmada`. (En la sesión anterior una clasificación errónea
  llegó a abrir un wizard de cancelación sobre esta misma reserva; se
  comprobó a propósito.)

---

## 5. Hallazgo mayor: `extraer_datos_reserva` era código muerto de 54-78 s

Con la clasificación ya rápida, el cuello de botella pasó a ser el wizard:
`"quiero reservar una cámara"` seguía tardando **76 s**. Midiendo la
extracción sola:

| Mensaje | Tiempo | Devolvió |
|---|---|---|
| `quiero reservar una camara` | 54.2 s | todo `null` |
| `necesito la camara canon eos r el viernes a las 3pm por 2 horas` | 64.8 s | `Cámara Canon EOS R`, `2026-09-11 15:00`, `17:00` ✅ |
| `resérvame el dron mañana de 9 a 11` | 77.6 s | `Dron DJI Mavic`, **`2026-10-10`** ❌ (estando a 9 de septiembre, "mañana" es el 10 de **septiembre**: un mes de más) |

Al revisar quién consume eso, resultó que **nada de lo que devuelve puede
cambiar el resultado**:

- **`inicio` y `fin` nunca se usaron.** El wizard pregunta fecha y hora en
  pasos de formato fijo, por decisión ya documentada (para que el modelo no
  invente una hora que el usuario no dio). Menos mal: el error del mes de
  arriba habría creado una reserva con un mes de diferencia.
- **`recurso` se usaba por una rama inalcanzable.**
  `_resolver_recurso_para_reserva` solo mira el nombre del LLM cuando
  `buscar_recursos_en_texto` **no encontró ningún candidato**, y encima le
  exige `_puntaje_nombre_en_texto(...) > 0`. Pero `candidatos` es
  precisamente el conjunto de nombres con puntaje > 0, así que "sin
  candidatos" implica puntaje 0 para todos. **Las dos condiciones no pueden
  cumplirse a la vez.**

Eso último no se dio por bueno leyendo el código: se verificó ejecutándolo.

```
'quiero reservar una camara'         candidatos=3 con_puntaje=3 rama_llm_alcanzable=False
'necesito la camara canon eos r'     candidatos=1 con_puntaje=8 rama_llm_alcanzable=False
'resérvame el dron manana'           candidatos=1 con_puntaje=1 rama_llm_alcanzable=False
'algo para grabar'                   candidatos=0 con_puntaje=0 rama_llm_alcanzable=False
'el kit de luces led'                candidatos=1 con_puntaje=1 rama_llm_alcanzable=False
'quiero reservar'                    candidatos=0 con_puntaje=0 rama_llm_alcanzable=False
'una sala'                           candidatos=1 con_puntaje=1 rama_llm_alcanzable=False

rama recurso_llm alcanzable en algun caso: False
```

Se quitó la llamada de `_iniciar_reserva`. `extraer_datos_reserva` y su
prompt **siguen existiendo** en `llm_client.py` por si se quieren usar de
verdad más adelante; simplemente ya no están en el camino crítico.

**Regresión de identificación de activos, sin LLM** — idéntica, y en
milisegundos en vez de minutos:

| Mensaje | Resuelto | Tiempo |
|---|---|---|
| `quiero reservar una camara canon eos r` | `Cámara Canon EOS R` | 15 ms |
| `necesito la camara sony a7iii` | `Cámara Sony A7III` | 1 ms |
| `quiero reservar la sala de edicion 1` | `Sala de edición 1` | 1 ms |
| `reservar el dron dji mavic` | `Dron DJI Mavic` | 1 ms |
| `el kit de luces led` | `Kit de luces LED` | 1 ms |
| `quiero reservar una camara` | (ambiguo: pregunta cuál de 3) | 1 ms |
| `camara canon` | (ambiguo: pregunta EOS R o EOS R6) | 1 ms |
| `quiero reservar algo` | (nada: pregunta qué) | 1 ms |

Iniciar una reserva pasó de **76 s a 10.1 s**, y esos 10.1 s son
enteramente el intento de envío fallido a un número inexistente: la lógica
del bot corre en milisegundos.

---

## 6. Otros defectos encontrados de paso

| Defecto | Efecto con el modelo nuevo | Arreglo |
|---|---|---|
| `generar_respuesta` leía solo `content`, con `timeout=30` y `max_tokens=300` | `lfm2.5-2.6b` gasta ~140 tokens razonando **antes** de escribir; el presupuesto se quedaba corto y el timeout también → respuestas cortadas o mensaje de error | `timeout=120`, `max_tokens=800`, y si `content` llega vacío se usa el texto de respaldo en vez de mandar un mensaje en blanco |
| El modelo escribe markdown (`**Reservar**`) | WhatsApp usa **un** asterisco: `**así**` se ve con los asteriscos literales | `_limpiar_para_whatsapp()` convierte `**x**` → `*x*`, quita encabezados `#` y bloques `<think>` sueltos |
| `"cancelar"` dentro del menú de fallback | Cerraba el menú y sugería escribir *"cancelar mi reserva"*, o sea: pedía escribir otra vez lo que ya se había escrito | El menú tiene opción 3 (Cancelar) y `"cancelar"` la selecciona directamente |
| Responder `"cancelar"` a *"¿sobre qué equipo quieres saber?"* | Se pasaba tal cual a la consulta de disponibilidad, que no encontraba nada | Se enruta al wizard de cancelación |

---

## 7. Qué queda pendiente (a propósito)

1. **La latencia del LLM es irreducible en los 9 de 20 casos que sí lo
   necesitan: 9-30 s.** No se puede apagar el razonamiento (sección 1.1). Si
   esa espera resulta molesta en producción, las salidas reales son ampliar
   `MARCADORES_*` para que más mensajes caigan en la ruta rápida, o un modelo
   sin razonamiento. Bajar `max_tokens` más no sirve: se probó, y solo
   cambia respuestas correctas por fallbacks.
2. **El envío por Baileys cuesta ~10 s cuando falla.** No se investigó, es
   un artefacto del número de pruebas inexistente, pero conviene confirmar
   que con un número real es inmediato.
3. **No hay tests automatizados en el repo.** Todo lo de este documento se
   corrió a mano dentro del contenedor. Las pruebas de la sección 2 (40
   casos, sin red, sin base de datos) son las candidatas obvias a
   convertirse en un `pytest` de verdad: corren en milisegundos y cubren la
   lógica que más fácil se rompe al tocar vocabulario.
4. **`extraer_datos_reserva` quedó sin usar.** Se dejó a propósito (sección
   5). Si en seis meses sigue igual, bórrala.

---

## 8. Qué de esto sobrevive al cambio a una API de terceros

El LLM local es una etapa: el plan es migrar a una API de terceros. Conviene
tener claro qué de lo anterior es arquitectura y qué es andamiaje para las
limitaciones de `lfm2.5-2.6b`, para no conservar de más el día de la
migración.

**Se queda (no tiene que ver con el modelo):**

- El cliente ya habla la API de chat de OpenAI, así que cambiar de proveedor
  es `LLM_BASE_URL` + `LLM_MODEL` + `LLM_API_KEY` en `.env`. No hay nada
  específico de LM Studio en el código.
- Todos los guardrails: rate limiting, filtro de entrada, whitelist de
  intents, y que la disponibilidad, las fechas y las escrituras en
  `reservas` **nunca** salgan del LLM. Un modelo mejor no cambia que no
  quieras que invente una reserva.
- El intent `saludo` y el menú de fallback: son decisiones de producto sobre
  cómo debe sonar el bot, no parches de latencia.
- La ruta rápida sin LLM. Con una API de terceros deja de ser una cuestión
  de segundos y pasa a ser una de plata: cada mensaje que no sale a la red
  es una llamada que no se paga.

**Habría que revisarlo (existe solo por la latencia de este modelo):**

| Decisión | Por qué está | Qué revisar con una API |
|---|---|---|
| `timeout=45` y `max_tokens=800` en clasificación | El modelo se enreda hasta 952 tokens y 72 s | Un modelo hospedado responde en 1-3 s: los topes sobran, y `max_tokens` bajo podría cortar respuestas legítimas |
| Saludo pelado con texto fijo en vez de generado | Generar cuesta 10-20 s | Con 1-2 s de latencia conviene generarlo siempre: suena más natural |
| Fallback por `finish_reason == "length"` | El razonamiento se dispara | Sigue siendo sano dejarlo, pero dejará de dispararse |
| Leer `reasoning_content` cuando `content` viene vacío | Peculiaridad de los modelos que razonan en LM Studio | Casi ninguna API de terceros expone ese campo así; revisar que el fallback no enmascare un error real |

**Lo que sí conviene medir de nuevo el día de la migración:** la batería de
la sección 3 completa. Un modelo distinto puede acertar cosas que este falla
(y al revés), y varias decisiones de arriba se tomaron con números, no con
intuición — reemplazar el modelo sin volver a medir las deja sin respaldo.

---

## Archivos modificados

- `flask-app/app/llm_client.py` — intent `saludo`; ruta rápida sin LLM
  (`_clasificar_sin_llm`); guard holgado por señal de dominio
  (`_validar_intent_de_accion`, reemplaza a `_corroborar_o_heuristico`);
  `_es_charla_social`; `_extraer_intent_de_texto`; `responder_charla`;
  `_limpiar_para_whatsapp`; `max_tokens`/`timeout` acotados.
- `flask-app/app/recursos_service.py` — `listar_terminos_catalogo()` nueva;
  `_iniciar_reserva` ya no llama al LLM.
- `flask-app/app/menu_service.py` — opción 3 (Cancelar); textos más naturales.
- `flask-app/app/routes/webhook.py` — dispatch de `saludo` y de la acción
  `cancelar` del menú; le pasa el catálogo al clasificador.
- `flask-app/app/config.py` y `docker-compose.yml` — `saludo` agregado a
  `ALLOWED_INTENTS`.
- `.env` — `LLM_MODEL=lfm2.5-2.6b` (ya estaba puesto; el contenedor se
  recreó para tomarlo).

No se tocaron `guardrails.py`, `sesiones.py`, `registro_service.py`, el
esquema de Postgres ni el frontend del dashboard.
