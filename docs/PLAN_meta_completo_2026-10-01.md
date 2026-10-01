# Plan: dejar listo el canal de Meta (Cloud API) sin tocar Baileys (2026-10-01)

Rama `dev`. Este plan cubre solo los **huecos de código** que impiden que el canal de Meta funcione del todo en producción. Cada paso explica por qué Baileys no cambia. Hay cambios sin commit en `guardrails.py`, `recursos_service.py` y `verificar_robustez_bot.py` que no son de este plan: **no se tocan**.

Datos vivos consultados (solo lectura) el 2026-10-01:
- `whatsapp:proveedor = meta`.
- Los wamid guardados miden **62** caracteres, y `auditoria_bot.mensaje_id` es `VARCHAR(64)`.
- La respuesta más larga del bot tiene 1955 caracteres; el límite de Meta para texto libre es 4096.
- Los avisos más largos rondan los 350 caracteres.

## Revisión de los candidatos

| Candidato | Veredicto | Motivo |
|---|---|---|
| Ventana de 24 h mal calculada | **Confirmado (paso 1)** | `_escribio_en_24h` también cuenta las filas de auditoría de mensajes que llegaron **por Baileys**. Una persona que escribió por Baileys en las últimas 24 h recibe texto libre desde el número de Meta, Meta lo acepta con 200 y después lo marca `failed` 131047: el mensaje se pierde sin aviso. Además, el TTL de Redis cuenta desde que se procesa el mensaje, no desde su `timestamp`. |
| Parámetro de plantilla | **Confirmado (paso 2)** | El cuerpo completo de la plantilla admite 1024 caracteres. El texto fijo de `lacs_aviso` ocupa unos 96, así que un parámetro de 1000 puede pasarse. Tabulaciones o más de 4 espacios seguidos dan el error 132018. |
| Errores HTTP o de token | **Confirmado (paso 3)** | `_post_meta` solo registra "400 Client Error": no se ve el código de Meta (190, 131030, 132xxx…). |
| Casos raros del webhook | **Confirmado (paso 4)** | No se filtra por `metadata.phone_number_id`: si la app recibe eventos de otro número (el de prueba y el real a la vez), se contestaría desde el número configurado. Los `failed` y `value.errors` no dejan registro en el log. Varias entradas o cambios en un mismo POST, y estados de wamid desconocidos, ya se manejan bien (`actualizar_estado_envio` devuelve False). |
| **Meta pisa `usuarios.jid_wa`** (no estaba en la lista) | **Confirmado (paso 5)** | `_procesar_mensaje` llama a `obtener_o_crear_usuario(numero, "<num>@s.whatsapp.net")` y eso **sobrescribe un `jid_wa` `@lid`** que había guardado Baileys. Al volver a Baileys, si llega un `@lid` sin `senderPn`, la búsqueda por `jid_wa` ya no encuentra a la persona: se crea una fila nueva y tiene que registrarse otra vez. Es un efecto del camino de Meta sobre el de Baileys. |
| `mensaje_id` `VARCHAR(64)` | **Confirmado (paso 6)** | El wamid lleva el número codificado en base64: 62 caracteres para un número de 12 dígitos, y más de 64 con números más largos. Pasado ese largo, el INSERT de auditoría falla: la respuesta sale, pero no queda en la auditoría ni se le puede asociar el ack. |
| BSUID completo | **Confirmado (paso 7), sin columna nueva** | Basta reutilizar el flujo del LID: `telefono_wa` = BSUID mientras no dé su celular, `jid_wa` = `<BSUID>@bsuid`, y el registro le pide el celular como ya hace con los `@lid`. Sí hace falta ensanchar `telefono_wa` y `jid_wa`. |
| `jid_de` con usuario que solo tiene LID, canal Meta | Va dentro del paso 7 | Hoy manda a `<LID>@s.whatsapp.net`, Meta lo rechaza y no sale nada. El arreglo hace falta para BSUID. |
| Mensajes que no son texto (multimedia, ubicación, reacciones, `unsupported`, `interactive`/`button`) | **Descartado** | Baileys también los ignora (`index.js` solo pasa `conversation`/`extendedTextMessage`), así que ya se comportan igual. El bot no manda botones ni listas, y `lacs_aviso` no tiene botones: no llegan respuestas `interactive`/`button`. |
| Multimedia, PDF, actas, catálogo, "escribiendo…", confirmación de lectura | **Descartado** | Flask solo manda texto (`enviar_mensaje_texto`). Baileys no manda "escribiendo…" ni marca como leído. Los endpoints de catálogo de `index.js` no se usan. |
| Reintentos | **Descartado** | Baileys tampoco reintenta, y los avisos guardan `error_envio` igual en los dos canales. Reintentar tras un timeout puede duplicar mensajes, porque Meta quizá ya lo aceptó. |
| Reenviar como plantilla al recibir 131047 | **Descartado** | Con el paso 1 el texto libre solo sale dentro de una ventana que de verdad está abierta. Queda como nota `ponytail:` en el código. |
| Webhook lento por el LLM (Meta reintenta) | Ya cubierto | La deduplicación por wamid (`SET NX`) se hace antes de procesar, así que los reintentos se descartan. |

## Cómo se verifica todo (vale para cada paso)

Siempre dentro de Docker, nunca con el python o node del host:

```
docker compose up -d --build flask-app && docker compose restart dashboard-frontend
docker exec -i lacs_flask_app python - < flask-app/verificar_meta_whatsapp.py
docker exec -i lacs_flask_app python - < flask-app/verificar_robustez_bot.py
docker exec -i lacs_flask_app python - < flask-app/verificar_celular_registro.py
```

Las tres pruebas tienen que terminar en OK. Lo nuevo se agrega como `assert` en `verificar_meta_whatsapp.py`, con `requests.post` parcheado: nunca se llama a Meta.

**Antes de empezar**, hay que ajustar el script de pruebas:
- `post_falso` tiene que devolver un objeto con `ok`, `status_code` y `text`, y debe poder simular un fallo con una bandera, por ejemplo `fallar = {"on": False}`.
- En `entrante()`, `"timestamp"` pasa a `str(int(time.time()))`. Hoy vale `"1"`, y con el paso 1 eso ya no abriría la ventana.

**Prueba de que Baileys no cambia.** Se agrega una vez en el script y se mantiene:
- Con `cambiar_proveedor("baileys")`, `W.enviar_mensaje_texto("999000000000077@lid", "x")` hace POST a `.../send` con `{"jid": "999000000000077@lid", "text": "x"}`.
- `W.jid_de(u)` devuelve `u.jid_wa`.
- Después se vuelve a `meta`.

---

## Paso 1: ventana de 24 h solo con Redis y medida desde el mensaje

**Archivos:** `flask-app/app/whatsapp_client.py`, `flask-app/app/routes/webhook.py`.

**Cambios:**
- `_escribio_en_24h(numero)` queda en `return bool(redis_client.exists(f"meta:ventana:{numero}"))`.
  - Se borra la consulta a `AuditoriaBot`.
  - Se borran los imports que quedan sin uso: `datetime`, `db`, `AuditoriaBot`.
  - Comentario: la auditoría mezcla mensajes de los dos canales; la ventana de Meta solo la abre un mensaje que llegó por Meta.
- En `_procesar_mensaje_meta`, en lugar de `redis_client.set(f"meta:ventana:{numero}", 1, ex=86400)`:
  ```python
  # La ventana cuenta desde que la persona escribió, no desde que llegó el webhook (Meta reintenta hasta 36 h).
  ttl = 86400 - 300 - int(time.time() - int(msg.get("timestamp") or time.time()))
  if ttl > 0:
      redis_client.set(f"meta:ventana:{numero}", 1, ex=ttl)
  ```
  Se agrega `import time`. Los 300 s son margen frente al reloj de Meta.

**Por qué Baileys no cambia:** `_escribio_en_24h` solo la llama `_enviar_meta`, y `_procesar_mensaje_meta` solo corre en `POST /webhook/meta`.

**Pruebas:**
- `guardrails.registrar_interaccion(NUM2, "hola", None, None, permitido=True)` (la prueba revierte todo al final), y luego `enviar_mensaje_texto(f"{NUM2}@s.whatsapp.net", "x")` → `type == "template"`.
- Un entrante con `timestamp` de hace 25 h → `not redis_client.exists(f"meta:ventana:{NUM3}")`.
- Un entrante con `timestamp` actual → `0 < redis_client.ttl(...) <= 86100`.

## Paso 2: parámetro de plantilla válido

**Archivo:** `whatsapp_client.py`, en `_enviar_meta`.

**Cambio:**
```python
# Meta: sin \n ni \t ni más de 4 espacios seguidos, y el cuerpo entero ≤ 1024 (el texto fijo de lacs_aviso ocupa ~96).
plano = " · ".join(" ".join(l.split()) for l in texto.splitlines() if l.strip())
plano = plano if len(plano) <= 900 else plano[:899] + "…"
```

**Por qué Baileys no cambia:** solo se usa en la rama de plantilla de `_enviar_meta`.

**Pruebas:**
- `"a\tb     c\n\n d"` → el parámetro es `"a b c · d"`.
- Un texto de 2000 caracteres → `len(param) == 900` y termina en `…`.
- El `assert` que ya existe (`"Línea 1 · Línea 2"`) sigue pasando.

## Paso 3: dejar visible el error de Meta

**Archivo:** `whatsapp_client.py`, en `_post_meta`.

**Cambio:** se reemplaza `resp.raise_for_status()` por:
```python
if not resp.ok:
    # El cuerpo trae error.code (190 token, 131030, 132xxx plantilla…).
    current_app.logger.error("Meta rechazó el envío (%s): %s", resp.status_code, resp.text[:500])
    return False, None
```
El `except` se mantiene para timeouts y JSON inválido.

**Por qué Baileys no cambia:** `_post_meta` no está en el camino de Baileys.

**Prueba:** con la bandera de fallo activa (`ok=False`, `status_code=400`, `text='{"error":{"code":131030}}'`), `enviar_mensaje_texto(...) == (False, None)` y no se lanza ninguna excepción.

## Paso 4: webhook, número correcto y fallos en el log

**Archivo:** `routes/webhook.py`, en `recibir_webhook_meta`.

**Cambios:**
- Después de `valor = cambio.get("value", {})`:
  ```python
  if valor.get("metadata", {}).get("phone_number_id") != current_app.config["META_WA_PHONE_NUMBER_ID"]:
      continue  # evento de otro número de la cuenta: no se contesta desde el nuestro
  for err in valor.get("errors", []):
      current_app.logger.warning("Error de Meta en webhook: %s", err)
  ```
- Dentro del bucle de `statuses`:
  ```python
  if st.get("status") == "failed":
      current_app.logger.warning("Meta no entregó %s: %s", st.get("id"), st.get("errors"))
  ```

**Por qué Baileys no cambia:** solo se toca la ruta `/webhook/meta`.

**Pruebas:**
- El payload de estado de la línea 112 del script tiene que llevar `"metadata": {"phone_number_id": "123"}`.
- Un entrante con `phone_number_id` `"999"` → 200, `enviados` vacío y sin usuario creado.
- Un `failed` con `errors: [{"code": 131026}]` para un wamid desconocido → 200.
- Un `failed` para `WAMID_ENVIO` → `estado_envio == "fallido"`.

## Paso 5: Meta no pisa el `jid_wa` que usa Baileys

**Archivos:** `flask-app/app/usuarios_service.py`, `routes/webhook.py`.

**Cambios:**
- `obtener_o_crear_usuario(telefono_wa, jid_wa=None, pisar_jid=True)`. La condición de la línea 34 queda:
  ```python
  if jid_wa and usuario.jid_wa != jid_wa and (pisar_jid or not usuario.jid_wa):
  ```
  Comentario: Meta no pisa el JID de Baileys (`@lid`); sí rellena uno vacío, para que `asignar_celular` no tome la fila por una del dashboard.
- `_procesar_mensaje(numero, texto, remote_jid, pisar_jid=True)`. Las dos llamadas a `obtener_o_crear_usuario` (líneas 151 y 167) pasan `pisar_jid=pisar_jid`.
- `_procesar_mensaje_meta` llama con `pisar_jid=False`.

**Por qué Baileys no cambia:** `recibir_webhook` y `verificar_celular_registro.py` usan el valor por defecto `True`, que es la condición de hoy.

**Prueba:**
- Crear `Usuario(telefono_wa=NUM3, jid_wa="999000000000078@lid", rol="estudiante")`.
- Un entrante de Meta desde NUM3 → `jid_wa` sigue en `@lid`, y la respuesta salió con `to == NUM3`.
- Un usuario con `jid_wa=None` que escribe por Meta queda con `jid_wa == f"{num}@s.whatsapp.net"`.

## Paso 6: `mensaje_id` más ancho

**Archivos:** `postgres/init/02-schema.sql`, `flask-app/app/models.py`.

**Cambios:**
- En el esquema, junto a los `ALTER` de `auditoria_bot`:
  ```sql
  -- El wamid de Meta lleva el número en base64: 62+ caracteres.
  ALTER TABLE auditoria_bot ALTER COLUMN mensaje_id TYPE VARCHAR(128);
  ```
  Es idempotente, y en Postgres ensanchar un varchar no reescribe la tabla.
- En el modelo: `mensaje_id = db.Column(db.String(128))`.

**Migración viva:**
```
docker exec -e APP_DB_USER=<APP_DB_USER> -i lacs_postgres psql -v ON_ERROR_STOP=1 -U postgres -d reservas_db < postgres/init/02-schema.sql
```

**Por qué Baileys no cambia:** solo se ensancha una columna; los ids de Baileys (~20–32 caracteres) siguen cabiendo igual.

**Prueba:** `registrar_interaccion(NUM, "x", None, "y", True, mensaje_id="wamid." + "A" * 100)` hace flush sin error. Correr el esquema dos veces no da error.

## Paso 7: soporte completo de BSUID (personas con nombre de usuario, sin número)

**Esquema** (`02-schema.sql` + `models.py`, misma migración del paso 6):
```sql
-- BSUID ("CO.xxxx…") como identidad mientras la persona no dé su celular (ver usuarios_service.telefono_es_lid).
ALTER TABLE usuarios ALTER COLUMN telefono_wa TYPE VARCHAR(200);
ALTER TABLE usuarios ALTER COLUMN jid_wa TYPE VARCHAR(220);
ALTER TABLE auditoria_bot ALTER COLUMN telefono_wa TYPE VARCHAR(200);
```
- Ajustar los `String(...)` del modelo a esas medidas.
- Confirmar el largo máximo del BSUID en la documentación de Meta (enlace en `GUIA_META_WHATSAPP.md`). Si es mayor, usar ese valor más un margen.

**`usuarios_service.py`:**
- En la línea 21 y en `telefono_es_lid`: `jid_wa.endswith("@lid")` pasa a `jid_wa.endswith(("@lid", "@bsuid"))`.
- Actualizar los docstrings: el BSUID se trata igual que el LID, es decir, se le pide el celular y se busca por `jid_wa`.

**`routes/webhook.py`, en `_procesar_mensaje_meta`:**
- Se borran la rama "sin número" y `_AVISO_SIN_NUMERO`.
- Queda:
  ```python
  numero = msg.get("from") or msg.get("from_user_id")
  if not numero:
      return
  for clave in {msg.get("from"), msg.get("from_user_id")} - {None}:
      ...  # el set de ventana del paso 1, para cada clave
  jid = f"{numero}@s.whatsapp.net" if msg.get("from") else f"{numero}@bsuid"
  _procesar_mensaje(numero, msg["text"]["body"], jid, pisar_jid=False)
  ```

**`whatsapp_client.py`:**
- Se borra `enviar_meta_a_bsuid`.
- En `_enviar_meta`, se reemplaza el chequeo de `@s.whatsapp.net` por:
  ```python
  if jid_destino.endswith("@bsuid"):
      clave, destino = jid_destino.removesuffix("@bsuid"), "recipient"
  elif jid_destino.endswith("@s.whatsapp.net"):
      clave, destino = jid_destino.split("@")[0], "to"
  else:
      current_app.logger.error("Meta necesita número o BSUID, no el JID %s: no se envía.", jid_destino)
      return False, None
  payload = {"messaging_product": "whatsapp", "recipient_type": "individual", destino: clave}
  if _escribio_en_24h(clave): ...
  ```
  El resto queda igual.
- En `jid_de`, rama Meta, con `from app import usuarios_service` (ver Riesgos por el import circular):
  ```python
  if usuarios_service.telefono_es_lid(usuario):
      return usuario.jid_wa  # @lid: _enviar_meta lo rechaza; @bsuid: sale por recipient
  return f"{usuario.telefono_wa}@s.whatsapp.net"
  ```

**Por qué Baileys no cambia:**
- Baileys nunca genera JIDs `@bsuid`, así que `endswith(("@lid", "@bsuid"))` da lo mismo que hoy para todo lo que llega por Baileys.
- La rama Baileys de `jid_de` no se toca.
- Ensanchar columnas no afecta a los datos existentes.
- `verificar_celular_registro.py` cubre el flujo LID sin cambios.

**Pruebas** (`BS = "CO.PRUEBA" + uuid4().hex[:20]`):
- Un entrante sin `from` y con `from_user_id=BS` → existe `Usuario` con `telefono_wa == BS` y `jid_wa == f"{BS}@bsuid"`, y `usuarios_service.telefono_es_lid(u)` es True.
- El POST capturado tiene `recipient == BS`, **no** tiene `"to"`, y `type == "text"` (ventana abierta).
- Después de borrar `meta:ventana:{BS}`, `jid_de(u) == f"{BS}@bsuid"` y `enviar_mensaje_texto(jid_de(u), "x")` sale como plantilla con `recipient == BS`.
- Un usuario con solo LID (`telefono_wa = LID`, `jid_wa = f"{LID}@lid"`) con Meta activo: `enviar_mensaje_texto(jid_de(u), "x") == (False, None)` y no se hace ningún POST.
- Al final hay que limpiar `meta:ventana:{BS}` y `meta:wamid:*` de la prueba.

---

## Documentación (al terminar)

- `docs/META_WHATSAPP_ESTADO_2026-09-30.md`:
  - En §5.7, marcar BSUID como hecho, sin columna nueva.
  - En §2: la ventana ya solo usa Redis, Meta ya no pisa `jid_wa`, y se filtra por `phone_number_id`.
- `docs/GUIA_META_WHATSAPP.md`: cambiar la fila "Personas sin número en el webhook" por "Se registran con su BSUID y el bot les pide el celular".

## Riesgos y límites que se aceptan

- Una persona conocida por Baileys **solo** por LID (sin celular) que escribe por Meta con su número aparece como fila nueva, y vuelve a registrarse. No se fusiona; solo pasa con quien nunca dio su celular.
- El dashboard muestra el BSUID como "teléfono" mientras la persona no complete el registro (formateador `telefono()` del frontend). Es solo visual.
- Import circular: `whatsapp_client` → `usuarios_service` → `registro_service` → `usuarios_service`. Ese ciclo ya existe y está resuelto. Si el import al inicio del módulo falla, moverlo dentro de `jid_de`.

**Fuera de alcance (no es código):** aprobación de la plantilla `lacs_aviso`, número real de la LACS (coexistencia u otro número) y publicación de la app, verificación del negocio, método de pago, y dominio fijo para el túnel.
