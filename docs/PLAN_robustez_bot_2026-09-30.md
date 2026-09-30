# Plan: errores y robustez del bot tras la prueba E2E (2026-09-30)

Origen: 10 pruebas en vivo con Habid (resultado en la conversación de hoy).
Todo en `flask-app/app/recursos_service.py` salvo donde se indica. Regla de
oro que se mantiene: **fecha y hora nunca las inventa el LLM**; todo lo que
se extraiga por texto libre es una *interpretación* y pasa por los mismos
pasos de confirmación que ya existen ("Entendí jueves 01/10/2026. ¿Es
correcto?"). Nada de LLM nuevo en el camino.

## F1 — Error: "sí, cancelar" no cancela (prioridad alta)

`gestionar_cancelacion` (≈línea 3779) corta el wizard de cancelación si el
texto contiene cualquier palabra de `PALABRAS_CANCELAR` = {"no", "cancelar",
"cancela", "cerrar"}. Dentro del wizard de *cancelación*, "cancelar" es la
respuesta afirmativa. Cambio: ahí la única palabra de escape es `"cerrar"`
(es la que anuncia el propio mensaje: 'o "cerrar" para no cancelar nada').
"no" en el paso `confirmar_cancelacion` ya lo resuelve `_interpretar_opcion`
contra `OPCIONES_CONFIRMAR_CANCELACION`; verificar que "si, cancelar",
"sí", "si", "dale", "no", "no gracias" se interpretan bien ahí (si
`_interpretar_opcion` no alcanza, usar `_interpretar_si_no`, que ya existe).

## F2 — Horas en palabras

`_interpretar_hora_individual_relajada` debe aceptar además:
- "9 de la mañana", "9 de la manana", "9 en la mañana" → am
- "2 de la tarde", "7 de la noche" → pm
- "mediodía"/"medio dia" → 12:00; "12 del mediodía" → 12:00
- "9 y media" → 9:30; "9 y cuarto" → 9:15 (con el periodo si viene)
- Hora sin am/pm entre 1 y 6 → pm (la LACS no abre de madrugada). 7–11 sin
  periodo → am como hoy. Siempre es interpretación → se confirma.

## F3 — Fechas y rangos en palabras

- `_interpretar_rango_fecha_natural`: si el lado izquierdo es solo un día
  ("7", "el 7") y el derecho trae mes ("9 de octubre"), el izquierdo toma mes
  y año del derecho. Casos: "del 7 al 9 de octubre", "entre el 7 y el 9 de
  octubre", "del 7 de octubre al 9 de octubre", "de mañana a pasado mañana".
- `_interpretar_fecha_natural`: hoy busca el nombre del día ANTES que "N de
  mes", así que "lunes 12 de octubre" devuelve el próximo lunes (05/10), no
  el 12. Invertir: primero `N de <mes>` (con `re.search`, no `match`, para
  aceptar "el lunes 12 de octubre", "para el 12 de octubre"), luego el día de
  la semana. Aceptar también artículos/relleno: "el", "este", "para el",
  "próximo".

## F4 — El primer mensaje no pierde lo que ya dijo

`_iniciar_reserva` hoy solo usa el activo. Nueva función pura
`_extraer_detalles_de_texto(texto, ahora_local) -> dict` que busca en el
texto libre, con regex (reusando F2/F3), y devuelve fragmentos de texto:
`{"fecha": "mañana" | "5 de octubre" | "07/10/2026 a 09/10/2026" | ...,
"hora_inicio": "9 de la mañana", "hora_fin": "11 de la mañana"}` (claves
ausentes si no hay). Patrones:
- Fecha: dd/mm/aaaa (y rango con "a"), "hoy", "pasado mañana", "mañana"
  **que no sea "de la mañana"/"en la mañana"**, "(el|este|próximo) <día>",
  "<día> N de <mes>", "N de <mes>", "del N al M de <mes>".
- Horas: "de X a Y [periodo]" (el periodo final aplica a ambas si ninguna
  trae uno), "desde las X hasta las Y", "a las X" (solo inicio), "X a Y".
  Cuidado de no confundir "de 9 a 11" con un rango de fechas.

Uso: se guardan en el wizard como `prefill` (dict). Cuando `_avanzar_wizard`
llega al paso `fecha` / `hora_inicio` / `hora_fin` y hay prefill para ese
paso, en vez de preguntar se hace `pop` del fragmento y se procesa **con la
misma lógica que `_continuar_reserva` usa para ese paso**, como si el usuario
lo hubiera escrito (validación de festivos, sábados, plazo del seguro,
choque, y la pregunta "¿Es correcto?" de las interpretaciones). Si falla, se
muestra el error y la pregunta normal de ese paso (el prefill ya se
consumió; no se reintenta). Lo más limpio probablemente es factorizar el
cuerpo de cada paso de `_continuar_reserva` en una función que ambos llamen;
elegir lo de menor diff.

También en el primer mensaje:
- **Varios activos** ("el micrófono rode y el trípode"): partir el texto por
  " y " / "," / " con " y resolver cada parte con
  `_resolver_recurso_para_reserva`. Si ≥2 partes resuelven a recursos
  distintos (o a subcategorías distintas), el primero sigue como hoy y el
  resto va a `wizard["por_agregar"]` (lista de textos). Cuando un ítem
  termina de encajarse en el carrito (`_mover_item_resuelto_al_carrito`) y
  `por_agregar` no está vacío, se arranca el siguiente como si el usuario
  hubiera elegido "Agregar otro activo" y escrito ese texto.
- **Accesorios excluidos** ("sin el lente de 100-500", "sin lentes"): en el
  paso `confirmar_accesorios`, si el texto inicial (guardar
  `wizard["texto_inicial"]`) tiene "sin <algo>", los accesorios cuyo nombre
  coincide con ese fragmento (`_puntaje_nombre_en_texto` > 0; "sin lentes" =
  todos) se quitan solos y el mensaje lo dice ("Quité Lente Canon RF
  100-500mm como pediste."). El paso se salta si ya quedó resuelto.

## F5 — Consulta de disponibilidad con fecha + el "sí" que sigue

- `evaluar_consulta_disponibilidad`: si el texto trae fecha (F4), y ese día
  no es hábil o está bloqueado → anteponer el mismo motivo que da el wizard
  ("No se pueden hacer reservas para el sábado 03/10/2026: la LACS no presta
  servicio los sábados. La siguiente fecha disponible es…"; reusar la
  función que genera ese texto). Si es hábil → el estado de cada activo es
  para ese día (libre / ocupado en parte, con `ids_ocupados_en_rango` sobre
  el horario del día), no el de hoy.
- `menu_service.activar_oferta(sesion, respuesta, texto_consulta)`: guarda
  el texto de la consulta en la oferta; al aceptar ("sí"), el payload que va
  a `gestionar_reserva` es ese texto original (ej. "me prestan las luces led
  este sábado?"), no "sí" — así F4 arranca con el activo y la fecha. Ajustar
  las dos llamadas en `routes/webhook.py`.

## F6 — Cancelar por descripción

`_iniciar_cancelacion(texto, …)`: filtrar los bundles con el texto: nombres
de recursos que aparecen (`_puntaje_nombre_en_texto`) y/o fecha (F4). Si
queda exactamente uno → ir directo a `confirmar_cancelacion` con ese. Si
quedan varios → listar solo esos. Si ninguno o el texto no dice nada → lista
completa como hoy.

## F7 — Espacios no "salen de Barranquilla"

Si todos los ítems del carrito son espacios (`tipo == "espacio"`), el paso
`sale_de_barranquilla` se salta con `False` y se marca
`sale_de_barranquilla_auto = True`. Si luego se agrega un equipo y el valor
era automático, se vuelve a `None` para que se pregunte. En el resumen de una
reserva solo de espacios no mostrar la línea "Sale de Barranquilla".

## F8 — Recordatorio con los minutos reales

`mensaje_aviso_previo`: minutos = redondeo de `(inicio - ahora)` en minutos
(mínimo 1), no la constante. Si faltan ≥ AVISO_PREVIO_MIN usar la constante.

## Ronda 2 (hallado en la prueba en vivo después de F1–F8)

- **F9 — `_interpretar_opcion` compara subcadenas sueltas.** Con el menú
  "1. Particular / 2. Para una asignatura", responder "si" elige asignatura
  porque "si" está dentro de "a*si*gnatura" (y "no" dentro de "ca*no*n", que
  ya motivó un comentario en `gestionar_reserva`). Cambio: la coincidencia
  por texto del usuario dentro de la etiqueta solo vale al **inicio de una
  palabra** (`re.search(rf"\b{re.escape(texto_norm)}", etiqueta_norm)`), así
  "micro" sigue encontrando "Micrófono" pero "si" no entra en "asignatura".
  La otra dirección (etiqueta dentro del texto) queda igual. Revisar que
  ningún menú existente dependa de una coincidencia a mitad de palabra.
  En el paso tipo_uso, un "si"/"no" suelto no es ninguna de las dos opciones
  → "No reconocí esa opción" (no adivinar).
- **F10 — "del jueves 15" (día de la semana + número sin mes).** Hoy
  `_PATRONES_FECHA_TEXTO` toma "del jueves" = próximo jueves e ignora el 15.
  Reconocer "<día> N" sin mes (y "el 15" solo) como el próximo día N del
  mes (este mes si N ≥ hoy, si no el siguiente), en el extractor y en
  `_interpretar_fecha_natural`. Sin confundirlo con horas: no aplica si el
  número va seguido de ":", "am/pm", "de la <periodo>" o " a <número>".
  Si el día de la semana no cuadra con esa fecha, gana el número (se
  confirma igual en el wizard).
- **F11 — Cancelar: si nombre+fecha no deja ninguna, usar solo el nombre.**
  En `_iniciar_cancelacion`, si `filtrados` queda vacío pero el filtro solo
  por nombre deja alguna, usar esas (y si es una, directo a confirmar).
- **F12 — Consulta de un día no hábil.** Tras el aviso "No se pueden hacer
  reservas para el sábado…", no listar "libre ese día" (contradice el
  aviso). Listar el estado de hoy como antes de F5, o nada; lo más corto.

Pruebas: agregar casos F9–F12 a `verificar_robustez_bot.py` ("si" en tipo_uso
→ no reconocido; "micro" en el menú de activos → micrófono; "cancela la sala
de edición del jueves 15" con la sala el 15/10 → directo a confirmar;
"hay disponibilidad del dron el sabado?" → sin "libre ese día").

## Pruebas

`flask-app/verificar_robustez_bot.py`, estilo de los otros `verificar_*.py`:
sin mensajes, en transacción revertida (parchear commit→flush), con un
usuario ficticio completo (crear y revertir). Casos mínimos:
- F1: wizard de cancelación con "si, cancelar" → cancela; "no" → no; "cerrar" → no.
- F2: tabla de horas ("9 de la mañana"→9:00, "2 de la tarde"→14:00,
  "mediodía"→12:00, "9 y media"→9:30, "3"→15:00, "10"→10:00).
- F3: "del 7 al 9 de octubre", "lunes 12 de octubre"→12/10, "el 5 de octubre".
- F4: `_extraer_detalles_de_texto` sobre las frases reales de la prueba:
  "necesito el tripode manfrotto para mañana de 9 a 11 de la mañana",
  "me gustaria apartar la sala de edicion el lunes 5 de octubre de 2 a 5 de
  la tarde", "quiero el microfono rode y el tripode para el martes 6 de
  octubre". Y un `gestionar_reserva` de la primera frase que llegue a
  "Entendí jueves …" sin volver a pedir la fecha en blanco.
- F4 varios activos: el resumen trae micrófono y trípode.
- F4 accesorios: "la canon eos r6 pero sin el lente de 100-500" → el 100-500
  no queda en el carrito.
- F5: consulta "luces led este sábado" menciona que los sábados no; y la
  oferta aceptada arranca la reserva con las luces.
- F6: "cancela la sala de edición del lunes" con 3 reservas → va directo a
  confirmar la de la sala.
- F7: reserva solo de sala no pregunta Barranquilla; sala + cámara sí.
- F8: mensaje con inicio en 25 min dice "en 25 minutos".
- Correr también los `verificar_*.py` existentes que tocan reservas
  (`verificar_bot_no_disponible.py` al menos) para no romper nada.

Despliegue: `docker compose up -d --build flask-app` y luego
`docker compose restart dashboard-frontend` (nginx guarda la IP vieja de
Flask). Sin cambios de esquema.

Después, Opus repite la prueba E2E en vivo con Habid.
