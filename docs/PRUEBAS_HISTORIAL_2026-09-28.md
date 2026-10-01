# Reporte de pruebas: historial de estados y puntualidad

**Fecha:** 28 de septiembre de 2026
**Alcance:** la tabla `reserva_estado_historial` (y su trigger), la vista
`reserva_puntualidad` y el arreglo de la bandera `entrega_tardia`.
**Entorno:** todo dentro de Docker, contra la base viva (`reservas_db`). Script:
[`entregables/OE3/pruebas/verificar_historial.py`](../entregables/OE3/pruebas/verificar_historial.py), que
corre con

```
docker exec -i lacs_flask_app python - < entregables/OE3/pruebas/verificar_historial.py
```

**Usuario:** solo Habid Abdala (`usuarios.id = 24`, `573045246117`), el único
número con WhatsApp real en este entorno. **No se mandó ningún mensaje.** Cada
escenario se crea, se transiciona y se confirma en una sola transacción, así
que el barrido de avisos, que solo mira reservas `confirmada`, nunca llega a
ver una reserva de prueba. Las transiciones se hacen llamando directamente a
`actas_service.aplicar_transicion`, sin escribir PDF de actas a disco.

**Evidencia:** se dejó en la base a propósito. Son las reservas 20 a 23
(grupos 14 a 17) y las filas 28 a 40 del historial.

---

## Resumen ejecutivo

**Seis escenarios, seis pasan.**

| # | Escenario | Qué se comprueba | Resultado |
|---|---|---|---|
| 1 | Recoge 10 min antes y devuelve a tiempo | retraso de entrega negativo, `completada` | ✅ −10 min |
| 2 | Recoge 10 min tarde, dentro del margen de no-show | `entrega_tardia = false`, retraso +10 | ✅ |
| 3 | Recoge 45 min tarde **sin** pasar por `no_show` | `entrega_tardia = true` (**antes quedaba `false`**) | ✅ |
| 3b | Se le adelanta el `fin` y devuelve después | el cambio de horario queda registrado, `devuelta_tarde` +1 min | ✅ |
| 4 | `no_show` y después entrega | cadena `confirmada → no_show → entregada` | ✅ |
| 5 | Se borra la reserva 21 completa | su historial y su retraso siguen consultables | ✅ |
| 6 | La app intenta `UPDATE`/`DELETE` sobre el historial | Postgres lo rechaza (permission denied) | ✅ |

---

## Cómo queda en la base

Horas en `America/Bogota`.

### `reservas`: estado actual

La 21 no aparece porque se borró en el escenario 5.

```
 id | grupo_id | recurso_id | inicio |  fin  |     estado     | entrega_tardia
----+----------+------------+--------+-------+----------------+----------------
 20 |       14 |          1 | 10:45  | 12:35 | completada     | f
 22 |       16 |          4 | 09:50  | 10:34 | devuelta_tarde | t
 23 |       17 |          5 | 09:50  | 12:35 | entregada      | t
```

### `reserva_estado_historial`: todo lo que pasó

```
 id | reserva_id | grupo_id | usuario_id | recurso_id | inicio |  fin  |  anterior  |     nuevo      |     cambiado_en
----+------------+----------+------------+------------+--------+-------+------------+----------------+---------------------
 28 |         20 |       14 |         24 |          1 | 10:45  | 12:35 | (alta)     | confirmada     | 2026-09-28 10:35:21
 29 |         20 |       14 |         24 |          1 | 10:45  | 12:35 | confirmada | entregada      | 2026-09-28 10:35:21
 30 |         20 |       14 |         24 |          1 | 10:45  | 12:35 | entregada  | completada     | 2026-09-28 10:35:21
 31 |         21 |       15 |         24 |          3 | 10:25  | 12:35 | (alta)     | confirmada     | 2026-09-28 10:35:21
 32 |         21 |       15 |         24 |          3 | 10:25  | 12:35 | confirmada | entregada      | 2026-09-28 10:35:21
 33 |         22 |       16 |         24 |          4 | 09:50  | 12:35 | (alta)     | confirmada     | 2026-09-28 10:35:21
 34 |         22 |       16 |         24 |          4 | 09:50  | 12:35 | confirmada | entregada      | 2026-09-28 10:35:21
 35 |         22 |       16 |         24 |          4 | 09:50  | 10:34 | entregada  | entregada      | 2026-09-28 10:35:21
 36 |         22 |       16 |         24 |          4 | 09:50  | 10:34 | entregada  | devuelta_tarde | 2026-09-28 10:35:21
 37 |         23 |       17 |         24 |          5 | 09:50  | 12:35 | (alta)     | confirmada     | 2026-09-28 10:35:21
 38 |         23 |       17 |         24 |          5 | 09:50  | 12:35 | confirmada | no_show        | 2026-09-28 10:35:21
 39 |         23 |       17 |         24 |          5 | 09:50  | 12:35 | no_show    | entregada      | 2026-09-28 10:35:21
 40 |         21 |       15 |         24 |          3 | 10:25  | 12:35 | entregada  | (borrada)      | 2026-09-28 10:35:21
```

Cómo leer la tabla:

- **Fila 35**: `entregada → entregada` no es un cambio de estado sino de
  horario. `fin` pasó de 12:35 a 10:34, y la fila guarda el horario nuevo.
- **Fila 40**: la reserva 21 se borró. En `reservas` ya no existe, pero aquí
  queda con su horario y su usuario.
- **Todas las horas son iguales**: `cambiado_en` es `now()`, que en Postgres
  es la hora de *inicio de la transacción*, y cada escenario corrió en una
  sola transacción. En uso real, cada acta es su propio `commit` y cada fila
  lleva su propia hora.

### `reserva_puntualidad`: la métrica

```
 reserva_id |   evento   |     estado     | ocurrido | retraso_min
------------+------------+----------------+----------+-------------
         20 | entrega    | entregada      | 10:35    |         -10
         20 | devolucion | completada     | 10:35    |        -120
         21 | entrega    | entregada      | 10:35    |          10
         22 | entrega    | entregada      | 10:35    |          45
         22 | devolucion | devuelta_tarde | 10:35    |           1
         23 | entrega    | entregada      | 10:35    |          45
```

La reserva 21 sigue apareciendo aunque ya no exista en `reservas`.

### Consulta de ejemplo: puntualidad por estudiante en el último año

```sql
SELECT u.nombre,
       COUNT(*) FILTER (WHERE p.evento = 'entrega')                          AS entregas,
       COUNT(*) FILTER (WHERE p.evento = 'entrega' AND p.retraso > interval '0') AS recogio_tarde,
       COUNT(*) FILTER (WHERE p.estado = 'devuelta_tarde')                   AS devolvio_tarde
FROM reserva_puntualidad p JOIN usuarios u ON u.id = p.usuario_id
WHERE p.ocurrido_en > now() - interval '1 year'
GROUP BY u.nombre;
```

```
    nombre    | entregas | recogio_tarde | devolvio_tarde
--------------+----------+---------------+----------------
 Habid abdala |       13 |             4 |              2
```

De esas 13 entregas, 9 vienen del backfill desde las actas anteriores a hoy
(historial ids 1 a 11: 9 entregas y 2 devoluciones) y 4 son de esta prueba.
Los ids 12 a 27 eran de la primera corrida y se borraron (ver Notas).

---

## Notas

- **Una reserva de prueba sigue ocupando un equipo.** La 23 queda `entregada`
  con `fin` a las 12:35 del 28/09. Hasta esa hora ocupa el recurso 5 (Dron DJI
  Mavic), y después el panel la mostrará como *Retrasada*, porque nadie firma
  su devolución. Si se vuelve a correr el script antes de las 12:35, hay que
  cambiar `RECURSOS`.
- **Pruebas previas del mismo día.** Hubo una primera corrida (reservas 16 a
  19) que sí limpiaba al final. Sus filas de historial se borraron como
  superusuario para no ensuciar las métricas; por eso esta corrida empieza en
  la reserva 20.
