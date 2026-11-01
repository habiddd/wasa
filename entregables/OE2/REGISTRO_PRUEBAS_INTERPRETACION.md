# Registro de pruebas de interpretación y correcciones aplicadas

Entregable 3 del Objetivo Específico 2.

## Cómo se probó

Las rondas 1 a 8 las hizo el practicante, de tres formas complementarias. La
ronda 9 la hicieron usuarios piloto (punto 4).

1. **Pruebas en vivo por WhatsApp** con su propio usuario (Habid), escribiendo
   al bot como lo haría un estudiante. Cada ronda dejó una lista de hallazgos
   (F1, F2…) en un plan de corrección.
2. **Pruebas de extremo a extremo por el webhook real** con un usuario de
   prueba (`000000000000`), que recorre el mismo código que un mensaje real,
   pero cuya respuesta no llega a ningún teléfono. La respuesta se lee de
   `auditoria_bot`.
3. **Pruebas automatizadas** (`entregables/OE3/pruebas/verificar_*.py`) que
   fijan cada corrección para que no vuelva a fallar. Se corren dentro del
   contenedor y revierten lo que escriben.
4. **Ronda con usuarios piloto**: cuatro conocidos del practicante le
   escribieron al bot desde su propio WhatsApp; con el practicante, son los
   cinco usuarios piloto del anteproyecto.

Todas se ejecutaron dentro de Docker. Las rondas 1 a 3 usaron el modelo local
`lfm2.5-2.6b`, servido por LM Studio. Las rondas 4 a 8 compararon otros
modelos, locales y por la API de OpenRouter, y terminaron con el cambio a
`deepseek/deepseek-v4-flash-0731`, el modelo en uso desde el 16 de octubre y
también en la ronda 9.

## Ronda 1 — Clasificación de intenciones (9 de septiembre de 2026)

Fuente: `docs/PRUEBAS_CHAT_2026-09-09.md`.

| Qué se midió | Resultado |
|---|---|
| Lógica pura del clasificador (ruta rápida, charla social, señal de dominio, lectura de la respuesta del LLM, respaldo si el LLM cae) | **40/40** |
| Batería de 20 mensajes contra el LLM real | **20/20** (primera corrida: 15/18) |
| Mensajes resueltos sin llamar al modelo | 11 de 20 |
| Peor caso de clasificación | De 150 s a **45 s** acotados |
| Recorrido E2E por el webhook (saludo, fuera de dominio, menú, cancelar, reservar) | 6/6 |

Muestra de la batería:

| Mensaje | Intención | Vía |
|---|---|---|
| `hola` | saludo | rápida |
| `necesito una camara para el viernes` | reservar | LLM |
| `tienen algo para grabar video el jueves?` | consultar_disponibilidad | LLM |
| `ya no voy a necesitar la reserva de manana` | cancelar | LLM |
| `cual es la capital de mongolia` | otro → menú | LLM |
| `me haces un poema sobre el mar` | otro → menú | LLM |

Fallos encontrados y corrección:

| Caso | Diagnóstico | Corrección |
|---|---|---|
| `ya no voy a necesitar la reserva de manana` → `reservar` | Error nuestro: la ruta rápida vio "reserv" y no consultó al modelo, que sí acertaba. | Marcadores de desistimiento (`ya no`, `no voy a`, `desisto`…): con uno presente decide el LLM. En la ronda 6 se quitó la ruta rápida por subcadenas completa. |
| `esto no tiene sentido` → `ayuda` en 72 s | Error del modelo, que razona demasiado y concluye mal. | `max_tokens` de 2000 a 800 y tiempo límite de 150 s a 45 s; al agotarse responde el heurístico, que acierta. |
| `me sirve algo para grabar un podcast el martes` | Expectativa mal planteada: la frase es ambigua y responder con disponibilidad es correcto. | Se corrigió el caso de prueba, no el código. |
| `buenos dias` → `reservar` con `lfm2.5-1.2b` | Sesgo del modelo anterior. | Cambio a `lfm2.5-2.6b`, que lo clasifica bien, y se aflojó la guarda rígida que exigía palabras exactas. |
| Extracción de datos con el LLM tardaba 54–78 s por reserva | Código caro que no cambiaba el resultado. | Se quitó del camino crítico; fecha y hora se interpretan con reglas y se confirman con el usuario. |

## Ronda 2 — Prueba en vivo de reservas (30 de septiembre de 2026)

Fuente: `docs/PLAN_robustez_bot_2026-09-30.md`. Diez pruebas en vivo con el
usuario Habid.

| # | Hallazgo | Corrección |
|---|---|---|
| F1 | "sí, cancelar" no cancelaba: "cancelar" se tomaba como palabra de escape. | Dentro del wizard de cancelación la única palabra de escape es "cerrar". |
| F2 | No entendía horas en palabras ("9 de la mañana", "mediodía", "9 y media"). | Intérprete de horas en lenguaje natural; siempre se confirma. |
| F3 | Rangos de fechas en palabras ("del 7 al 9 de octubre") y "lunes 12 de octubre" mal leídos. | Primero "N de mes", luego el día de la semana; rangos con mes heredado. |
| F4 | Se perdía la fecha y la hora dichas en el primer mensaje. | Se extraen del primer mensaje y se ofrecen para confirmar. |
| F5 | La consulta solo mostraba disponibilidad "ahora" y el "sí" siguiente no arrancaba la reserva. | Consulta con fecha y oferta de reserva que conserva lo pedido. |
| F6 | No se podía cancelar describiendo la reserva. | Cancelación por nombre del activo y fecha. |
| F7 | Se preguntaba por salida de Barranquilla para espacios. | Solo se pregunta por equipos. |
| F8 | El recordatorio decía "30 minutos" aunque faltaran menos. | Minutos reales. |
| F9 | "si" elegía "asignatura" (subcadena). | Coincidencia solo al inicio de palabra. |
| F10 | "el jueves 15" se leía como el próximo jueves. | Gana el número del día. |
| F11 | Cancelar con nombre y fecha que no coincidían no encontraba nada. | Se usa solo el nombre. |
| F12 | En un día no hábil se listaba "libre ese día". | Se muestra el aviso sin contradecirlo. |

## Ronda 3 — Prueba en vivo (1 de octubre de 2026)

Fuente: `docs/PLAN_robustez_bot_ronda3_2026-10-01.md`.

| # | Hallazgo | Corrección |
|---|---|---|
| F13 | "¿Qué reservas tengo?" abría el wizard de reserva. | Se listan las reservas activas sin abrir el wizard. |
| F14 | "si correcto", "si está bien" y raíces de palabras no se reconocían en los menús Sí/No. | `_interpretar_opcion` reconoce sí/no y raíces. |
| F15 | Un rango de horas en el paso de hora de inicio se perdía. | Se toman inicio y fin del mismo mensaje. |
| F16 | Una nota escrita en el mismo "sí" se perdía. | Se guarda la nota. |
| F17 | En el menú de conflicto no se podía cambiar la fecha o la hora. | Opciones para cambiar fecha u hora. |
| F18 | Cancelar con ordinales ("la segunda") o franja del día; el estado de envío podía retroceder. | Ordinales y franja reconocidos; el estado de entrega nunca retrocede. |

## Ronda 4: `qwen3-1.7b` contra la línea base (14 de octubre de 2026)

Fuente: `docs/PRUEBAS_CHAT_QWEN3_2026-10-14.md`. Mismas pruebas de la ronda
1, cambiando solo el modelo.

| Qué se midió | `lfm2.5-2.6b` (9 sep) | `qwen3-1.7b` |
|---|---|---|
| Batería de 20 mensajes | 20/20 | 19/20 |
| Clasificación que llama al modelo | 9.4 a 29.4 s | 1.6 a 4.6 s |
| Estabilidad (9 mensajes × 5 repeticiones) | no medida | 41/45; los 4 fallos son "necesito una camara para el viernes" |
| Respuesta de charla | 10 a 20 s | 2.5 a 3.3 s |
| ¿Se puede apagar el razonamiento? | No | Sí |

## Ronda 5: instrucción del sistema y batería ampliada (15 de octubre de 2026)

Fuente: `docs/PRUEBAS_CHAT_MEJORAS_2026-10-15.md`. Batería de 36 mensajes
(128 clasificaciones con las repeticiones), con pedidos que no dicen
"reservar" y mensajes fuera de tema que usan palabras del dominio.

| Configuración | Aciertos (128) | Mediana | Máximo |
|---|---|---|---|
| `qwen3-1.7b`, código del 9 de septiembre | 103 | 4.3 s | 20.1 s |
| `qwen3-1.7b` con la instrucción corregida | 121 | 3.6 s | 10.3 s |
| `qwen3-4b-2507` con la instrucción corregida | 123 | 0.2 s | 16.1 s (carga del modelo) |

Corrección: reglas y ejemplos en `PROMPT_CLASIFICACION` para cada familia de
error. Ningún pedido real de equipo cae ya al menú de opciones.

## Ronda 6: casos borde (15 de octubre de 2026)

Fuente: `docs/PRUEBAS_CHAT_BORDES_2026-10-15.md`. Con `qwen3-4b-2507`, 51
casos en 9 grupos: fragmentos de palabras engañosos, negaciones, reservas ya
hechas, mezclas, ortografía y jerga, fuera de tema, fragmentos y emoji,
inyección e inglés.

| Capa que decidía | Fallos | Ejemplos |
|---|---|---|
| Ruta rápida por subcadenas | 22 | "aparte de eso, qué hora es" → reservar; "no canceles nada" → cancelar; "mi reserva quedó confirmada?" → reservar |
| Modelo de lenguaje | 1 | "ya devolví el trípode" → cancelar |

| Batería | Mensajes | Antes | Después |
|---|---|---|---|
| Casos borde | 51 | 28 | 50 |
| Casos nuevos, no usados para ajustar | 25 | | 24 |
| Batería ampliada (regresión) | 36 | 35 | 35 |
| De extremo a extremo por el webhook | 6 | | 6 |

Correcciones: la ruta rápida quedó solo para saludos y cortesías; la
instrucción del sistema incluye negaciones, reservas ya hechas, palabras con
otro sentido, inglés y órdenes dentro del mensaje; el filtro de entrada
rechaza prefijos de rol como "SYSTEM:" o "categoría:".

## Ronda 7: sistema en ejecución con seis modelos (15 de octubre de 2026)

Fuente: `docs/PRUEBAS_SISTEMA_MODELOS_2026-10-15.md`. 112 mensajes y una
conversación completa de 23 pasos, contra el webhook real, con el mismo código
para todos los modelos.

| Modelo | Dónde | Batería (112) | Casos nuevos (25) | Tiempo mediano / p90 / máximo | Costo cada 1000 mensajes |
|---|---|---|---|---|---|
| `deepseek/deepseek-v4-flash-0731` | API de pago | 111 | 25 | 1.7 / 5.3 / 19.8 s | USD 0.10 a 0.15 |
| `openai/gpt-oss-20b` | API de pago | 110 | 24 | 4.8 / 18.1 / 31.7 s | USD 0.01 |
| `qwen/qwen3-4b-2507` | Local | 109 | 24 | 0.4 / 2.4 / 3.7 s | Sin costo |
| `nvidia/nemotron-3.5-lightning:free` | API gratuita | 105 | | 8.9 / 20.2 / 46.1 s | Sin costo |
| `liquid/lfm-2.5-2.6b:free` | API gratuita | 102 | | 2.1 / 5.2 / 9.0 s | Sin costo |
| `google/gemma-4-31b-it:free` | API gratuita | 66 (116 errores 429) | | | Sin costo |

Se eligió `deepseek-v4-flash`: el que más acierta, el único sin errores en los
casos nuevos y resistente a órdenes inyectadas, que `lfm` y `nemotron` sí
obedecen. Los 66 de gemma miden el clasificador de respaldo, porque el modelo
casi nunca respondió.

Fallas del sistema encontradas y corregidas:

| # | Hallazgo | Corrección |
|---|---|---|
| S1 y S2 | El menú de opciones interpretaba cualquier frase por fragmentos: "mi reserva quedó confirmada?" abría el flujo de reservar y "gracias, chao" recibía "No reconocí esa opción". | El menú solo entiende un número, el nombre de una opción o una palabra de salida; las demás frases vuelven al clasificador, salvo las que empiezan con un número, que cuentan como opción. |
| S3 | El control de tasa se contaba por proceso: con un tope de 8, pasaban 10 mensajes simultáneos. | Contador en Redis, compartido; en la misma prueba pasan 8. |
| S4 | Los modelos que razonan podían gastar todo el presupuesto y no escribir la respuesta, sin dejar rastro. | Presupuesto de 2000 tokens y aviso en el log. |

## Ronda 8: mensajes adversariales (16 de octubre de 2026)

Con `deepseek-v4-flash`, contra el sistema en ejecución. Detalle en
`docs/SEGURIDAD.md`.

| Qué se probó | Resultado |
|---|---|
| 36 mensajes trampa, dos veces cada uno (suplantación, instrucciones en JSON o en código, caracteres invisibles, escritura deformada, SQL, mensajes de más de 1000 caracteres) | 70/72; el único que falla, "kansela mi rezerba", va al menú |
| Privacidad: preguntar quién reservó un equipo o pedir el teléfono de otro usuario | Ninguna respuesta incluyó datos de otro usuario |
| Reglas del flujo de reserva, incluido el horario de atención | Todas rechazadas; un choque ofrece alternativas |

## Ronda 9: usuarios piloto (28 de octubre de 2026)

Cuatro conocidos del practicante le escribieron al bot desde su propio
WhatsApp entre las 10:28 y las 10:40 a.m., con `deepseek-v4-flash`. El quinto
usuario piloto es el practicante, que hizo las rondas en vivo 2 y 3. Se les
pidió registrarse, y dos de ellos hicieron además una reserva.

El dato verificable del registro es el celular, que viene de la cuenta de
WhatsApp de cada uno. Nombre, código y cédula los escribe el usuario y no se
contrastan con ningún listado; se aceptaron, por ejemplo, el código `222333` y
la cédula `100000000`.

| Participante | Qué hizo | Mensajes | Resultado |
|---|---|---|---|
| Santiago | Registro y reserva de un MacBook M5, 29/10 de 4:00 a 6:00 p.m. | 16 | Reserva n.º 218, pendiente por requerir autorización; el LACS la confirmó desde el back office 41 s después |
| Juancho | Registro | 5 | Registrado |
| Ramiro | Registro y "Quiero reservar" | 6 | No contestó la primera pregunta del flujo; no hubo reserva |
| Pierino | Registro y reserva de la Sala de edición 1, 29/10 de 3:45 a 5:30 p.m. | 17 | Reserva n.º 219, confirmada en el momento |

| Qué se midió | Resultado |
|---|---|
| Intención asignada a los 44 mensajes de los cuatro usuarios | 44/44 correctas |
| Celular cuando WhatsApp entrega un identificador anónimo en vez del número (Santiago y Pierino) | El bot lo pidió y los dos lo escribieron bien |
| Hora de fin fuera del horario de atención (Pierino pidió 6:30 p.m.) | Rechazada; la cambió a 5:30 p.m. |
| Tiempo desde que entra el mensaje hasta que se registra la respuesta | Mediana 0.07 s, promedio 0.35 s, p95 1.8 s, máximo 3.2 s; los 44 envíos llegaron |

| # | Hallazgo | Corrección |
|---|---|---|
| F19 | Pierino empezó con "Espacio" y el bot le mostró el menú completo, con los equipos primero, sin usar lo que ya había dicho. | Si el primer mensaje dice "espacio" o "salón" sin nombrar cuál, el primer menú trae solo los espacios; si dice "equipo", solo los equipos; si dice las dos cosas, el menú completo (`recursos_service._avanzar_wizard`, paso `elegir_activo`). Corregida el 28 de octubre. |

La corrección se probó el mismo día enviando mensajes al webhook real con el
usuario de pruebas `000000000000` (las respuestas no llegan a ningún teléfono):

| Primer mensaje | Antes | Después |
|---|---|---|
| "Espacio" | Menú completo, equipos primero | Solo espacios; la reserva de la Sala de edición 1 se completó |
| "necesito un salón" | Menú completo, equipos primero | Solo espacios |
| "quiero reservar un equipo" | Menú completo | Solo equipos |
| "quiero reservar una sala" | Directo a la Sala de edición 1 | Igual |
| "quiero reservar el estudio" | Directo al Estudio de grabación A | Igual |
| "Quiero reservar" | Menú completo | Igual |

Para salir del flujo de reserva sirven "cancelar" y "cerrar". "salir" solo
funciona en el menú general; dentro del flujo, en el paso de la fecha, se
toma como una fecha mal escrita.

Las capturas del back office (usuarios, reservas y las dos conversaciones,
con teléfonos y cédulas ocultos) están en el informe final, sección 2.5,
figuras 12 a 14.

## Verificación automatizada vigente

Cada hallazgo quedó fijado como caso en
`entregables/OE3/pruebas/verificar_robustez_bot.py`. Última corrida, 28 de
octubre de 2026:

```
OK: F1 a F19 (cancelar con 'sí, cancelar', horas y fechas en palabras, lo dicho en el primer mensaje,
consulta con fecha + oferta, cancelar por descripción, espacios sin Barranquilla, recordatorio con
minutos reales, ver mis reservas, sí/no y raíces, rango de horas, nota en el mismo sí, cambiar
fecha/hora en conflicto, ordinales y franja al cancelar, estado de envío sin retroceso, primer menú
filtrado por espacio, salón o equipo).
```

## Limitaciones de estas pruebas

- La ronda con usuarios piloto fue corta: 44 mensajes de cuatro conocidos del
  practicante en doce minutos. Una ronda más larga con estudiantes del
  Laboratorio mostraría mejor cómo escriben quienes piden equipos de verdad.
- Las rondas 4 a 8 las corrió el practicante por el webhook, con un usuario de
  prueba; no son conversaciones de terceros.
- Con el modelo por API, si OpenRouter no responde o la cuenta se queda sin
  saldo, el bot cae al clasificador de respaldo (66/112) sin un aviso visible.
