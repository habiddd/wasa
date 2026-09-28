# Reporte de pruebas: equipos no devueltos

**Fecha:** 28 de septiembre de 2026

**Alcance:** qué pasa con una reserva y con sus equipos cuando el usuario no
devuelve uno o varios. El cambio que se prueba es el de la sección 10 de
[`SESION_2026-09-28.md`](SESION_2026-09-28.md):

- La línea de la reserva pasa a `no_devuelta`.
- El activo pasa a `no_devuelto`.
- Los dos cambios quedan en `recurso_estado_historial`.

**Entorno:** todo dentro de Docker, contra la base viva y contra los endpoints
reales del dashboard (`test_client` de Flask). El script es
[`flask-app/verificar_no_devuelto_evidencia.py`](../flask-app/verificar_no_devuelto_evidencia.py):

```
docker exec -i lacs_flask_app python - < flask-app/verificar_no_devuelto_evidencia.py
```

**Usuario:** solo Habid Abdala (`usuarios.id = 24`). **No se mandó ningún
mensaje.** Las reservas se hicieron para el 30/09, así que el barrido de avisos
nunca vio una reserva de prueba como `confirmada`.

**Evidencia:** no se borró nada. Todo lo siguiente quedó en la base a propósito:

- Grupos 21 y 22 (reservas 34 a 37).
- Actas 14 a 17 y sus PDF en `/app/actas/21` y `/app/actas/22`.
- Filas 80 a 91 de `reserva_estado_historial`.
- Filas 4 a 7 de `recurso_estado_historial`.

**Quedan en `no_devuelto` a propósito:**

- Cámara Canon EOS R (recurso 2).
- Micrófono Rode NTG3 (recurso 4).

Para volver a prestarlos hay que cambiarlos a *Disponible* en la pestaña
Activos. Esa es justamente la acción que se probó con el trípode.

---

## Resumen ejecutivo

**Todo pasa.**

| # | Qué se hizo | Qué se esperaba | Resultado |
|---|---|---|---|
| 1 | Reserva de 3 equipos (grupo 21); se firma la entrega con los 3 marcados | las 3 líneas quedan `entregada` | ✅ |
| 2 | Se firma la devolución con solo la cámara Sony marcada | Sony `completada`; Canon y trípode `no_devuelta` | ✅ |
| 3 | Se consulta si el acta sigue pendiente | no pide otra devolución (`momento_siguiente = None`) | ✅ **antes la pedía para siempre** |
| 4 | Se revisa el estado de los activos | Sony `disponible`; Canon y trípode `no_devuelto` | ✅ **antes seguían `disponible`** |
| 5 | Se consulta la disponibilidad que usa el bot para reservar | ofrece la Sony; no ofrece Canon ni trípode | ✅ |
| 6 | Se intenta crear una reserva de la Canon desde el dashboard | 400, "está no_devuelto" | ✅ |
| 7 | Se intenta poner la Sony en `no_devuelto` a mano desde Activos | 409, solo se llega con el acta | ✅ |
| 8 | La LACS recupera el trípode y lo pasa a `disponible` en Activos | 200, queda en el historial y el bot vuelve a ofrecerlo | ✅ |
| 9 | Reserva de 1 equipo (grupo 22); se firma la devolución **sin nada marcado** | micrófono `no_devuelta` y `no_devuelto` | ✅ |

---

## Salida del script

```
ESCENARIO 1: grupo 21, lineas {1: 34, 2: 35, 3: 36}
  - reserva creada: ['Cámara Sony A7III (recurso 1)', 'Cámara Canon EOS R (recurso 2)', 'Trípode Manfrotto (recurso 3)'], del 2026-09-30 21:30 al 23:30 UTC
  - acta de ENTREGA firmada con los 3 marcados -> {34: 'entregada', 35: 'entregada', 36: 'entregada'}
  - acta de DEVOLUCION firmada solo con Cámara Sony A7III (recurso 1) marcado -> {34: 'completada', 35: 'no_devuelta', 36: 'no_devuelta'}
  - el acta ya no pide otra devolucion (momento_siguiente = None)
  - activos: Cámara Sony A7III (recurso 1)=disponible, Cámara Canon EOS R (recurso 2)=no_devuelto, Trípode Manfrotto (recurso 3)=no_devuelto
  - el bot ofrece el devuelto y NO ofrece los no devueltos
  - bot, consulta 'Cámara Canon EOS R': 'Esto es lo que tengo:\n- Cámara Canon EOS R6: libre, sin reservas próximas'
  - bot, consulta 'Trípode Manfrotto': 'Te comparto el *catálogo completo* de equipos y espacios de la LACS, ...'
  - dashboard, crear reserva con Cámara Canon EOS R (recurso 2) -> 400 'No se puede reservar Cámara Canon EOS R (10000020): está no_devuelto.'
  - Activos, poner Cámara Sony A7III (recurso 1) a mano en no_devuelto -> 409 "A 'no devuelto' solo se llega registrando el acta de devolución sin marcar ese equipo."
  - Activos, la LACS recupera Trípode Manfrotto (recurso 3) y lo pasa a disponible -> 200; el bot vuelve a ofrecerlo
  - Cámara Canon EOS R (recurso 2) se DEJA en no_devuelto como evidencia
ESCENARIO 2: grupo 22, lineas {4: 37}
  - acta de ENTREGA firmada con Micrófono Rode NTG3 (recurso 4)
  - acta de DEVOLUCION sin nada marcado -> {37: 'no_devuelta'}; Micrófono Rode NTG3 (recurso 4)=no_devuelto, el bot no lo ofrece
  - Micrófono Rode NTG3 (recurso 4) se DEJA en no_devuelto como evidencia
OK. Evidencia: grupos 21 y 22; activos no_devuelto: 2, 4; recuperado: 3.
```

---

## Cómo queda en la base

Horas en `America/Bogota`.

### `reservas`

```
 id | grupo_id | recurso_id |       nombre        |   inicio    |  fin  |   estado
----+----------+------------+---------------------+-------------+-------+-------------
 34 |       21 |          1 | Cámara Sony A7III   | 30/09 16:30 | 18:30 | completada
 35 |       21 |          2 | Cámara Canon EOS R  | 30/09 16:30 | 18:30 | no_devuelta
 36 |       21 |          3 | Trípode Manfrotto   | 30/09 16:30 | 18:30 | no_devuelta
 37 |       22 |          4 | Micrófono Rode NTG3 | 30/09 16:30 | 18:30 | no_devuelta
```

La reserva 36 sigue en `no_devuelta` aunque el trípode ya esté `disponible`.
Es lo correcto: esa reserva **no** devolvió el equipo, y lo que la LACS hizo
después con el activo es otro hecho, registrado en otra tabla.

### `reserva_estado_historial`: qué pasó con cada línea

```
 id | reserva_id | grupo_id | usuario_id | recurso_id |  anterior  |    nuevo    | cambiado_en
----+------------+----------+------------+------------+------------+-------------+--------------
 80 |         34 |       21 |         24 |          1 | (alta)     | confirmada  | 16:30:30.559
 81 |         35 |       21 |         24 |          2 | (alta)     | confirmada  | 16:30:30.559
 82 |         36 |       21 |         24 |          3 | (alta)     | confirmada  | 16:30:30.559
 83 |         34 |       21 |         24 |          1 | confirmada | entregada   | 16:30:30.659
 84 |         35 |       21 |         24 |          2 | confirmada | entregada   | 16:30:30.659
 85 |         36 |       21 |         24 |          3 | confirmada | entregada   | 16:30:30.659
 86 |         34 |       21 |         24 |          1 | entregada  | completada  | 16:30:30.700
 87 |         35 |       21 |         24 |          2 | entregada  | no_devuelta | 16:30:30.700
 88 |         36 |       21 |         24 |          3 | entregada  | no_devuelta | 16:30:30.700
 89 |         37 |       22 |         24 |          4 | (alta)     | confirmada  | 16:30:30.765
 90 |         37 |       22 |         24 |          4 | confirmada | entregada   | 16:30:30.771
 91 |         37 |       22 |         24 |          4 | entregada  | no_devuelta | 16:30:30.792
```

### `recurso_estado_historial`: qué pasó con cada activo

```
 id | recurso_id | estado_anterior | estado_nuevo | cambiado_en
----+------------+-----------------+--------------+--------------
  4 |          2 | disponible      | no_devuelto  | 16:30:30.700
  5 |          3 | disponible      | no_devuelto  | 16:30:30.700
  6 |          3 | no_devuelto     | disponible   | 16:30:30.729
  7 |          4 | disponible      | no_devuelto  | 16:30:30.792
```

- **Filas 4 y 5:** tienen la misma hora que las filas 87 y 88 de la tabla
  anterior. El acta, la línea y el activo cambian en una sola transacción.
- **Fila 6:** es la LACS recuperando el trípode desde Activos.

### `reserva_actas`

```
 id | grupo_id |  momento   | origen  |                  archivo                  | firmado_por           | nota
----+----------+------------+---------+-------------------------------------------+-----------------------+-------------------------------------------
 14 |       21 | entrega    | firmada | 21/20260928T213030-entrega-firmada.pdf    | Habid Abdala (prueba) |
 15 |       21 | devolucion | firmada | 21/20260928T213030-devolucion-firmada.pdf | Habid Abdala (prueba) | Prueba: no trajo dos de los tres equipos.
 16 |       22 | entrega    | firmada | 22/20260928T213030-entrega-firmada.pdf    | Habid Abdala (prueba) |
 17 |       22 | devolucion | firmada | 22/20260928T213030-devolucion-firmada.pdf | Habid Abdala (prueba) | Prueba: no devolvió nada.
```

Los PDF los generó el servidor y se subieron tal cual. Por eso **no tienen
firma ni las marcas ✓/X**: esas se estampan en el navegador con pdf-lib al
firmar desde el dashboard.

### Por qué no está disponible cada activo

Esta consulta cruza los dos historiales por activo y por hora exacta:

```sql
SELECT h.recurso_id, h.estado_nuevo, r.reserva_id, r.grupo_id, r.usuario_id
FROM recurso_estado_historial h
JOIN reserva_estado_historial r
  ON r.recurso_id = h.recurso_id AND r.cambiado_en = h.cambiado_en
 AND r.estado_nuevo = 'no_devuelta'
WHERE h.estado_nuevo = 'no_devuelto';
```

```
 recurso_id | estado_nuevo | reserva_id | grupo_id | usuario_id
------------+--------------+------------+----------+------------
         19 | no_devuelto  |         31 |       19 |         24
          2 | no_devuelto  |         33 |       20 |         24
          2 | no_devuelto  |         35 |       21 |         24
          3 | no_devuelto  |         36 |       21 |         24
          4 | no_devuelto  |         37 |       22 |         24
```

- **Grupo 19:** es la reserva real del lente, arreglada con UPDATE.
- **Grupo 20:** es la corrida anterior de `verificar_no_devuelto.py`. Esa
  prueba borra su reserva al terminar, pero su historial se conserva.

Métrica de cuántos equipos no devolvió cada usuario:

```sql
SELECT usuario_id, count(*) FROM reserva_estado_historial
WHERE estado_nuevo = 'no_devuelta' GROUP BY 1;
-- usuario 24: 5
```

### Activos que no están disponibles ahora

```
 id |          nombre          |    estado
----+--------------------------+---------------
  2 | Cámara Canon EOS R       | no_devuelto
  4 | Micrófono Rode NTG3      | no_devuelto
 19 | Lente Sony 24-70mm f/2.8 | no_devuelto
 40 | Edificio LACS            | no_disponible
```

---

## Observación: el bot cuando preguntan por un equipo no devuelto

El bot ya no presta un activo `no_devuelto`, porque solo trabaja con activos
`disponible`. Pero **no dice que ese equipo no está**:

- **Si se pregunta por "Cámara Canon EOS R",** responde con el modelo más
  parecido que sí está disponible: *"Cámara Canon EOS R6: libre"*. Es cierto
  que la R6 está libre, pero puede confundir a quien buscaba la EOS R.
- **Si se pregunta por "Trípode Manfrotto",** no lo encuentra y manda el
  enlace al catálogo.

No se cambió nada del bot. Si se quiere que responda algo como *"ese equipo
no está disponible por ahora"*, habría que hacer que la búsqueda también mire
los activos `no_devuelto`.
