# Reporte de pruebas: los ocho estados de reserva y las seis notificaciones

**Fecha:** 22 de septiembre de 2026
**Alcance:** los ocho valores de `reservas.estado`, las dos cosas que NO son
estados (`entrega_tardia` y `retrasada`), y las seis notificaciones de WhatsApp
que el sistema puede mandarle a un estudiante.
**Entorno:** todo dentro de Docker, contra la base viva. Las reservas se
crearon con `recursos_service.crear_reservas_grupo()` por `python -c` dentro
del contenedor; las transiciones y actas, por la API real del dashboard
(`curl` desde el host contra `127.0.0.1:5000`, auth Basic). Los avisos, por
`avisos_reservas.barrer(ahora=...)`.
**Usuario:** solo Habid Abdala (`usuarios.id = 24`, `573045246117`), el único
número con WhatsApp real en este entorno. Se mandaron **9 mensajes de verdad**
a ese teléfono.

La base de reservas se había vaciado por completo justo antes (ver
"Punto de partida"), así que los grupos van del 1 al 8 sin ruido previo.

---

## Resumen ejecutivo

**Ocho escenarios, ocho pasan.** Los ocho estados quedaron cubiertos, las seis
notificaciones se dispararon (o no se dispararon, donde eso era la asserción) y
los 9 mensajes llegaron al teléfono: ninguno quedó en `pendiente` ni
`error_http`.

Lo que más importaba validar pasó:

- La **entrega tardía no extiende el horario**: `fin` quedó idéntico al
  microsegundo después de firmar la entrega sobre un `no_show`.
- La bandera `entrega_tardia` **sobrevive a la devolución** (sigue en `true`
  cuando el estado ya es `completada`), que era la razón de haberla hecho
  columna y no estado.
- El borde `min(inicio + margen, fin)` está bien **por los dos lados**: con una
  reserva de 15 minutos, a los 14 no dispara y a los 15 sí.

Salió **un hallazgo de producto**, no un defecto de código:

| # | Qué | Gravedad |
|---|---|---|
| 1 | Un barrido de recuperación tras una caída pasa a `no_show` todas las `confirmada` vencidas **sin avisarle a nadie** | Medio — decisión de producto pendiente |

Y una observación que conviene tener anotada: **registrar un acta no notifica
nada**. Entregar y devolver equipo es silencioso para el estudiante.

---

## Punto de partida

Antes de estas pruebas se vaciaron por completo las reservas (87 filas, 39
grupos, 37 avisos, 6 actas firmadas) con un `TRUNCATE ... RESTART IDENTITY`
sobre las seis tablas del cierre transitivo:

    reserva_grupos, reservas, reserva_grupo_integrantes,
    reserva_actas, reserva_avisos, secuencias_confirmacion

Dos cosas que un `DELETE FROM reservas` no cubre y hay que hacer aparte:

- Los **PDFs en el volumen `actas_pdf`**. `reserva_actas.archivo` guarda el
  nombre del archivo, no el binario: borrar la fila deja el PDF huérfano en
  `/app/actas/<grupo_id>/`.
- La llave `avisos_reservas:barrido` de Redis (no es grave, expira sola).

**`reservas_user` no puede hacer TRUNCATE.** Las tablas son de `postgres` y
`TRUNCATE` es un privilegio aparte del DML. Para vaciar hace falta
`psql -U postgres`; para `SELECT`/`UPDATE` basta `reservas_user`.

---

## La técnica: inyectar el tiempo, no esperarlo

Esto es lo más reutilizable de esta sesión.

`avisos_reservas.barrer(ahora=None)` acepta el instante como parámetro. Esa es
la costura de prueba: en vez de esperar una hora a que el ticker real llegue a
la hora de la reserva, se crean reservas con `inicio` en el **futuro** y se
llama a `barrer` con el `ahora` que toque. Los tres avisos de un ciclo completo
salen en segundos, en orden y de forma determinista.

De paso, las aísla del ticker real que corre cada 60 s en paralelo: su filtro es
`estado='confirmada' AND inicio <= now() + AVISO_PREVIO_MIN`, así que una
reserva con `inicio = now + 3h` le es invisible hasta que la prueba la mueva.

**Regla dura: ninguna reserva de prueba nace con `inicio` en el pasado.** Si
nace en el pasado, el ticker real la agarra antes que la prueba y dispara
WhatsApp fuera de secuencia.

Dos funciones **no** aceptan `ahora` y usan el reloj del sistema:
`actas_service.aplicar_transicion` y `actas_service.esta_retrasada`. Para probar
`devuelta_tarde` y `retrasada` hay que retroceder `fin` con un UPDATE directo.
Es seguro porque el barrido solo mira `confirmada`, y una fila `entregada` ya
salió de ese filtro.

---

## Lo que existe: ocho estados, dos no-estados, seis notificaciones

Los ocho valores de `reservas.estado` (ver el comentario de esa columna en
`postgres/init/02-schema.sql`, que es la fuente de verdad):

    confirmada | pendiente | denegada | cancelada
    entregada  | completada | devuelta_tarde | no_show

Dos cosas que **no** son estados y conviene no confundir:

- **`entrega_tardia`** — columna booleana sobre `entregada`. Se decide una vez,
  al firmar la entrega de un grupo que estaba en `no_show`. Es columna y no
  estado justamente para sobrevivir a la devolución, que sí cambia `estado`.
- **`retrasada`** — derivado en caliente: `estado='entregada' AND fin < now()`.
  No se persiste porque cambia solo con el paso del tiempo, sin que nadie
  accione nada; guardarlo obligaría a un worker que lo recalculara.

Las seis notificaciones. **Son las únicas seis que existen**: registrar un acta
no manda nada, y cambiar el estado a mano desde el selector del dashboard
tampoco.

| `intent_detectado` | Texto | Quién lo dispara |
|---|---|---|
| `aviso_previo` | `mensaje_aviso_previo` | barrido, `inicio - AVISO_PREVIO_MIN` |
| `aviso_hora` | `mensaje_hora_recogida` | barrido, `inicio` |
| `aviso_no_show` | `mensaje_no_show` | barrido, `min(inicio + NO_SHOW_MARGEN_MIN, fin)` |
| `confirmacion_lacs` | `mensaje_reserva_confirmada` | `POST /api/reservas/grupo/<id>/confirmar` |
| `denegacion_lacs` | `mensaje_decision_lacs` | `POST /api/reservas/<id>/denegar` |
| `reserva_dashboard` | `mensaje_reserva_confirmada` | `POST /api/reservas` con `notificar: true` |

---

## Cómo se verifica

Tres fuentes, las tres obligatorias. **No basta con el valor que devuelve una
función**: hay que confirmarlo contra la base.

1. `reservas.estado` y `reservas.entrega_tardia`, por SQL.
2. `reserva_avisos` (`tipo`, `enviado`, `motivo`), por SQL.
3. `auditoria_bot` filtrado al teléfono: `intent_detectado` y **`estado_envio`**.

El tercero es el único que prueba entrega real. El ack de Baileys
(`POST /webhook/ack`) mueve `estado_envio` a `entregado` y luego a `leido`; si
se queda en `pendiente` o `error_http`, el mensaje **no llegó**, aunque
`avisar_usuario` haya devuelto `True`.

Para `retrasada` y `entrega_tardia` ya serializados, `GET /api/reservas`.

---

## Resultados

| Escenario | Grupo | Estado esperado | Estado real | Notificación | Resultado |
|---|---|---|---|---|---|
| E1 Ciclo completo | 1 | `confirmada`→`no_show`→`entregada`(tardía)→`completada` | Igual en cada paso; `fin` sin cambiar | `aviso_previo`, `aviso_hora`, `aviso_no_show` — las 3 `leido` | **PASA** |
| E2 Retrasada y devolución tarde | 2 | `entregada`(tardía=false) → `retrasada` false→true → `devuelta_tarde` | Igual | Ninguna (0 filas) | **PASA** |
| E3 Pendiente confirmada | 3 | `pendiente`→`confirmada` | Igual | `confirmacion_lacs` / `leido` | **PASA** |
| E4 Pendiente denegada | 4 | `pendiente`→`denegada` | Igual | `denegacion_lacs` / `entregado` | **PASA** |
| E5 Cancelada, sin aviso | 5 | `confirmada`→`cancelada`, 0 filas nuevas | Igual | Ninguna (asserción negativa) | **PASA** |
| E6 Alta desde dashboard | 6 | `confirmada`, `notificado: true` | Igual, `canal_origen=dashboard` | `reserva_dashboard` / `entregado` | **PASA** |
| E7 Corte de frescura | 7 | 3 avisos `vencido`, `no_show` aplicado, 0 mensajes | Igual | Ninguna (asserción negativa) | **PASA** |
| E8 Borde `min(inicio+margen, fin)` | 8 | +14 min: sin no-show; +15 min: no-show | Igual en ambos barridos | `aviso_previo`, `aviso_hora`, `aviso_no_show` | **PASA** |

### E1 — el ciclo completo, y la regla del horario

Reserva de 4 horas (`inicio = now + 3h`, `fin = now + 7h`), el ejemplo que
motivó la funcionalidad. Se barrió a `inicio - 30min`, a `inicio` y a
`inicio + 30min`; los tres avisos salieron y el grupo pasó a `no_show`.

Después se firmó la entrega **sobre el `no_show`**, que es la entrega tardía:
el estudiante llegó después del margen pero sigue dentro de su ventana. Quedó
`entregada` con `entrega_tardia = true`.

La asserción que importa: **`fin` no se movió**. Siguió en
`2026-09-23 05:25:25.327282+00`, el mismo microsegundo con el que nació. El que
llega una hora tarde a una reserva de 4 horas se queda con 3, no con 4.

Al firmar la devolución pasó a `completada` y **`entrega_tardia` siguió en
`true`**. Eso es lo que justifica que sea columna y no estado.

### E2 — `retrasada` es derivado, `devuelta_tarde` se decide una vez

Se firmó la entrega de inmediato sobre una `confirmada`: quedó `entregada` con
`entrega_tardia = false`, el contraste limpio con E1.

Con `fin` todavía en el futuro, `GET /api/reservas` daba `retrasada: false`.
Tras retroceder `inicio` y `fin` con un UPDATE, la misma fila pasó a
`retrasada: true` **sin que nadie tocara nada**: eso es exactamente lo que
significa que sea derivado.

Al firmar la devolución con el reloj ya pasado `fin`, quedó `devuelta_tarde`.
Ese sí se persiste, porque se decide una sola vez y debe quedar para siempre en
la métrica de atrasos.

### E5 y E7 — las dos aserciones negativas

Las dos comprueban que **no** pasa algo, y las dos pasaron:

- **E5**: cancelar desde el dashboard (`PATCH .../grupo/<id>` con
  `estado: cancelada`) no genera ninguna fila en `auditoria_bot`. Cambiar el
  estado a mano no le escribe al estudiante.
- **E7**: barriendo con un `ahora` cinco horas adelantado, los tres avisos
  quedaron con `enviado = false, motivo = 'vencido'` y **cero mensajes**. El
  `no_show` sí se aplicó. Es el corte de frescura funcionando: un "recoges en 30
  minutos" que sale seis horas tarde no es un aviso atrasado, es información
  falsa.

### E8 — el borde que se dio en producción

Reserva de 15 minutos, más corta que el margen de no-show de 30. El deadline
debe ser `fin`, no `inicio + 30min`.

Se barrió a `inicio + 14min`: salieron `previo` y `hora`, y **`no_show: 0`**.
Se barrió a `inicio + 15min`: **`no_show: 1`**.

Probarlo por los dos lados es lo que distingue que el `min` esté bien de que el
resultado salga por casualidad. Sin el `min`, el paso de los 15 minutos
fallaría. Este caso ya se había dado con el grupo 64 en la sesión anterior.

---

## Los nueve mensajes, literales

Sacados de `auditoria_bot.respuesta_enviada`, ids 860–868, en orden
cronológico. Sirven para revisar la redacción sin volver a mandarlos.

1. `aviso_previo` — ⏰ Recordatorio: en 30 minutos empieza tu reserva N.º 1
   (martes 22/09/2026, 8:25 p.m.). Te esperamos en la LACS a recoger Trípode
   Manfrotto.
2. `aviso_hora` — 🕐 Ya es la hora de tu reserva N.º 1: Trípode Manfrotto. Pasa
   por la LACS a recoger -- si no te presentas en los próximos 30 minutos, el
   equipo queda libre para otra persona.
3. `aviso_no_show` — ⚠️ Tu reserva N.º 1 (Trípode Manfrotto) se liberó por no
   presentarte a tiempo. Si todavía estás dentro de tu horario (hasta las 12:25
   a.m.), puedes pasar por la LACS igual y te lo entregamos.
4. `confirmacion_lacs` — *RESERVA CONFIRMADA* / *Cámara Sony A7III* / N.º de
   reserva: 3 / Inicio: martes 22/09/2026, 9:27 p.m. / Fin: martes 22/09/2026,
   11:27 p.m. / ✅ El equipo de la LACS confirmó tu reserva completa.
5. `denegacion_lacs` — ❌ La LACS no aprobó el préstamo de *Cámara Canon EOS R*
   de tu reserva N.º 4 (martes 22/09/2026, 9:27 p.m.), así que no hace parte de
   la reserva. Como no le quedan otros activos, la reserva queda anulada. Si
   necesitas otro equipo, escríbeme.
6. `reserva_dashboard` — *RESERVA CONFIRMADA* / *Sala de edición 1* / N.º de
   reserva: 6 / Inicio: martes 22/09/2026, 10:28 p.m. / Fin: martes 22/09/2026,
   11:28 p.m. / ✅ El equipo de la LACS confirmó tu reserva completa.
7. `aviso_previo` — mismo formato que (1), reserva N.º 8, Lente Sony 70-200mm.
8. `aviso_hora` — mismo formato que (2), reserva N.º 8.
9. `aviso_no_show` — mismo formato que (3), reserva N.º 8, "hasta las 8:44 p.m."
   (el `fin` de la reserva de 15 minutos, no `inicio + 30min` — ver E8).

Los 9 quedaron en `entregado` o `leido`. Seis llegaron a `leido` en segundos.

---

## Hallazgo: el barrido de recuperación es silencioso

Esto salió como efecto secundario de la metodología, pero **no es un artefacto
de prueba**: es lo que va a pasar en producción.

Para probar E7 hubo que barrer con un `ahora` cinco horas adelantado.
`barrer()` **no tiene límite inferior de tiempo** — está documentado a
propósito en el código: *"una reserva vieja que sigue en 'confirmada' es justo
la que hay que barrer"*. Así que esa corrida también alcanzó a los grupos 3 y 6,
que seguían `confirmada`, y los pasó a `no_show`:

    {'grupos': 3, 'avisos_enviados': 0, 'avisos_vencidos': 9, 'no_show': 3}

Sin mandar un solo WhatsApp, que es justo lo que el corte de frescura promete.

**Lo que eso simula es una caída del contenedor.** Si flask-app se cae un rato
largo y el barrido se pone al día de golpe, todas las reservas `confirmada`
vencidas pasan a `no_show` en una sola corrida y **el estudiante no se entera
nunca**, porque su aviso se suprimió por rancio.

En operación normal no ocurre: el aviso sale a tiempo. Después de una caída,
alguien se queda sin equipo y sin mensaje.

La salida **no** es quitar el corte de frescura. Sería peor: mandar el
"recoges en 30 minutos" seis horas tarde es decirle al estudiante algo falso.
Si se quiere cerrar, lo que falta es **un texto distinto para el caso vencido**
("tu reserva de las 3 p.m. se liberó"), en vez de silencio. Es una decisión de
producto, no está tomada.

Consecuencia para leer este reporte: el estado final de los grupos 3 y 6 en la
base ya **no** es el que dejaron sus escenarios. E3 y E6 pasaron sus
aserciones antes de la contaminación — los mensajes 863 y 865 lo prueban— pero
ambos terminaron en `no_show`.

---

## Estado final de la base

| grupo | recurso | estado | `entrega_tardia` | nota |
|---|---|---|---|---|
| 1 | Trípode Manfrotto | `completada` | **true** | ciclo completo con entrega tardía |
| 2 | Micrófono Rode NTG3 | `devuelta_tarde` | false | `fin` retrocedido a mano por E2 |
| 3 | Cámara Sony A7III | `no_show` | false | quedó `confirmada` en E3; lo movió el barrido de E7 |
| 4 | Cámara Canon EOS R | `denegada` | false | |
| 5 | Estudio de grabación A | `cancelada` | false | |
| 6 | Sala de edición 1 | `no_show` | false | quedó `confirmada` en E6; lo movió el barrido de E7 |
| 7 | Lente Sony 24-70mm | `no_show` | false | corte de frescura |
| 8 | Lente Sony 70-200mm | `no_show` | false | reserva de 15 min |

`reserva_avisos`: grupos 1 y 8 con 3 filas `enviado=true, motivo IS NULL`;
grupos 3, 6 y 7 con 3 filas `enviado=false, motivo='vencido'`. Grupos 2, 4 y 5
sin filas (nunca pasaron por `barrer`).

`reserva_actas`: 4 PDFs (entrega y devolución de los grupos 1 y 2).

**Ningún grupo quedó en `confirmada`, `pendiente` ni `entregada`.** Para revisar
esos tres badges en el dashboard —y `retrasada`, que solo se ve sobre
`entregada`— hay que crear grupos nuevos.

---

## Cosas que conviene dejar anotadas

- **`registrar un acta no notifica`**. Entregar y devolver es silencioso para el
  estudiante. Puede ser intencional, pero no estaba escrito en ningún lado.
- **No hay límite de tasa de salida.** `guardrails.excede_limite_mensajes` es
  solo para mensajes ENTRANTES. Los tres avisos de un grupo pueden salir en la
  misma corrida sin espera entre ellos, y así salieron.
- **`jid_wa` importa.** Habid tiene `144186850431118@lid`. Para las cuentas
  `@lid`, `whatsapp_client.jid_de` **no** puede rearmar el JID desde el número:
  si esa columna estuviera vacía, el envío fallaría. Solo se llena cuando el
  usuario escribe.
- **No hay `curl` dentro del contenedor flask.** Hay que llamar la API desde el
  host contra `127.0.0.1:5000`, o usar `requests` por `python -c`.
- En Git Bash sobre Windows, una ruta absoluta estilo Unix pasada a
  `docker compose exec` (por ejemplo `-C /app`) se convierte a ruta de Windows y
  rompe el comando. Anteponer `MSYS_NO_PATHCONV=1`.
- `curl -F "archivo=@ruta;type=application/pdf"` falla con exit 26 en el curl de
  mingw64; sin el sufijo `;type=...` funciona. Es del binario de curl, no del
  backend.

---

## Qué queda por decidir

1. **El texto del caso vencido** (ver el hallazgo de arriba). Hoy un barrido de
   recuperación libera reservas en silencio.
2. **Los dos usuarios de prueba con teléfono real.** Maria Fernanda
   (`573001112233`) y Carlos Andrés (`573004445566`) siguen existiendo. Antes
   eran inofensivos; desde que hay avisos automáticos, cualquier reserva sobre
   ellos les escribe sola. Si esos números le pertenecen a alguien, le llegan
   mensajes de la LACS. Ponerles `000000000000` los neutraliza (es el número que
   no sale a WhatsApp real).
3. Sigue pendiente lo que ya estaba: la carrera de envío-antes-del-commit en
   `barrer()` y la falta de un interruptor único para los avisos automáticos
   (ver `docs/SEGURIDAD.md` y `docs/ARQUITECTURA.md`).
