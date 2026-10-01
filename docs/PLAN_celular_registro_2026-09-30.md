# Plan: pedir el celular en el registro (2026-09-30)

## Revisión 3 (vigente; reemplaza la 2)

Decisión del usuario:
- El celular se pregunta **solo** si WhatsApp no lo dio (regla de la
  revisión 1: `telefono_es_lid`). Se descarta "preguntar a todos".
- Al final del registro se le muestran **todos** sus datos (nombre, ID
  Uninorte, cédula, celular) y la persona los confirma o corrige.

Parte del código de la revisión 1 ya está en el árbol (sin commit) y sirve:
`telefono_es_lid`, `asignar_celular`, pregunta del celular, prefijo "hola de
nuevo". Se construye encima.

### Esquema

- Quitar de `postgres/init/02-schema.sql` la línea de `celular_confirmado`
  (revisión 2, nunca usada) y borrarla de la base viva una vez:
  `ALTER TABLE usuarios DROP COLUMN IF EXISTS celular_confirmado;`
- Añadir `celular_escrito BOOLEAN NOT NULL DEFAULT false` (junto a los otros
  ALTER de `usuarios`) + la columna en el modelo `Usuario`. True = el
  `telefono_wa` lo escribió la persona (no vino de WhatsApp). Solo ese
  celular se puede corregir en la confirmación: uno que dio WhatsApp es la
  identidad de la cuenta y no se toca.
- `obtener_o_crear_usuario`: cuando `senderPn` corrige `telefono_wa` (rama
  existente), poner `celular_escrito = False`.

### Flujo (`registro_service.procesar_registro`)

Paso de confirmación derivado de la BD (no depende de la sesión): todos los
campos llenos, no LID, y `registro_completo = False` → toca confirmar.
`registro_completo = True` **solo** se pone tras el "sí".

1. Campos como hoy: nombre → código → cédula → celular (solo si
   `telefono_es_lid`).
2. Cuando no falta ninguno: mandar el resumen y dejar la sesión en
   `registro_campo_pendiente = "confirmar"`:
   ```
   Revisa tus datos:
   • Nombre: Habid Abdala
   • ID Uninorte: 200085000
   • Cédula: 1002379607
   • Celular: 573045246117
   ¿Son correctos? Responde *sí* para confirmar o *no* para corregir alguno.
   ```
   (Prefijo "¡Gracias! " si viene de responder un campo.)
3. Respuesta en "confirmar" (minúsculas, sin tildes, sin puntuación):
   - sí / si / s / ok / correcto / confirmo → `registro_completo = True`,
     mensaje de "¡Listo, ya quedaste registrado!..." de hoy.
   - no / n → "¿Qué dato quieres corregir? Responde con el número:
     1. Nombre 2. ID Uninorte 3. Cédula" + "4. Celular" solo si
     `celular_escrito`. Sesión → `"corregir"`.
   - otra cosa → repetir la pregunta de sí/no (con el resumen).
4. Respuesta en "corregir": número válido → sesión `"corregir:<campo>"` y se
   manda la pregunta de ese campo. Inválido → repetir las opciones.
5. Respuesta en `"corregir:<campo>"`: validar como siempre (`_validar`);
   inválido → "No entendí ese dato..." + pregunta. Válido → sobrescribir
   (`celular` vía `asignar_celular`, que puede rechazar/fusionar) y volver al
   resumen (paso 2).
6. Si la sesión se perdió (`registro_campo_pendiente` None) y todos los
   campos están llenos → mandar el resumen directo (sin bienvenida).
7. Usuario ya registrado por LID sin número (`registro_completo` True y
   `telefono_es_lid`): al entrar al registro poner `registro_completo =
   False`, pedir el celular con el prefijo "¡Hola de nuevo!..." y seguir al
   resumen como cualquiera.
8. `asignar_celular` pone `celular_escrito = True` en la fila que queda.
   Fusión **solo** si la fila existente no tiene `jid_wa` (creada desde el
   dashboard); si tiene, devolver None y responder "Ese número ya está
   registrado con otra cuenta de WhatsApp. Escribe tu número o comunícate con
   la LACS." + repetir la pregunta. Comentario `ponytail:` en la fusión: sin
   OTP no se prueba que el número sea de quien lo escribe.
9. `usuario_esta_completo` queda `registro_completo and not
   telefono_es_lid(usuario)`.
10. `MENSAJE_BIENVENIDA` sigue "son unos datos rápidos".

### Pruebas (reescribir `entregables/OE3/pruebas/verificar_celular_registro.py`)

Sin mensajes reales, base igual al final, IDs/números ficticios.
1. JID `@s.whatsapp.net`: 3 campos → resumen con celular de WhatsApp, no
   pregunta el celular → "sí" → completo.
2. LID sin número: 3 campos → celular → resumen → "no" → "4" → otro número
   → resumen con el nuevo → "Sí." → completo, `telefono_wa` nuevo,
   `celular_escrito` True.
3. Corregir nombre: "no" → "1" → nombre nuevo → resumen → "si" → completo.
4. Número de WhatsApp: tras "no" las opciones no traen "4"; responder "4"
   → repite opciones.
5. "tal vez" en confirmar → repite la pregunta; sigue incompleto.
6. Sesión perdida en confirmación (`{}`) → sale el resumen, no la bienvenida.
7. Usuario ya completo por LID sin número → "hola de nuevo" → celular →
   resumen → "sí".
8. Fusión con fila del dashboard sin `jid_wa` → una sola fila, confirmada al
   final; LID que escribe el número de una fila con `jid_wa` → rechazo,
   ninguna fila cambia.
9. `obtener_o_crear_usuario` con `senderPn` sobre un usuario con
   `celular_escrito` → `celular_escrito` False.

Migración viva y rebuild como en la revisión 2 (`02-schema.sql` con psql como
`postgres`, luego `docker compose up -d --build flask-app`).

## Revisión 2 (reemplaza la regla "solo a quien llega por LID")

Prueba en vivo: Habid escribe por `@lid` pero **con** `senderPn`, así que ya
teníamos su número y no se le preguntó. El usuario decidió: **se pregunta a
todos**, siempre.

`telefono_wa` es la clave de identidad (búsqueda del usuario, sesión de
Redis, auditoría), así que el número escrito **no** puede pisar un número
que ya vino de WhatsApp: cualquiera podría escribir el número de otro y
quedarse con su cuenta. Reglas:

1. Columna nueva `usuarios.celular_confirmado BOOLEAN NOT NULL DEFAULT false`
   (bloque idempotente al final de `postgres/init/02-schema.sql` + modelo
   `Usuario`). **Sin backfill**: todo usuario existente lo da una vez en su
   próximo mensaje (con el prefijo "¡Hola de nuevo!..." que ya existe).
2. `_siguiente_campo_faltante`: tras `CAMPOS_REGISTRO`, si
   `not usuario.celular_confirmado` → `("celular", PREGUNTA_CELULAR)`.
   `usuario_esta_completo` = `registro_completo and celular_confirmado`.
   `telefono_es_lid` sigue existiendo, pero solo para decidir el caso 3.
3. Al responder (valor ya normalizado con `normalizar_telefono`):
   - Si `telefono_es_lid(usuario)` → `asignar_celular` como hoy (pone
     `telefono_wa` o fusiona), **pero la fusión solo si la fila existente no
     tiene `jid_wa`** (cliente creado desde el dashboard que nunca escribió
     por WhatsApp). Si la tiene, es la cuenta de otra persona de WhatsApp:
     no se toca nada y se responde "Ese número ya está registrado con otra
     cuenta de WhatsApp. Escribe tu número o comunícate con la LACS." y se
     repite la pregunta (la sesión sigue en `celular`).
     `asignar_celular` puede devolver `None` en ese caso para que
     `procesar_registro` responda el error.
     Dejar un comentario `ponytail:` en la fusión: sin OTP no se puede probar
     que el número es de quien lo escribe; quien sepa el número de un cliente
     creado desde el dashboard puede reclamar esa fila.
   - Si no es LID (el número vino de WhatsApp): el escrito debe ser igual a
     `telefono_wa`; si no, "Ese número no coincide con el de este WhatsApp.
     Escribe el número de este celular." y se repite la pregunta.
   - En ambos casos de éxito, `celular_confirmado = True` en la fila que
     queda (también en la fila existente tras una fusión).
4. `preparar_usuario_dashboard` no toca `celular_confirmado` (lo confirma la
   persona, no el personal).
5. Nada más en dashboard ni webhook.

### Pruebas (actualizar `entregables/OE3/pruebas/verificar_celular_registro.py`)

1. JID real `@s.whatsapp.net`: tras la cédula pregunta el celular; un número
   distinto → mensaje de "no coincide", sigue sin completar; el mismo número
   escrito como "300 999 0001" → completo, `celular_confirmado` True.
2. LID sin número: como antes, guarda `telefono_wa` y queda confirmado.
3. Usuario ya registrado (registro_completo True, celular_confirmado False,
   número real): `usuario_esta_completo` False; prefijo "hola de nuevo";
   al confirmar queda completo.
4. Fusión con fila del dashboard (sin `jid_wa`): como antes, y la fila que
   queda tiene `celular_confirmado` True.
5. LID que escribe el número de una fila **con** `jid_wa`: rechazo, ninguna
   de las dos filas cambia, la fila LID sigue incompleta.

Migración viva (ver memoria de migraciones): re-ejecutar `02-schema.sql` con
`docker exec -e APP_DB_USER=<APP_DB_USER de .env> -i lacs_postgres psql -v ON_ERROR_STOP=1 -U postgres -d <APP_DB_NAME> < postgres/init/02-schema.sql`,
luego `docker compose up -d --build flask-app`.

---

## Revisión 1 (original): pedir el celular solo cuando llega por LID

## Problema

Con JID `@lid` y sin `senderPn`, `usuarios_service.obtener_o_crear_usuario`
crea el Usuario con `telefono_wa` = el LID (no es un teléfono). El registro
(`registro_service.py`) no pregunta el celular porque asume que ya lo tenemos.

## Decisión

- **Solo se pregunta a quien no tiene número real**: `usuario.jid_wa` termina
  en `@lid` y `usuario.telefono_wa == jid_wa.split("@")[0]`. A quien escribe
  con número real no se le pregunta nada nuevo.
- **Se guarda en `telefono_wa`** (no columna nueva): así el dashboard, la
  búsqueda por teléfono de `preparar_usuario_dashboard` y la auditoría quedan
  con el número real sin tocar nada más. Los mensajes siguientes por LID se
  resuelven por `jid_wa` (ya existe en `obtener_o_crear_usuario`), y como
  `telefono_wa != LID` ya no se vuelve a preguntar.
- **Se pregunta de último** (después de nombre, código y cédula). La sesión de
  Redis y el rate-limit usan `telefono_wa` como clave; si cambiara a mitad de
  registro, el siguiente mensaje caería en una sesión vacía y se perdería la
  respuesta. De último, la sesión ya termina con `registro_campo_pendiente=None`.
- **Usuarios ya registrados con LID** también deben darlo: `usuario_esta_completo`
  devuelve False si falta el celular, así el próximo mensaje entra al registro
  y se le pide solo ese dato. Para ellos el prefijo no es la bienvenida de
  "necesito registrarte" sino algo como "¡Hola de nuevo! Antes de seguir,
  necesito un dato más." (caso `es_primera_pregunta` y `usuario.nombre` ya lleno).
- **Choque de número**: si el celular ya es `telefono_wa` de otra fila (p. ej.
  cliente creado desde el dashboard), se **fusiona** la fila LID en la
  existente: `existente.jid_wa = lid_jid`; se rellenan solo los campos vacíos
  de la existente con los de la fila LID (misma regla de "no pisar" que
  `preparar_usuario_dashboard`); `reservas` y `reserva_grupos` de la fila LID
  pasan a la existente (`UPDATE ... SET usuario_id`); se borra la fila LID;
  `registro_completo=True` si quedan nombre+código+cédula. `aviso_privacidad_en`:
  conservar el más antiguo no nulo. Son las únicas dos FK a `usuarios`.

## Cambios

1. `usuarios_service.py`
   - `telefono_es_lid(usuario) -> bool` (regla de arriba).
   - `asignar_celular(usuario, telefono_normalizado) -> Usuario`: sin choque
     pone `telefono_wa`; con choque fusiona como arriba y devuelve la fila
     existente. Un commit al final.
2. `registro_service.py`
   - Pregunta: "¿Cuál es tu número de celular? (ej. 3001234567)".
   - `_siguiente_campo_faltante`: después de `CAMPOS_REGISTRO`, si
     `telefono_es_lid` → `("celular", PREGUNTA)`.
   - `_validar("celular", ...)` usa `usuarios_service.normalizar_telefono`.
     Hay import circular (usuarios_service importa registro_service): importar
     dentro de la función, o mover `normalizar_telefono` a registro_service y
     dejar que usuarios_service lo llame desde ahí. Elegir lo más corto.
   - Al guardar `celular` no se usa `setattr`: `usuario = asignar_celular(...)`
     y se sigue con ese objeto (puede ser otra fila).
   - `usuario_esta_completo`: `registro_completo and not telefono_es_lid(usuario)`.
   - `MENSAJE_BIENVENIDA`: quitar "son solo 3 datos" (ahora pueden ser 4);
     "son unos datos rápidos".
   - Actualizar el docstring del módulo (hoy dice que el celular no se pregunta).
3. `webhook.py`: nada, salvo confirmar que no hace falta. `numero` queda como
   el LID para ese último mensaje (sesión y auditoría de ese turno); aceptado.

Sin cambios de esquema.

## Pruebas

`entregables/OE3/pruebas/verificar_celular_registro.py`, estilo de los otros `verificar_*.py`
(asserts, `docker exec -i lacs_flask_app python - < ...`), todo dentro de una
transacción que se revierte al final, sin mandar mensajes (llamar
`procesar_registro` / `obtener_o_crear_usuario` directo, no el webhook):

1. Usuario con JID real (`...@s.whatsapp.net`): el registro termina tras la
   cédula, nunca pregunta el celular.
2. Usuario LID nuevo: nombre → código → cédula → pregunta celular; respuesta
   inválida ("abc") repite la pregunta; "300 123 4567" guarda
   `telefono_wa == "573001234567"`, `registro_completo` True, `jid_wa` intacto.
   Luego `obtener_o_crear_usuario(LID, LID@lid)` devuelve el mismo usuario y
   `usuario_esta_completo` es True.
3. Usuario LID ya registrado (registro_completo True, telefono_wa = LID):
   `usuario_esta_completo` False; el primer `procesar_registro` pregunta solo
   el celular con el prefijo de "hola de nuevo"; al responder queda completo.
4. Choque: existe fila con `telefono_wa=57300...` (sin jid, sin cédula) y una
   reserva/grupo; la fila LID completa el registro dando ese número →
   queda una sola fila, con `jid_wa` = LID, cédula rellenada, nombre original
   sin pisar, reservas de ambas en ella, fila LID borrada.

Usar números/LIDs ficticios que no existan (p. ej. LID `999000000000001`,
teléfono `573009990001`). Cuidado: `db.session.commit()` dentro de los
servicios rompe el rollback final — parchear `db.session.commit = db.session.flush`
durante la prueba, o limpiar las filas creadas al final.

Además, correr los `verificar_*.py` existentes que tocan usuarios/registro si
alguno lo hace, y reiniciar el contenedor (`docker compose restart` del
servicio flask) antes de probar.
