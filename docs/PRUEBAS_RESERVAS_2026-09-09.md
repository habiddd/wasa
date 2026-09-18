# Reporte de pruebas: reservas de activos y lugares, con dos usuarios

**Fecha:** 9 de septiembre de 2026
**Alcance:** alta de lugares y activos asociados a lugares, y reserva de
equipos, espacios y combinaciones de ambos, con dos usuarios distintos.
**Entorno:** todo dentro de Docker. El alta de datos pasó por la API real
del dashboard (`POST/PATCH /dashboard/api/activos`, auth Basic), no por SQL;
las reservas, por el webhook real (`POST /webhook/whatsapp`) con el payload
de `messages.upsert`, leyendo cada respuesta del bot desde `auditoria_bot`.

---

## Resumen ejecutivo

**Ocho escenarios ejecutados, ocho completados.** La mecánica de reservas se
comportó como debía en todos: asignación de unidades, agrupación por número
de reserva, bundles padre/hijo, rechazo por solapamiento y aislamiento entre
usuarios.

Salieron **dos defectos reales** y un hueco de producto:

| # | Qué | Gravedad |
|---|---|---|
| 1 | La consulta de disponibilidad muestra las horas **en UTC**, cinco horas adelantadas respecto a lo que ve el usuario en el resto del bot | **Alto** — le dice al usuario una hora equivocada |
| 2 | La disponibilidad solo se puede consultar para **"ahora"**, nunca para una fecha futura | Medio — limitación de producto |
| 3 | "Reservar el lugar X **con sus** activos" no existe: `espacio_id` no lo usa el bot | Medio — hueco conocido, ahora medido |

Lo más importante que se quería validar **pasó**: al cancelar, cada usuario
ve exclusivamente sus propias reservas.

---

## 1. Datos creados

Se armó una jerarquía de lugares anidados con activos adentro, para poder
probar el caso que motivó `espacio_id` en su momento (la Sala 4 con sus
iMac y MacBook).

| Lugar | id | Dentro de | Contiene |
|---|---|---|---|
| Edificio LACS | 40 | — | las dos salas de abajo |
| Sala de postproducción 4 | 41 | Edificio LACS | 3 × iMac 24 M3, 2 × MacBook Pro 14 M3 |
| Cabina de audio 2 | 42 | Edificio LACS | Micrófono Shure SM7B (+ su brazo), Interfaz Focusrite |

| Activo | ids | Lugar | Notas |
|---|---|---|---|
| iMac 24 M3 | 43, 44, 45 | Sala de postproducción 4 | **tres unidades con el mismo nombre**, a propósito |
| MacBook Pro 14 M3 | 46, 47 | Sala de postproducción 4 | dos unidades |
| Micrófono Shure SM7B | 48 | Cabina de audio 2 | `requiere_seguro=true` |
| Brazo articulado Rode PSA1 | 50 | Cabina de audio 2 | **hijo** del micrófono (`padre_id=48`) |
| Interfaz de audio Focusrite | 49 | Cabina de audio 2 | |

Las tres unidades repetidas de iMac son deliberadas: el bot agrupa por
nombre y elige una unidad libre, y eso solo se puede probar con stock real.

**Dos reglas del modelo quedaron confirmadas de paso:**

- **Herencia de lugar.** El brazo articulado se creó con `padre_id` y
  **sin la clave `espacio_id` en el JSON** (no `null`: ausente, que es la
  condición exacta que exige el código). Heredó el lugar del padre.
- **Propagación a hijos.** Un `PATCH` sobre la Cámara Canon EOS R6 (id 23)
  asignándole el Estudio de grabación A devolvió `hijos_actualizados: 3`, y
  sus tres lentes (24, 25, 26) se movieron con ella.

Los 11 `POST` y el `PATCH` respondieron 201/200 sin un solo error de
validación.

---

## 2. Escenarios de reserva

Usuarios: **María Fernanda Rojas** (U1, `573001112233`) y **Carlos Andrés
Pérez** (U2, `573004445566`), ambos ya registrados y sin reservas previas.
Fechas: 15 y 16 de septiembre de 2026.

### A. Equipo suelto — correcto

U1: *"quiero reservar un imac"* → wizard → 15/09, 9:00–11:00 → confirmar.
Quedó la unidad **43**, número de reserva 19.

### B. Lugar — correcto

U1: *"quiero reservar la cabina de audio 2"* → 15/09, 14:00–16:00.
Quedó el recurso **42**, número de reserva 20. El resumen no mencionó los
activos que viven dentro de la cabina (ver escenario C).

### C. Lugar con sus activos — **no existe, confirmado literalmente**

U1: *"quiero reservar la sala de postproducción 4 **con sus imac**"*.
El bot pasó directo a pedir la fecha:

```
*FECHA DE RESERVA*
――――――――――――――――――
*Sala de postproducción 4*
――――――――――――――――――
Hoy es miércoles 09/09/2026.
¿Qué fecha necesitas? ...
```

**Ignoró por completo la frase "con sus imac":** ni mencionó que la sala
tuviera activos adentro, ni ofreció menú de accesorios. El resumen final
mostró solo la sala.

Esto no es una sorpresa —`espacio_id` quedó explícitamente fuera de alcance
en la sección 10 de `docs/SESION_2026-09-09.md`, como una relación de
inventario y dashboard— pero ahora está medido en vez de supuesto.

**Sí se puede armar a mano.** Desde el menú de confirmación, la opción
*"2. Agregar otro activo o espacio"* permitió sumar un MacBook Pro a la
misma reserva. Ambos quedaron bajo el **mismo número de reserva (21)**, con
idénticas fecha y hora. Así que la capacidad existe; lo que falta es que el
bot la ofrezca solo.

### D. Bundle padre/hijo — correcto

U2: *"quiero reservar el microfono shure sm7b"* → el bot detectó el hijo:

```
*ACTIVO SELECCIONADO*
*Micrófono Shure SM7B*
Incluye por defecto:
1. Brazo articulado Rode PSA1
2. Ninguno (dejar todos incluidos)
```

Al confirmar agregó, correctamente, el aviso de seguro:
*"⚠ Micrófono Shure SM7B requiere seguro — el equipo de la LACS te
contactará para completar ese trámite."*
Quedaron las dos filas (48 y 50) bajo el número de reserva 22.

**Contraste que vale la pena notar:** el bot sí sabe traer los hijos por
`padre_id` (accesorios en bundle), pero no los contenidos por `espacio_id`
(activos dentro de un lugar). Son dos jerarquías separadas por diseño, y
solo una llegó a la lógica conversacional.

### E. Concurrencia sobre varias unidades — correcto

Todas en el mismo rango (15/09, 9:00–11:00):

| Intento | Usuario | Resultado |
|---|---|---|
| 1.º | U1 | unidad **43** |
| 2.º | U2 | unidad **44** — eligió otra, correcto |
| 3.º | U1 | unidad **45** — última libre |
| 4.º | U2 | **rechazado** |

El cuarto intento no llegó siquiera al resumen: cortó antes de escribir nada
en la base.

```
*DISPONIBILIDAD*
――――――――――――――――――
No está disponible en ese horario:
    • iMac 24 M3
――――――――――――――――――
¿Qué quieres hacer?
1. Quitar esos activos y continuar con el resto de la reserva
2. Cancelar esta reserva y elegir otro activo
```

### F. Conflicto sobre un recurso único — correcto

U2 pidió la Cabina de audio 2 en el mismo rango que U1. Mismo patrón de
rechazo. Al ser un solo recurso físico no hay unidad alternativa, y el bot
lo bloqueó bien.

### G. Consulta de disponibilidad — **aquí salieron los dos defectos**

*"que computadores tienen disponibles"* →
`- MacBook Pro 14 M3: disponible ahora (2 de 2 unidades libres)` /
`- iMac 24 M3: disponible ahora (3 de 3 unidades libres)`

*"esta libre la cabina de audio 2"* →
`Cabina de audio 2: libre ahora, pero reservado del 15/09 19:00 a las 21:00`

Ver la sección 3: ese **19:00 está mal**, y el "ahora" es una limitación.

### H. Aislamiento entre usuarios y cancelación — correcto, y es lo que más importaba

U2: *"cancelar mi reserva"* →

```
*TUS RESERVAS ACTIVAS*
1. Micrófono Shure SM7B / Brazo articulado Rode PSA1 — 15/09/2026 9:00am–11:00am
2. iMac 24 M3 — 15/09/2026 9:00am–11:00am
```

**La lista contiene exclusivamente las reservas de Carlos. Ninguna de María
aparece**, pese a que María tenía cuatro reservas activas, dos de ellas en
ese mismo rango horario. Se canceló la del micrófono: las filas 64 y 65
quedaron `cancelada`, el resto intacto.

---

## 3. Defectos encontrados

### 3.1 La disponibilidad muestra la hora en UTC (alto)

La reserva de la Cabina de audio 2 es de **14:00 a 16:00 hora de Colombia**.
El bot la reporta así:

> Cabina de audio 2: libre ahora, pero reservado del 15/09 **19:00** a las **21:00**

19:00 y 21:00 son los valores **UTC crudos** de la tabla. Verificado contra
la base: `inicio = 2026-09-15 19:00:00+00`, que son las 14:00 en Bogotá.

**Causa.** En `flask-app/app/recursos_service.py`, `_estado_recurso` y
`_estado_grupo` formatean directamente el `datetime` que devuelve
SQLAlchemy, sin convertirlo:

```python
return f"libre ahora, pero reservado del {proxima.inicio.strftime('%d/%m %H:%M')} ..."
```

La columna es `DateTime(timezone=True)` y vuelve en UTC. El resto del bot
nunca hace esto: usa `_formatear_hora(dt_local)` sobre un valor ya pasado
por `.astimezone(ZONA_HORARIA_LACS)`, y por eso el wizard y las
confirmaciones sí muestran "2:00 p.m.". Son tres puntos de formateo, los
tres en las mismas dos funciones.

**Por qué importa.** No es cosmético: al usuario se le informa una hora que
está cinco horas corrida, y encima con un formato (24h, sin a.m./p.m.)
distinto al del resto del bot, lo que hace más difícil notar el error.

**No está arreglado**: se reporta, no se tocó código en este ejercicio.

### 3.2 No se puede consultar disponibilidad para una fecha futura (medio)

`evaluar_consulta_disponibilidad` responde siempre respecto de `ahora`:
arranca con `ahora = datetime.now(timezone.utc)` y compara contra reservas
en curso. Por eso *"que computadores tienen disponibles"* contestó
*"3 de 3 unidades libres"* aun con las tres unidades ya reservadas para el
15/09: hoy, 9 de septiembre, efectivamente están libres.

La respuesta no es incorrecta, pero es la que menos le sirve a alguien que
está por reservar: no hay forma de preguntar *"¿hay iMac libres el 15 a las
9?"* sin meterse en el wizard y llegar hasta el paso de la hora para que el
bot te diga que no.

### 3.3 "Un lugar con sus activos" no existe (medio)

Detallado en el escenario C. `espacio_id` es hoy una relación de inventario
que no llega a la conversación. Un usuario que quiera la sala y sus
computadores tiene que saber que existe la opción *"Agregar otro activo"*
en el menú de confirmación, y agregarlos uno por uno.

Si se quisiera cerrar, el punto de entrada natural es `_iniciar_reserva`:
cuando el activo resuelto es `tipo='espacio'` y tiene contenidos, ofrecer
esos contenidos igual que hoy se ofrecen los accesorios por `padre_id`
(`_accesorios_por_defecto` ya es exactamente ese patrón, sobre la otra
jerarquía).

---

## 4. Observaciones menores de redacción

- Al elegir la categoría "Computador", el bot contesta *"Como solo tenemos
  una marca de computador (Apple), sigo con esa."* Funciona, pero suena a
  mensaje interno más que a texto para el usuario final.
- El wizard pide y espera las horas en formato 12h (`3:00pm`); nunca acepta
  24h en ese paso.

## 5. Cosas que funcionaron y conviene dejar anotadas

- **El rate limiting se disparó dos veces** durante la corrida (8 mensajes
  por minuto y por número). Devolvió `{"status":"rate_limited"}` sin llegar
  a procesar el mensaje ni escribir en auditoría; se resolvió esperando y
  reenviando. El guardrail hace lo que promete, pero conviene saber que un
  wizard largo puede rozarlo.
- **El envío por Baileys sí funcionó** con estos números (`{"status":"ok"}`),
  a diferencia del número de pruebas `000000000000` usado en
  `docs/PRUEBAS_CHAT_2026-09-09.md`, que siempre falla al entregar.
- Ningún rechazo por falta de disponibilidad escribió nada en la base: el
  bot corta antes del resumen de confirmación.
- Las reservas protegidas (47, 48, 49, del "Usuario de Pruebas") quedaron
  intactas y `confirmada`.

---

## 6. Estado final de la base

| id | usuario | recurso | rango (hora Colombia) | estado | n.º reserva |
|---|---|---|---|---|---|
| 60 | María (U1) | iMac 24 M3 (43) | 15/09 09:00–11:00 | confirmada | 19 |
| 61 | María (U1) | Cabina de audio 2 (42) | 15/09 14:00–16:00 | confirmada | 20 |
| 62 | María (U1) | Sala de postproducción 4 (41) | 16/09 08:00–12:00 | confirmada | 21 |
| 63 | María (U1) | MacBook Pro 14 M3 (46) | 16/09 08:00–12:00 | confirmada | 21 |
| 64 | Carlos (U2) | Micrófono Shure SM7B (48) | 15/09 09:00–11:00 | **cancelada** | 22 |
| 65 | Carlos (U2) | Brazo articulado Rode (50) | 15/09 09:00–11:00 | **cancelada** | 22 |
| 66 | Carlos (U2) | iMac 24 M3 (44) | 15/09 09:00–11:00 | confirmada | 23 |
| 67 | María (U1) | iMac 24 M3 (45) | 15/09 09:00–11:00 | confirmada | 24 |

Las filas 62 y 63 comparten número de reserva: es el lugar y su activo
armados a mano, en una sola reserva. Las tres unidades de iMac (43, 44, 45)
quedaron repartidas entre los dos usuarios en el mismo horario, sin
solaparse.

---

## 7. Qué haría falta decidir

1. **Arreglar la hora en UTC** (3.1). Es un cambio de tres líneas en dos
   funciones de `recursos_service.py`, más una verificación. Es el único
   hallazgo que le muestra al usuario un dato falso.
2. **Decidir si "un lugar con sus activos" debe existir** (3.3). El patrón
   ya está escrito para `padre_id`; replicarlo sobre `espacio_id` es un
   trabajo acotado, pero es una decisión de producto, no una corrección.
3. **Decidir si la disponibilidad debe aceptar una fecha** (3.2). Hoy
   obliga a entrar al wizard para descubrir un conflicto.
4. **Datos de prueba en la base.** Los lugares 40-42 y los activos 43-50, y
   las reservas 60-67, quedaron creados. Si esta base es la de producción,
   conviene limpiarlos antes de usarla de verdad.
