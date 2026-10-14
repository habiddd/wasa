# Reporte de pruebas: cambio de `lfm2.5-2.6b` a `qwen/qwen3-1.7b`

**Fecha:** 14 de octubre de 2026
**Alcance:** las mismas pruebas de `PRUEBAS_CHAT_2026-09-09.md` (modelo en
crudo, batería de 20 mensajes, generación de respuestas, extracción y flujo
end-to-end por el webhook), repetidas con el modelo nuevo para comparar.
**Entorno:** todo dentro de Docker (`docker compose build flask-app && docker
compose up -d`, más el reinicio de `dashboard-frontend`), contra el LM Studio
real del host (`http://host.docker.internal:1234`). El contenedor confirma
`LLM_MODEL=qwen/qwen3-1.7b`. No se ejecutó nada en el host.

**Qué se cambió:** solo `LLM_MODEL` en `.env`. No se tocó código, prompts,
`max_tokens` ni timeouts: todo lo que sigue mide al modelo nuevo con el
código tal como lo dejó la sesión del 9 de septiembre.

---

## Resumen ejecutivo

| Qué se midió | `lfm2.5-2.6b` (9 sep) | `qwen/qwen3-1.7b` (hoy) |
|---|---|---|
| Batería de 20 mensajes (bot real) | **20/20** | **19/20** |
| Mensajes que ni tocan el LLM | 11 de 20 | 11 de 20 (misma ruta rápida) |
| Clasificación que sí llama al LLM | 9.4 – 29.4 s | **1.6 – 4.6 s** |
| Peor caso de clasificación | 45 s (tope) | 9.6 s en el bot; 16 s una vez en E2E |
| Estabilidad (9 mensajes × 5 repeticiones) | no medida | **41/45**: los 4 fallos son el mismo mensaje |
| Respuesta de charla (`responder_charla`) | 10 – 20 s | **2.5 – 3.3 s** |
| `extraer_datos_reserva` (fuera del camino crítico) | 54 – 78 s | **6.5 – 10.3 s** |
| ¿Se puede apagar el razonamiento? | No (3 intentos) | **Sí**: `/no_think` o `reasoning_effort: "none"` → 0 tokens, ~0.3 s |

En una línea: **qwen3 es entre 4 y 8 veces más rápido, pero menos estable
en el caso ambiguo más común** ("necesito una cámara para el viernes"), que
con este modelo cae al menú de fallback más o menos la mitad de las veces.

Tres hallazgos:

1. **El razonamiento de qwen3 sí se puede apagar** desde la API. Con
   lfm2.5 eso no funcionaba, y casi todas las decisiones de latencia del
   código vienen de ahí (sección 1.1). No se aplicó: es un cambio de código,
   y el pedido era solo medir.
2. **`"necesito una camara para el viernes"` es inestable**: con
   `temperature: 0`, la misma llamada da `reservar`, `consultar_disponibilidad`
   u `otro` según la corrida. `otro` manda al usuario al menú de fallback.
   Es el modelo, no nuestro parseo (sección 3.2).
3. La calidad del texto generado bajó: mezcla tú/usted, frases raras y
   listas con viñetas aunque el prompt las prohíbe (sección 4).

---

## 1. El modelo en crudo

Mismo prompt de clasificación (`PROMPT_CLASIFICACION`), `temperature: 0`,
`max_tokens: 800`, llamando al endpoint directo desde el contenedor. Antes
se hizo una llamada para cargar el modelo en memoria, que no se contó.

| Modelo | Mensaje | Tiempo | Tokens de razonamiento | Respuesta |
|---|---|---|---|---|
| `lfm2.5-2.6b` (9 sep) | `"buenos dias"` | 25.6 s | 189 | `otro` ✅ |
| `lfm2.5-2.6b` (9 sep) | `"necesito una camara para el viernes"` | 15.0 s | 182 | `reservar` ✅ |
| `qwen/qwen3-1.7b` | `"buenos dias"` | 0.9 s | 0 | `saludo` ✅ |
| `qwen/qwen3-1.7b` | `"necesito una camara para el viernes"` | 6.6 s | 347 | `reservar` ✅ |

Un detalle: con mensajes cortos y obvios (`hola`, `ayuda`, `cancela mi
reserva`) qwen3 decide por su cuenta no razonar y contesta en 0.2 s. Solo
razona cuando el mensaje tiene algo que pensar, y ahí gasta entre 66 y 430
tokens.

### 1.1 El razonamiento sí se puede apagar

Mismo mensaje (`"necesito una camara para el viernes"`), los mismos intentos
que en la sesión anterior, más `/no_think`, que es la forma que documenta
Qwen3 (la anterior probó `/nothink`, que es la de otros modelos):

| Intento | `lfm2.5-2.6b` (9 sep) | `qwen/qwen3-1.7b` | Tokens raz. (qwen3) | Respuesta (qwen3) |
|---|---|---|---|---|
| Sin nada (baseline) | 16.5 s | 7.0 s | 331 | `consultar_disponibilidad` |
| `chat_template_kwargs: {"enable_thinking": false}` | 17.1 s | 8.8 s | 427 | `reservar` |
| `/no_think` al final del system prompt | — | **0.4 s** | **0** | `consultar_disponibilidad` |
| `/nothink` al final del system prompt | 13.1 s | **0.3 s** | **0** | `consultar_disponibilidad` |
| `reasoning_effort: "none"` | 13.8 s | **0.2 s** | **0** | `consultar_disponibilidad` |
| `reasoning_effort: "low"` | 12.8 s | 8.3 s | 405 | `consultar_disponibilidad` |

**Tres de los intentos funcionan con qwen3.** `enable_thinking: false` no
(LM Studio lo ignora igual que antes), y `reasoning_effort: "low"` tampoco
recorta nada.

Fíjate en la primera fila: el baseline respondió `consultar_disponibilidad`,
y en la tabla de arriba, con la misma llamada, había respondido `reservar`.
Eso es lo de la sección 3.2.

---

## 2. Pruebas de lógica pura

No se repitieron, porque no dependen del modelo: `_clasificar_sin_llm`,
`_es_charla_social`, `_tiene_senal_de_dominio`, `_extraer_intent_de_texto`,
`_limpiar_para_whatsapp` y el heurístico son código, y el código no
cambió. Siguen sin estar en un `pytest` (pendiente 3 del reporte anterior).

---

## 3. Clasificación contra el LLM real — 19/20

Con `qwen/qwen3-1.7b`, dentro del contexto de Flask, pasándole los 52
términos reales del catálogo (eran 29 el 9 de septiembre: el catálogo
creció). Además de la clasificación del bot se le preguntó al modelo en
crudo, con y sin razonamiento, para separar lo que aporta la ruta rápida de
lo que aporta el modelo.

| Mensaje | Bot | Vía | Tiempo bot (antes) | Tiempo bot (hoy) | Crudo razonando | Crudo `/no_think` |
|---|---|---|---|---|---|---|
| `hola` | `saludo` ✅ | rápido | 0.0 s | 0.0 s | ✅ 0.2 s | ✅ 0.2 s |
| `buenos dias` | `saludo` ✅ | rápido | 0.0 s | 0.0 s | ✅ 0.2 s | ✅ 0.2 s |
| `como estas?` | `saludo` ✅ | rápido | 0.0 s | 0.0 s | ✅ 0.2 s | ✅ 0.2 s |
| `quien eres?` | `saludo` ✅ | rápido | 0.0 s | 0.0 s | ❌ repite `"quien eres"` | ❌ repite `"quien eres"` |
| `gracias, muy amable` | `saludo` ✅ | LLM | 19.3 s | **0.2 s** | ✅ 0.2 s | ✅ 0.2 s |
| `que puedes hacer` | `ayuda` ✅ | rápido | 0.0 s | 0.0 s | ✅ 0.2 s | ✅ 0.2 s |
| `ayuda` | `ayuda` ✅ | rápido | 0.0 s | 0.0 s | ✅ 0.2 s | ✅ 0.3 s |
| `necesito una camara para el viernes` | `consultar_disponibilidad` ❌ | LLM | 9.4 s | **4.3 s** | ✅ 7.0 s | ❌ `consultar_disponibilidad` 0.4 s |
| `quiero reservar la sala de edicion 1` | `reservar` ✅ | rápido | 0.0 s | 0.0 s | ✅ 3.0 s | ✅ 0.3 s |
| `me apartas el dron el lunes a las 3pm` | `reservar` ✅ | rápido | 0.0 s | 0.0 s | ✅ 4.1 s | ✅ 0.3 s |
| `tienen algo para grabar video el jueves?` | `consultar_disponibilidad` ✅ | LLM | 13.3 s | **2.5 s** | ✅ 4.2 s | ✅ 0.3 s |
| `que tienen disponible` | `consultar_disponibilidad` ✅ | rápido | 0.0 s | 0.0 s | ✅ 0.3 s | ✅ 0.3 s |
| `quiero saber si hay una camara libre` | `consultar_disponibilidad` ✅ | LLM | 19.9 s | **1.6 s** | ✅ 1.5 s | ✅ 0.3 s |
| `y el dron dji esta libre?` | `consultar_disponibilidad` ✅ | rápido | 0.0 s | 0.0 s | ✅ 2.6 s | ✅ 0.3 s |
| `cancela mi reserva` | `cancelar` ✅ | rápido | 0.0 s | 0.0 s | ✅ 0.2 s | ✅ 0.2 s |
| `ya no voy a necesitar la reserva de manana` | `cancelar` ✅ | LLM | 16.8 s | **3.2 s** | ✅ 3.6 s | ✅ 0.2 s |
| `cual es la capital de mongolia` | `otro` ✅ | LLM | 15.0 s | **4.2 s** | ✅ 4.6 s | ✅ 0.2 s |
| `asdfgh` | `otro` ✅ | LLM | 26.7 s | **0.2 s** | ✅ 3.0 s | ✅ 0.2 s |
| `esto no tiene sentido` | `otro` ✅ | LLM | 45.0 s (tope) | **2.9 s** | ✅ 2.9 s | ✅ 0.2 s |
| `me haces un poema sobre el mar` | `otro` ✅ | LLM | 29.4 s | **2.6 s** | ✅ 2.7 s | ✅ 0.2 s |

**Bot: 19/20.** Crudo razonando: 19/20. Crudo sin razonar: 18/20.

- **`"esto no tiene sentido"` ya no es un problema.** Con lfm2.5 era el caso
  que se enredaba (952 tokens, 72 s, respuesta mala) y que justificó bajar
  el timeout a 45 s. Con qwen3 le toma 139 tokens y 2.9 s, y acierta.
- **`"quien eres?"` lo salva la ruta rápida.** qwen3 no clasifica esa
  pregunta, repite el mensaje (`"quien eres"`), y `_extraer_intent_de_texto`
  no encuentra ninguna categoría. Como `_clasificar_sin_llm` lo resuelve
  antes, el bot nunca se lo pregunta al modelo.

### 3.1 Estabilidad: los 9 mensajes que sí llegan al LLM, 5 veces cada uno

La sesión anterior corrió cada mensaje una sola vez. Como en la sección 1.1
una misma llamada dio dos respuestas distintas, aquí se repitió cada
mensaje que pasa por el LLM 5 veces, siempre por `clasificar_intent`:

| Mensaje | Esperado | Aciertos | Respuestas | Tiempo |
|---|---|---|---|---|
| `gracias, muy amable` | `saludo` | 5/5 | `saludo` ×5 | 0.1 – 2.1 s |
| `necesito una camara para el viernes` | `reservar` | **1/5** | **`otro` ×3**, `consultar_disponibilidad` ×1, `reservar` ×1 | 3.5 – 9.6 s |
| `tienen algo para grabar video el jueves?` | `consultar_disponibilidad` | 5/5 | — | 2.4 – 2.5 s |
| `quiero saber si hay una camara libre` | `consultar_disponibilidad` | 5/5 | — | 1.5 – 1.8 s |
| `ya no voy a necesitar la reserva de manana` | `cancelar` | 5/5 | — | 3.0 – 3.7 s |
| `cual es la capital de mongolia` | `otro` | 5/5 | — | 3.9 – 4.6 s |
| `asdfgh` | `otro` | 5/5 | — | 2.7 – 3.5 s |
| `esto no tiene sentido` | `otro` | 5/5 | — | 3.6 – 4.8 s |
| `me haces un poema sobre el mar` | `otro` | 5/5 | — | 2.6 – 2.9 s |

**41/45. Mediana 3.0 s, máximo 9.6 s.** Ocho de los nueve mensajes son
perfectamente estables. Los cuatro fallos son todos el mismo mensaje.

### 3.2 Por qué falla `"necesito una camara para el viernes"`

Se le preguntó al endpoint directo seis veces seguidas, guardando el
razonamiento, para separar "el modelo se equivoca" de "nuestro código se
equivoca":

| Corrida | Tiempo | `finish_reason` | Respuesta | Final del razonamiento |
|---|---|---|---|---|
| 0 | 15.9 s | `length` | (vacía) | *"…maybe it's otro. Wait, but… If the user is asking to reserve something, it's reservar. If they're just stating a need"* (cortado) |
| 1 | 4.7 s | `stop` | `consultar_disponibilidad` | *"…it's more about availability."* |
| 2 | 10.1 s | `stop` | `otro` | *"…they just said 'necesito', which is more about needing, not necessarily reservation. So I think it's otro."* |
| 3 | 7.7 s | `stop` | `reservar` | *"…the action of wanting to use equipment implies reservation."* |
| 4 | 11.9 s | `stop` | `otro` | *"The user is not asking to reserve, cancel, get help, or social."* |
| 5 | 11.5 s | `stop` | `otro` | *"The safest bet might be 'otro'…"* |

**Es el modelo.** Razona en inglés y duda de verdad entre las tres
categorías; `temperature: 0` no lo vuelve determinista en LM Studio. Cuando
agota los 800 tokens (corrida 0), el código hace lo previsto: cae al
heurístico, que acierta `reservar` por la palabra "necesito". Eso explica
por qué este mensaje, cuando acierta, suele ser también el más lento.

Lo que preocupa es el `otro`. El guard actual
(`_validar_intent_de_accion`) solo protege en una dirección: impide que el
LLM arranque un wizard sobre un mensaje sin señal de dominio. **No hace
nada en la dirección contraria**: si el LLM dice `otro` a un mensaje lleno
de señales de dominio ("necesito", "camara", "viernes"), se le cree, y el
usuario recibe el menú de fallback. Con lfm2.5 eso no pasaba porque el
modelo acertaba; con qwen3 se ve. En el E2E (sección 5) pasó una vez de
dos.

---

## 4. Respuestas generadas por el LLM

| Llamada | Mensaje | `lfm2.5-2.6b` (9 sep) | `qwen/qwen3-1.7b` |
|---|---|---|---|
| `responder_charla` | `como estas?` | 10 – 20 s | 3.3 s |
| `responder_charla` | `quien eres?` | 10 – 20 s | 2.5 s |
| `responder_charla` | `gracias, muy amable` | 10 – 20 s | 2.9 s |
| `responder_charla` | `buenas tardes, todo bien?` | 10 – 20 s | 2.9 s |
| `generar_respuesta` | `ayuda` | — | 3.7 s |
| `generar_respuesta` | `que puedes hacer` | — | 5.3 s |

Ninguna cayó al texto de respaldo, o sea que el modelo siempre llegó a
escribir una respuesta. **La velocidad mejora mucho; el texto, no tanto:**

| Mensaje | Lo que respondió qwen3 | Problema |
|---|---|---|
| `como estas?` | *"¡Hola! ¿Cómo te va? Puedo ayudarte a reservar un equipo o espacio, decir**le** si algo está disponible…"* | Mezcla tú y usted en la misma frase |
| `gracias, muy amable` | *"Gracias por ser tan amable! ¿Podrías decirme si algo está disponible o si necesitas cancelar una reserva?"* | Le devuelve las gracias y le pregunta al **usuario** si algo está disponible: los roles quedan al revés |
| `como estas?` (E2E) | *"Hola, cómo estás? Puedo ayudarte a reservar un equipo o espacio. ¿Qué tal te gustaría hacer?"* | "¿Qué tal te gustaría hacer?" no es español natural |
| `cerrar` (E2E) | *"¡Hasta pronto! ¿Podrías cerrar la conversación o indicarme si necesitas ayuda con algo más? 🎞️"* | Le pide al usuario que cierre la conversación |
| `quien eres?` | *"Hola, soy el asistente de reservas de la LACS (Centro de Producción Audiovisual) de Uninorte. ¿En qué puedo ayudarte hoy? 🎬✨"* | Bien |
| `ayuda` | *"Puedo ayudarte con:\n- Reservar equipos/espacios\n- Consultar disponibilidad\n…\nNo puedo inventar disponibilidad…"* | Lista con viñetas, aunque `SYSTEM_PROMPT` dice "No uses markdown ni listas con viñetas"; y la línea sobre "no inventar disponibilidad" se le escapó del prompt al usuario |

Ninguna de estas respuestas es dañina (no inventa reservas ni
disponibilidad), pero suenan peor que las de lfm2.5. Es lo esperable en
un modelo de 1.7B, más chico que el de 2.6B.

---

## 5. Pruebas end-to-end por el webhook real

`POST /webhook/whatsapp` desde dentro de `lacs_flask_app`, usuario de
pruebas `000000000000`, 8.5 s entre mensajes, leyendo `auditoria_bot` y la
sesión de Redis después de cada paso. La columna `latencia_ms` de
`auditoria_bot` (nueva desde el reporte anterior) da el tiempo total del
bot; los ~10 s de cada fila son, como antes, el intento de envío fallido a
un número que no existe en WhatsApp (`estado_envio = error_http`). La
última columna resta esos 10 s.

### 5.1 El mismo recorrido del reporte anterior

| Mensaje | `status` | `intent` auditado | Respuesta | Lógica + LLM |
|---|---|---|---|---|
| `hola` | `error_envio` | `saludo` | *"¡Hey! 👋 Soy el bot de reservas de la LACS…"* (texto fijo) | ~0.3 s |
| `cual es la capital de mongolia` | `menu_fallback` | `menu_fallback` | El menú de tres opciones | ~5.0 s |
| `3` (dentro del menú) | `error_envio` | `cancelar` | *"No tienes reservas activas para cancelar."* | ~0 s |
| `cerrar` | `error_envio` | `saludo` | Respuesta de charla generada (ver sección 4) | ~3.1 s |
| `quiero reservar una camara canon eos r` | `error_envio` | `reservar` | Activo resuelto (`Cámara Canon EOS R`), pregunta particular/asignatura | ~0.3 s |
| `cancelar` (dentro del wizard) | `error_envio` | `reservar` | *"Reserva cancelada, no se guardó nada…"* | ~0 s |

El flujo se comporta igual que el 9 de septiembre. Las dos filas que
difieren no tienen que ver con el modelo:

- `3` → *"No tienes reservas activas"*: la base se reconstruyó el 30 de
  septiembre y el usuario de pruebas ya no tiene las reservas 47–49 (hoy
  tiene la 2 y la 3, en `no_show`, que no son cancelables). Por eso el
  wizard de cancelación no llega a abrirse.
- `cerrar` → `saludo`: como no había wizard abierto, "cerrar" llegó como
  mensaje suelto y el LLM lo tomó como una despedida. Es razonable.

La sesión quedó limpia después de cada salida
(`{"menu_fallback": null, "cancelacion_wizard": null, "reserva_wizard": null}`),
y **las reservas del usuario de pruebas no cambiaron**: `[(2, no_show),
(3, no_show)]` antes y después.

### 5.2 Los tres mensajes que pasan por el LLM, con sesión limpia

En la primera corrida, `"necesito una camara para el viernes"` cayó al menú
de fallback, y como ese menú quedó abierto, los dos mensajes siguientes se
interpretaron dentro de él. Para medirlos sin esa contaminación se
repitieron borrando la sesión antes de cada uno:

| Mensaje | Corrida 1 (sesión heredada) | Corrida 2 (sesión limpia) |
|---|---|---|
| `necesito una camara para el viernes` | `menu_fallback` en ~6.6 s ❌ | `reservar` en ~16.2 s ✅ (wizard con `subcategoria: cámara`, `prefill.fecha: "el viernes"`) |
| `tienen algo para grabar video el jueves?` | (dentro del menú) | `consultar_disponibilidad` en ~2.4 s ✅: *"esto te puede servir: Trípode Manfrotto: libre ese día"* |
| `como estas?` | (dentro del menú) | `saludo` en ~3.0 s ✅ |

Es la sección 3.2 en vivo: el mismo mensaje fue una vez al menú (rápido) y
otra vez al wizard (lento, porque el modelo agotó tokens y decidió el
heurístico).

---

## 6. `extraer_datos_reserva` (sigue fuera del camino crítico)

Se midió solo para comparar, porque desde el 9 de septiembre el bot no la
llama. Hoy es miércoles 14 de octubre.

| Mensaje | `lfm2.5-2.6b` (9 sep) | `qwen/qwen3-1.7b` | Devolvió (qwen3) |
|---|---|---|---|
| `quiero reservar una camara` | 54.2 s, todo `null` ✅ | 6.5 s | `Canon EOS R`, `2026-10-14 17:21` → `18:21` ❌ |
| `necesito la camara canon eos r el viernes a las 3pm por 2 horas` | 64.8 s ✅ | 10.3 s | `Cámara Canon EOS R`, `2026-10-16 15:00` → `17:00` ✅ |
| `resérvame el dron mañana de 9 a 11` | 77.6 s, fecha con un mes de más ❌ | 7.6 s | `Dron DJI Mavic`, `2026-10-15 09:00` → `11:00` ✅ |

qwen3 acierta las fechas relativas que lfm2.5 erraba, y es de 6 a 10 veces
más rápido. Pero ante un pedido vago **inventa** equipo y horario (la
primera fila: el usuario no dijo cuál cámara ni a qué hora). Con eso se
sostiene la decisión de no sacar fechas ni recursos del LLM.

---

## 7. Qué de las decisiones del 9 de septiembre cambia con este modelo

Varias decisiones de `llm_client.py` se tomaron por la latencia y el
razonamiento de lfm2.5. Revisadas contra los números de hoy (no se tocó
ninguna):

| Decisión | Por qué se tomó | Con qwen3 |
|---|---|---|
| `timeout=45` en clasificación | Toda clasificación correcta llegaba en ≤ 30 s | Sobra: el máximo medido fue 16 s, y sigue siendo inofensivo |
| `max_tokens=800` en clasificación | lfm2.5 se enredaba hasta 952 tokens | Sigue sirviendo: qwen3 lo agotó 1 vez de 6 con el mensaje ambiguo, y el heurístico acertó |
| Ruta rápida sin LLM | 13–25 s por llamada | Ya no es por velocidad (2–4 s), pero sigue tapando un fallo real del modelo (`"quien eres?"`) |
| Saludo pelado con texto fijo | Generar costaba 10–20 s | Generar cuesta ~3 s, pero el texto fijo suena mejor que lo que genera qwen3 (sección 4) |
| Docstrings que dicen "no se puede apagar el razonamiento" | Medido con lfm2.5 | **Ya no es cierto** con qwen3 (sección 1.1) |

---

## 8. Pendientes y recomendaciones (nada de esto se aplicó)

1. **Decidir qué hacer con el `otro` sobre mensajes con señal de dominio.**
   Es el único fallo de la batería y le pasa al caso de uso más típico. La
   salida más barata es un guard simétrico al que ya existe: si el LLM
   responde `otro` y `_tiene_senal_de_dominio(...)` es verdadero, usar
   `_clasificar_intent_heuristico`, que para este mensaje da `reservar`.
   Son unas tres líneas en `clasificar_intent`. Habría que volver a correr
   las secciones 3 y 3.1 después.
2. **Evaluar `/no_think` (o `reasoning_effort: "none"`) en la
   clasificación.** Bajaría cada llamada de 2–10 s a ~0.3 s. Sin
   razonamiento, el modelo crudo sacó 18/20, con los mismos dos fallos que ya
   cubren la ruta rápida (`quien eres?`) y el punto 1 (`necesito una
   camara…` → `consultar_disponibilidad`, que al menos no lleva al menú). No
   se midió la estabilidad sin razonamiento; habría que repetir la sección
   3.1 en ese modo antes de decidir.
3. **Calidad del texto de charla y ayuda.** Si se queda qwen3, conviene
   reforzar en `PROMPT_CHARLA` que hable siempre de tú, y en `SYSTEM_PROMPT`
   que no muestre las reglas internas. Las viñetas en WhatsApp se leen bien,
   así que eso es menor.
4. **Actualizar los docstrings de `llm_client.py`** que dicen que el modelo
   es `lfm2.5-2.6b` y que el razonamiento no se puede apagar, si se decide
   quedarse con qwen3.
5. **Siguen abiertos del reporte anterior:** que no haya tests automatizados
   (pendiente 3) y que `extraer_datos_reserva` esté sin usar (pendiente 4).

---

## Archivos modificados

Ninguno, salvo este reporte. El cambio de modelo (`LLM_MODEL=qwen/qwen3-1.7b`
en `.env`) lo hizo el usuario; para estas pruebas solo se reconstruyó
`flask-app` y se recrearon los contenedores. Los scripts de prueba se
corrieron con `docker exec -i lacs_flask_app python -` y no quedaron en el
repo.
