# OE3 — Lógica de negocio de reservas con validación transaccional y confirmación explícita

**Repositorio:** [github.com/habiddd/wasa](https://github.com/habiddd/wasa)

## Entregable 1: módulo de reservas en operación

| Operación | Dónde | Cómo |
|---|---|---|
| Consulta de disponibilidad | `recursos_service.evaluar_consulta_disponibilidad` | Consulta a la base por recurso y franja; nunca la inventa el modelo de lenguaje. Ofrece reservar al terminar. |
| Reserva | `recursos_service.gestionar_reserva` (wizard) y `crear_reservas_grupo` | Wizard paso a paso: activo, accesorios, fecha, hora, tipo de uso, modalidad, salida de Barranquilla, nota y **resumen con confirmación explícita** antes de escribir. Todas las líneas se escriben en una sola transacción. |
| Validación de no solapamiento | Restricción `reservas_no_solapamiento` (`postgres/init/02-schema.sql`) | `EXCLUDE USING gist` sobre recurso y rango horario. El chequeo previo de la aplicación solo da un mensaje más claro; la garantía es la base de datos. |
| Recursos con autorización | `recursos.requiere_confirmacion` | La reserva nace `pendiente`. |
| Decisión de la coordinación | Dashboard: Hoy → *Por confirmar*, Reservas y Calendario | La LACS confirma o deniega; la decisión queda en el historial y el bot le avisa al estudiante por WhatsApp (`confirmacion_lacs` / `denegacion_lacs`). |
| Cancelación | `recursos_service.gestionar_cancelacion` | Por número, por descripción ("la sala del martes") u ordinal; siempre pide confirmación. |
| Entrega y devolución | `actas_service.py`, `acta_pdf.py`, dashboard → acta | Acta firmada en pantalla o PDF subido; es la única forma de pasar a `entregada`, `completada`, `devuelta_tarde` o `no_devuelta`. |
| Avisos automáticos | `avisos_reservas.py` + `ticker.py` | Aviso previo a la recogida, aviso a la hora, no-show automático y aviso de devolución antes del vencimiento. `UNIQUE (grupo_id, tipo)` evita duplicados. |

### Notificación a la coordinación

Las solicitudes de recursos con autorización llegan a la bandeja
**Hoy → Por confirmar** del back office, que se refresca sola cada 30
segundos, y aparecen marcadas en el Calendario y en Reservas. Desde ahí la
coordinación las confirma o las deniega, y el estudiante recibe la respuesta
por WhatsApp.

Detalle del diseño: `docs/ARQUITECTURA.md`, secciones "Reserva paso a paso",
"Confirmación de la LACS: reservas parciales y pendientes", "Ciclo de estados
de una reserva" y "No-show automático y avisos de recogida y devolución".

## Entregable 2: pruebas automatizadas de concurrencia y del ciclo de préstamo

[pruebas/README.md](pruebas/README.md): 14 pruebas, incluida la de
concurrencia (`verificar_concurrencia.py`), con su comando y su resultado.
