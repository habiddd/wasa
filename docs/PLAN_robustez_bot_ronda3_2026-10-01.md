# Plan: robustez del bot, ronda 3 (2026-10-01)

Origen: prueba E2E en vivo de hoy. Todo en `flask-app/app/recursos_service.py`;
**no se tocan** `llm_client.py` ni `routes/webhook.py`. Regla que se mantiene:
fecha y hora nunca salen del LLM; nada de LLM nuevo. Toda interpretación sigue
pasando por los "¿Es correcto?" que ya existen.

## F13 — "¿Qué reservas tengo?" (prioridad alta)

Causa raíz: "que reservas tengo?", "mis reservas", "tengo reservas?" y "ver mis
reservas" contienen "reserv" (`MARCADORES_RESERVAR`, llm_client.py:92), así que
`_clasificar_sin_llm` devuelve "reservar" y llegan a `gestionar_reserva`, que
abre el wizard. No se crea un intent nuevo: haría falta tocar `INTENTS_VALIDOS`,
`ALLOWED_INTENTS` (config/env) y webhook. Como todas esas frases ya caen en
`gestionar_reserva`, se interceptan ahí.

- Constante nueva junto a `PALABRAS_CANCELAR` (≈l.1182):
  ```python
  _PATRON_VER_RESERVAS = re.compile(
      r"\bmis\s+reservas?\b|\b(?:que|cuales|cuantas)\s+reservas?\b"
      r"|\btengo\s+(?:algo\s+|alguna\s+|algunas\s+)?reserv(?:as?|ado|ada)\b"
      r"|\breservas?\s+(?:tengo|activas)\b")
  ```
  `reservas?\b` no matchea "reservar" ("tengo que reservar" no entra).
- `_mensaje_elegir_reserva_a_cancelar(bundles, pregunta=<texto actual>)`
  (≈l.3896): la pregunta final pasa a ser un parámetro con el texto de hoy
  como valor por defecto. Nada más cambia.
- `gestionar_reserva` (≈l.3823), DESPUÉS del chequeo de palabras de escape y
  ANTES del despacho:
  ```python
  if (wizard or {}).get("paso") != "nota_texto" and _PATRON_VER_RESERVAS.search(_normalizar(texto_usuario)):
      bundles = _reservas_cancelables_de(usuario.id)
      mensaje = (_mensaje_elegir_reserva_a_cancelar(bundles, pregunta=(
          'Si quieres cancelar alguna, dime cuál (ej. "cancela la sala de edición del martes").'))
          if bundles else "No tienes reservas activas.")
      if wizard:
          mensaje += "\n\nTu reserva en curso sigue abierta: responde la última pregunta para seguir."
      return mensaje, sesion   # sin wizard nuevo, sin cancelacion_wizard
  ```
  No se reenvía `_mensaje_menu_actual`: los pasos fecha/hora no lo actualizan
  y mostraría un menú viejo.
  `# ponytail: auditoría queda con intent "reservar"; intent propio si se mide aparte.`

**"Cancelar la de la sala del martes" con un wizard abierto.** No se hace
traspaso a la cancelación. Motivos:
1. El wizard fantasma lo abrió el bug de F13. Con F13 corregido, "quiero
   cancelar la de la sala…" llega a clasificación, cae en "cancelar" y el
   filtro F6/F11 va directo a confirmar.
2. Dentro de un wizard, "cancelar" significa abortar (contrato de F1).
   Adivinar que se refiere a una reserva existente exigiría una heurística
   con consulta a la base dentro del camino de escape.

Cambio mínimo: en el aborto por palabra de escape (≈l.3823), si la palabra fue
"cancelar"/"cancela" (no "no"/"cerrar"), se agrega al mensaje: ' Si lo que
querías era anular una reserva que ya tienes, escríbemelo de nuevo (ej.
"cancela la sala de edición del martes").'

## F14 — `_interpretar_opcion` (l.1625): sí/no y raíces de palabras

Se arregla en la función compartida. Llamadores revisados: tipo_uso (3089),
modalidad (3119), confirmar_grupo (3166), confirmar_fecha (3329),
confirmar_hora inicio/fin (3438/3497), resumen (3531), resolver_disponibilidad
(3646), elegir_alternativa (3704), quitar_item_menu (3727), confirmar
cancelación (4035) y `_aplicar_respuesta_menu` (activo/marca/modelo).
`sale_de_barranquilla` y `nota_preguntar` ya usan `_interpretar_si_no`.

Orden nuevo:
1. Número: igual que hoy.
2. **Menús Sí/No** (`{v for v, _ in opciones} == {"si", "no"}`, o sea
   CONFIRMAR_FECHA, CONFIRMAR_HORA y CONFIRMAR_CANCELACION): si el texto trae
   un sí o un no explícito (`tokens & (_SI_EXPLICITO | _NO_EXPLICITO)`, sin
   las palabras de contexto como "queda"/"aquí") y además NO trae dígitos ni
   fecha/hora (`not re.search(r"\d", texto) and not _extraer_detalles_de_texto(texto)`),
   se devuelve `_interpretar_si_no(texto)` como "si"/"no". La guarda deja
   "no, el viernes" y "sí, pero a las 4" a la reinterpretación que ya hace
   cada paso, en vez de aceptar o descartar a ciegas.
3. Coincidencia por etiqueta: igual que hoy (F9). Si hay ≥1 coincidencia, se
   devuelve como hoy (única → valor; varias → None).
4. **Solo si no hubo ninguna coincidencia: raíces de palabras.** Palabras del
   usuario y de la etiqueta con largo ≥4 y fuera de `PALABRAS_VACIAS_DESCRIPCION`
   (se reusa: ya trae "para", "esta", "quiero", "otro"…). Raíz de cada palabra
   de la etiqueta: `p[:max(4, len(p) - 2)]`. Una palabra del usuario suma 1 a la
   opción si `w.startswith(raiz)`. Gana el puntaje máximo solo si es único; si
   no, None.
   - "confirmala" → confirm(ar); "agregale" → agreg(ar); "cancelala" → cancel(ar);
     "quitale" → quit(ar); "cambia la fecha" → cambi + fech = 2, gana "Cambiar
     fecha" sobre las otras "Cambiar …" (1); "es para una asignatura" → asignatu(ra).
   - Falsos positivos que siguen fuera: "si"/"no" no llegan (largo <4, F9 intacto);
     "grabar" no entra en "grabaci(on)"; "cambiar" solo empata todas las
     "Cambiar…" → None.
   `# ponytail: raíces por prefijo, sin stemmer; basta para verbos -ar con enclítico.`
- `_continuar_cancelacion` (l.4035-4038): el respaldo con `_interpretar_si_no`
  queda cubierto por el paso 2. **Se borran esas 3 líneas.**
- **"agregale también el micrófono rode"**: en el paso "confirmar"
  (l.3560), después del `wizard.update(...)` de "agregar_item", se llama a
  `_precargar_activo(wizard, texto_usuario)` (ya existe y es el mismo camino
  que `por_agregar`). Si el texto no nombra nada, queda igual que hoy (menú de
  activos).

Fuera de alcance, sin cambio: `confirmar_grupo` con "si correcto"; "no,
cámbiala" en el resumen (el "no" sigue abortando, comportamiento F1 previo).

## F15 — Rango en el paso hora_inicio (l.3411)

Al inicio del paso:
```python
rango = _extraer_detalles_de_texto(f"de {texto}")  # ponytail: el "de" hace que "10 a 12" suelto cuente como horario
if "hora_fin" in rango:
    texto = rango["hora_inicio"]
    wizard["prefill"] = {**(wizard.get("prefill") or {}), "hora_fin": rango["hora_fin"]}
```
El resto del paso queda igual: "10 y media" → "Entendí 10:30 a.m. ¿Es
correcto?". Al confirmar, `_avanzar_wizard` llega a hora_fin y consume el
prefill ("12" → 12:00, formato exacto) con las validaciones normales, incluida
la disponibilidad. "de 10 y media a 12 del mediodia" ya lo parsea
`_PATRON_RANGO_HORAS` (h1="10 y media", h2="12").

## F16 — Nota en el mismo "sí" (l.3209, nota_preguntar)

Si `valor` es True y la primera palabra del texto es un sí explícito
(`_SI_EXPLICITO | {"1"}`), `resto` es el texto que sigue a esa palabra. Si
`resto`, sin las palabras de la etiqueta "Sí, quiero dejar una nota" ni
"por/favor/porfa/gracias", tiene ≥3 palabras:
`wizard["quiere_nota"] = True; wizard["paso"] = "nota_texto"; return _continuar_reserva(resto, usuario, sesion, wizard)`.
Así se reusan las validaciones de nota vacía o larga. "sí", "sí por favor" y
"sí, quiero dejar una nota" siguen pidiendo la nota como hoy.

## F17 — Menú de conflicto: cambiar fecha/hora (l.2617)

```python
OPCIONES_NO_DISPONIBLE = [("quitar", "Quitar esos activos y volver al resumen"),
    ("cambiar_fecha", "Cambiar fecha"), ("cambiar_hora", "Cambiar hora"),
    ("cancelar_reserva", "Cancelar toda la reserva")]
OPCIONES_NO_DISPONIBLE_CARRITO_VACIO = [("quitar", "Quitar esos activos y elegir otro"), *OPCIONES_NO_DISPONIBLE[1:]]
_CAMBIOS_HORARIO = {
    "cambiar_fecha": {"fecha": None, "fecha_fin": None, "inicio": None, "fin": None, "alternativas_para": []},
    "cambiar_hora": {"inicio": None, "fin": None, "alternativas_para": []},
}
```
- Resumen (l.3567-3572): los dos `elif` pasan a ser
  `elif valor in _CAMBIOS_HORARIO: wizard.update(_CAMBIOS_HORARIO[valor])`.
- resolver_disponibilidad (l.3644), después de `wizard["confirmado"] = False`:
  si `valor in _CAMBIOS_HORARIO`, se aplica `wizard.update(...)` y
  `opciones_actuales = None`, y se devuelve `_avanzar_wizard`. El carrito no se
  toca; el horario nuevo se vuelve a revisar en hora_fin
  (`_aplicar_hora_fin_valida`). `no_disponibles` ya se hizo pop arriba.
- Cambia la numeración del menú (cancelar pasa a 4). Ningún `verificar_*.py`
  usa esos números (revisado).

## Pruebas (agregar a `flask-app/verificar_robustez_bot.py`, antes del rollback)

Mismo estilo: usuario ficticio, commit→flush, rollback. Helpers nuevos:
```python
import copy
def dia_habil(d):
    while R._error_fecha_bloqueada({}, d): d += timedelta(days=7)
    return d
def hasta_hora_inicio(texto, dia):
    r, s = reservar(texto, {}); r, s = reservar("1", s); r, s = reservar("1", s)
    for _ in range(3):
        p = s["reserva_wizard"]["paso"]
        if p in ("confirmar_contenidos_espacio", "confirmar_accesorios"): r, s = reservar("no", s)
        elif p == "sale_de_barranquilla": r, s = reservar("2", s)
    r, s = reservar(dia.strftime("%d/%m/%Y"), s)
    assert "HORA DE INICIO" in r, r
    return s
```
- **F13**: `L._clasificar_sin_llm("que reservas tengo?") == "reservar"` (documenta
  la ruta). Para "que reservas tengo?", "mis reservas", "tengo reservas?",
  "ver mis reservas" y "que tengo reservado": `reservar(t, {})` → "TUS RESERVAS
  ACTIVAS" en r, "Sala de edición 1" en r (hay reservas de F6), sin
  `reserva_wizard` ni `cancelacion_wizard`. Con un wizard en tipo_uso: "que
  reservas tengo" lista y `s["reserva_wizard"]["paso"] == "tipo_uso"`. Luego
  "quiero cancelar la de la sala de edicion del martes" → wizard None y
  "anular una reserva que ya tienes" en r; ese mismo texto con `cancelar(…, {})`
  → "CONFIRMAR CANCELACIÓN". Con "no" el aborto no trae la pista.
  `R._PATRON_VER_RESERVAS.search("tengo que reservar")` es None.
- **F14** (unitarias): con CONFIRMAR_FECHA, "si correcto", "si esta bien",
  "dale", "sí" → "si"; con CONFIRMAR_HORA, "no gracias" → "no";
  "no, el viernes" (CONFIRMAR_FECHA) y "si, pero a las 4" (CONFIRMAR_HORA) → None.
  "es para una asignatura" → "asignatura"; "si" con TIPO_USO sigue en None (F9).
  Con OPCIONES_CONFIRMAR: "listo, confirmala" → "confirmar", "agregale tambien el
  microfono rode" → "agregar_item", "cambiale la hora" → "cambiar_hora",
  "cambia la fecha" → "cambiar_fecha", "quitale el tripode" → "quitar_item",
  "cambiar" → None. `op("quiero grabar", [("x", "Estudio de grabación")])` → None.
- **F15**: `D = dia_habil(dia_semana(2) + timedelta(days=35))`;
  `s = hasta_hora_inicio("quiero reservar la sala de edición", D)`;
  "de 10 y media a 12 del mediodia" → "Entendí 10:30 a.m."; "1" → "NOTA DE LA
  RESERVA" en r, inicio local 10:30 y fin local 12:00.
- **F16**: desde ese estado, con `copy.deepcopy`: "si por favor" → paso
  `nota_texto`. "si, es para grabar una entrevista con el decano" → "RESUMEN DE
  RESERVA" y `nota == "es para grabar una entrevista con el decano"`. Desde ese
  resumen, "agregale tambien el microfono rode" → "Micrófono Rode NTG3" en el
  carrito y "¿Qué necesitas reservar?" no está en r.
- **F17**: `D2 = dia_habil(dia_semana(3) + timedelta(days=35))`;
  `reservar_en(TRIPODE, D2, 10, 12)`;
  `s = hasta_hora_inicio("necesito el tripode manfrotto", D2)`; "10:00am",
  "12:00pm" → "No está disponible en ese horario" con "Cambiar fecha" y
  "Cambiar hora". Sobre una copia, `numero(s, "cambiar fecha")` → "FECHA DE
  RESERVA". `numero(s, "cambiar hora")` → "HORA DE INICIO" con el trípode aún
  en el carrito; "2pm", "4pm" → "NOTA DE LA RESERVA".
- Cambiar el print final a "OK: F1 a F17". Correr también
  `verificar_bot_no_disponible.py`.

Ejecución: `docker exec -i lacs_flask_app python - < flask-app/verificar_robustez_bot.py`
(igual para los otros `verificar_*.py`).

Despliegue: `docker compose up -d --build flask-app` y luego
`docker compose restart dashboard-frontend` (nginx guarda la IP vieja de Flask).
Sin cambios de esquema ni de config.

Después, Opus repite la prueba E2E en vivo con Habid.
