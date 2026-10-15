# Reporte: mejoras de precisión en la clasificación del chat

**Fecha:** 15 de octubre de 2026
**Contexto:** `PRUEBAS_CHAT_QWEN3_2026-10-14.md` encontró que
`qwen/qwen3-1.7b` es rápido pero inestable en el caso más común
(`"necesito una camara para el viernes"` caía al menú de fallback). El
criterio que se fijó es que **acertar importa más que la velocidad**: se
aceptan 15–30 s por respuesta.
**Entorno:** todo dentro de Docker, contra el LM Studio del host. Modelo
del `.env`: `qwen/qwen3-1.7b` (no se cambió).

---

## Resumen

| Configuración | Batería ampliada (128 clasificaciones) | Solo las que llegan al LLM | Mediana | Máximo |
|---|---|---|---|---|
| qwen3-1.7b, código del 9 sep (línea base) | 103/128 | 90/115 | 4.3 s | 20.1 s |
| qwen3-1.7b + cambios 1–3 (primera versión) | 115/128 | 102/115 | 3.6 s | 25.1 s |
| **qwen3-1.7b + cambios 1–3 (versión final)** | **121/128** | **108/115** | 3.6 s | 10.3 s |
| **qwen3-4b-2507 + cambios 1–3** (sin tocar `.env`) | **123/128** | **110/115** | **0.2 s** | 16.1 s (carga del modelo) |

- Con el modelo actual, el error pasó de 25 fallos a 7, y **ningún pedido
  real de equipo cae ya al menú de fallback**.
- `qwen/qwen3-4b-2507`, que ya está descargado en LM Studio, acierta todo
  salvo un caso inofensivo, responde casi al instante (es la variante
  instruct, no razona) y escribe mejor (sección 4). **Es la recomendación.**
- La votación de 3 llamadas (paso 4 del plan) **no se implementó**: con
  estos resultados no hace falta.

---

## 1. La batería ampliada

Los 20 mensajes del 9 de septiembre más 16 nuevos, elegidos donde el
modelo se equivocaba: pedidos que no dicen "reservar" ("quiero una cámara
para el sábado"), preguntas sin palabra clave, formas de desistir, y
mensajes **fuera de tema que usan palabras del dominio**, para comprobar
que los cambios no abren wizards de más ("necesito que me expliques la
fotosíntesis", "mi celular no carga").

Los mensajes que resuelve la ruta rápida son deterministas y se corren una
vez; los que llegan al LLM, **5 veces cada uno**. Ningún mensaje de la
batería coincide con los ejemplos que se pusieron en el prompt, para medir
comprensión y no memoria.

### Línea base (código sin cambios): 103/128

Los fallos no eran los que esperaba el reporte de ayer. El problema
dominante no es `otro`, sino que el modelo **confunde pedir con
preguntar**:

| Mensaje | Esperado | Resultado |
|---|---|---|
| `quiero una camara para el sabado` | `reservar` | `consultar_disponibilidad` 5/5 ❌ |
| `quisiera un microfono para manana en la tarde` | `reservar` | `consultar_disponibilidad` 5/5 ❌ |
| `necesito luces para una grabacion el martes` | `reservar` | 1/5 ❌ |
| `necesito una camara para el viernes` | `reservar` | 2/5 ❌ (`otro` 1, `consultar` 2) |
| `me gustaria usar la sala de edicion el jueves` | `reservar` | 4/5 |
| `que hora es` | `otro` | `saludo` 5/5 |
| `necesito que me expliques la fotosintesis` | `otro` | 4/5 (`ayuda` 1) |
| `mi celular no carga` | `otro` | 4/5 (`saludo` 1) |

---

## 2. Qué se cambió (`flask-app/app/llm_client.py`)

### 2.1 Protección contra un `otro` sobre un mensaje que nombra un equipo

Hasta ahora `_validar_intent_de_accion` cuidaba una sola dirección: que el
LLM no abra un wizard sobre un mensaje que no habla de la LACS. Ahora
también se cuida la contraria: si el LLM dice `otro` y el mensaje **nombra
un equipo o un espacio**, decide `_clasificar_intent_heuristico`. Si el
heurístico tampoco encuentra un verbo de acción, devuelve `otro` igual, así
que "mi celular no carga" sigue yendo al menú.

La condición es `_menciona_equipo_o_espacio` (nueva), más estricta que
`_tiene_senal_de_dominio`:

- Exige un **equipo o espacio**. No alcanza con un verbo ("quiero", que
  aparece en "quiero que me cuentes un chiste") ni con una fecha. Para eso
  `SENALES_DOMINIO` se partió en `_SENALES_ACCION`, `_SENALES_EQUIPO` y
  `_SENALES_TIEMPO`; la tupla completa sigue existiendo igual y
  `_tiene_senal_de_dominio` no cambió.
- Compara **palabras enteras** con plural opcional (`camaras` → `camara`,
  `proyectores` → `proyector`), no subcadenas. La primera versión usaba
  subcadenas y la batería lo detectó: "**foto**sintesis" contiene `foto`,
  así que el guard pisaba el `otro` correcto del modelo y abría el wizard de
  reservas **5 de 5 veces**. Se confirmó preguntándole al modelo en crudo,
  que respondía `otro` 3/3.

### 2.2 Prompt de clasificación: reglas y ejemplos

El razonamiento del modelo mostraba la confusión
(*"they just said 'necesito', which is more about needing, not necessarily
reservation"*). Se agregaron a `PROMPT_CLASIFICACION`:

- Cuatro reglas para los casos dudosos: decir que necesita o quiere un
  equipo es `reservar`; preguntar si hay es `consultar_disponibilidad`;
  decir que ya no lo necesita es `cancelar`; pedir algo que no es un equipo
  es `otro`, aunque diga "necesito".
- Siete ejemplos cortos (mensaje → categoría), en la frontera entre
  categorías y **distintos de los de la batería**.

### 2.3 Muestreo recomendado por Qwen3 y más presupuesto

- `temperature` de `0` a `0.6`, más `top_p: 0.95` y `top_k: 20`. Es lo que
  recomienda Qwen3 para su modo de razonamiento, que desaconseja
  explícitamente la decodificación greedy porque hace que el razonamiento
  entre en bucle. Ayer se vio justo eso: oscilaba entre `reservar` y `otro`
  hasta agotar los tokens.
- `max_tokens` de `800` a `1500`, para no cortar razonamientos largos que
  iban bien. El timeout queda en 45 s, como tope duro.

Estos tres valores de muestreo son específicos de Qwen3: el comentario en el
código pide revisarlos si se cambia de familia de modelo.

---

## 3. Resultados después de los cambios

### 3.1 qwen3-1.7b (modelo actual): 121/128

Los 7 fallos que quedan:

| Mensaje | Esperado | Resultado | ¿Importa? |
|---|---|---|---|
| `quiero una camara para el sabado` | `reservar` | 4/5 (`consultar_disponibilidad` 1) | Poco: la consulta termina ofreciendo *"¿Quieres reservar algo? Responde sí"* |
| `quiero que me cuentes un chiste` | `otro` | 3/5 (`saludo` 2) | No: en vez del menú, contesta conversando |
| `que hora es` | `otro` | 1/5 (`saludo` 4) | No: ídem |

**Todos los pedidos de equipo que antes iban al menú o a la consulta ahora
aciertan 5/5**, salvo una corrida del primero. El peor tiempo bajó de 20.1 s
a 10.3 s: con el muestreo nuevo el modelo ya no se enreda.

### 3.2 qwen3-4b-2507: 123/128

La misma batería y el mismo código. El modelo se cambió solo para el
proceso de prueba (`docker exec -e LLM_MODEL=qwen/qwen3-4b-2507`), sin
tocar el `.env`.

**Todo 5/5 salvo `que hora es` → `saludo`**, que hacen igual los dos modelos
y es inofensivo. Mediana de 0.2 s y p90 de 1.2 s: es la variante instruct
"2507", que no razona. El único tiempo alto (16.1 s) fue la primera llamada,
mientras LM Studio cargaba el modelo en memoria.

### 3.3 End-to-end por el webhook (qwen3-1.7b, código nuevo)

Usuario de pruebas `000000000000`, sesión limpia antes de cada mensaje. En
todas las filas hay que restar ~10 s del envío fallido al número de pruebas.

| Mensaje | `intent` auditado | Lógica + LLM |
|---|---|---|
| `necesito una camara para el viernes` | `reservar` ✅ | ~7.7 s (incluye recargar el 1.7b después de la prueba del 4B) |
| `quisiera un microfono para manana en la tarde` | `reservar` ✅ | ~5.6 s |
| `necesito que me expliques la fotosintesis` | `menu_fallback` ✅ | ~3.5 s |
| `cual es la capital de mongolia` | `menu_fallback` ✅ | ~4.5 s |

Las reservas del usuario de pruebas no cambiaron: `[(2, no_show), (3,
no_show)]` antes y después.

---

## 4. Calidad del texto generado: 1.7B contra 4B

Mismos mensajes, mismo prompt (`PROMPT_CHARLA` y `SYSTEM_PROMPT` no se
tocaron):

| Mensaje | qwen3-1.7b | qwen3-4b-2507 |
|---|---|---|
| `como estas?` | *"¡Hola! ¿Cómo estás? Estoy aquí para ayudarte con reservas. ¿Qué te gustaría verificada o qué necesitas?"* ❌ gramática | *"Hola, bien gracias, y tú cómo estás? ¿Te gustaría que te reserve un espacio o equipo en el Centro de Producción Audiovisual?"* |
| `gracias, muy amable` | *"¡Gracias por su amabilidad! ¿Podría ayudarme a reservar un equipo o espacio?"* ❌ roles invertidos, usted | *"De nada, ¡feliz día! Puedo ayudarte a reservar un equipo o espacio si necesitas. ¿Te interesa algo en concreto?"* |
| `quien eres?` | *"…¿Podrías decirme si algo está disponible…?"* ❌ le pregunta al usuario | *"Soy el asistente de reservas del Centro de Producción Audiovisual de Uninorte. Puedo ayudarte a reservar un equipo, un espacio o cancelar una reserva…"* |
| `que puedes hacer` | Lista con viñetas, y repite la regla interna "No puedo inventar disponibilidad" | Prosa: *"Puedo ayudarte a reservar equipos o espacios, consultar disponibilidad, cancelar una reserva…"* |

Tiempos parecidos (2.5–7.8 s con el 1.7B, 3.4–5.6 s con el 4B sin contar la
carga). El 4B tutea siempre y nunca invierte los roles.

---

## 5. Recomendación

**Cambiar en `.env` a `LLM_MODEL=qwen/qwen3-4b-2507`** y recrear
`flask-app` (`docker compose up -d flask-app`, más reiniciar
`dashboard-frontend`). Acierta más, contesta en menos de un segundo y
escribe mejor. El costo es más VRAM; conviene confirmar que el equipo lo
sostiene con LM Studio cargando solo ese modelo.

Si se cambia, revisar dos cosas:

1. **El muestreo de la sección 2.3** es el del modo de razonamiento de
   Qwen3. Para la variante instruct Qwen recomienda `temperature 0.7,
   top_p 0.8`. Con 0.6/0.95 ya sacó 123/128, así que no es urgente.
2. **Los docstrings de `llm_client.py`** todavía dicen que el modelo es
   `lfm2.5-2.6b` y que el razonamiento no se puede apagar. Conviene
   actualizarlos cuando se decida el modelo definitivo.

`que hora es` → `saludo` (y a veces "cuéntame un chiste" con el 1.7B) se
deja así a propósito: una respuesta conversacional es tan aceptable como el
menú, y forzarlo a `otro` costaría reglas nuevas para un caso sin
consecuencias.

---

## Archivos modificados

- `flask-app/app/llm_client.py`:
  - `PROMPT_CLASIFICACION`: reglas para los casos dudosos y 7 ejemplos.
  - `SENALES_DOMINIO` partida en `_SENALES_ACCION` / `_SENALES_EQUIPO` /
    `_SENALES_TIEMPO`, con el mismo contenido.
  - `_nombra_termino_catalogo`, extraída de `_tiene_senal_de_dominio` sin
    cambiar su comportamiento.
  - `_menciona_equipo_o_espacio`, nueva.
  - `clasificar_intent`: muestreo Qwen3, `max_tokens` 1500 y protección
    contra `otro`.

No se tocaron `.env`, los wizards, `webhook.py`, `PROMPT_CHARLA` ni
`SYSTEM_PROMPT`. Las comprobaciones de lógica pura (8 `assert` sobre
`_menciona_equipo_o_espacio` y el heurístico) se corrieron dentro del
contenedor y no quedaron en el repo, igual que la batería.
