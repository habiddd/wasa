# Registro de pruebas de interpretación y correcciones aplicadas

Entregable 3 del Objetivo Específico 2.

## Cómo se probó

Las pruebas las hizo el practicante, de tres formas complementarias:

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

Todas se ejecutaron dentro de Docker, contra el modelo de lenguaje local real
(`lfm2.5-2.6b` servido por LM Studio).

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
| `ya no voy a necesitar la reserva de manana` → `reservar` | Error nuestro: la ruta rápida vio "reserv" y no consultó al modelo, que sí acertaba. | Marcadores de desistimiento (`ya no`, `no voy a`, `desisto`…): con uno presente decide el LLM. |
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

## Verificación automatizada vigente

Cada hallazgo quedó fijado como caso en
`entregables/OE3/pruebas/verificar_robustez_bot.py`. Última corrida, 1 de
octubre de 2026:

```
OK: F1 a F18 (cancelar con 'sí, cancelar', horas y fechas en palabras, lo dicho en el primer mensaje,
consulta con fecha + oferta, cancelar por descripción, espacios sin Barranquilla, recordatorio con
minutos reales, ver mis reservas, sí/no y raíces, rango de horas, nota en el mismo sí, cambiar
fecha/hora en conflicto, ordinales y franja al cancelar, estado de envío sin retroceso).
```

## Limitaciones de estas pruebas

- Las pruebas en vivo las hizo una sola persona (el practicante). Falta una
  ronda con estudiantes del Laboratorio para medir la interpretación de
  mensajes escritos por terceros.
- La latencia del modelo local (13–25 s cuando hay que consultarlo) es una
  condición del hardware de pruebas. Por eso se reduce el número de mensajes
  que llegan al modelo.
