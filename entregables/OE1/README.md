# OE1 — Caracterizar el proceso actual y construir el modelo de datos relacional

**Repositorio:** _______________________________________________

## Entregable 1: repositorio con los scripts DDL y las migraciones versionadas

| Archivo | Contenido |
|---|---|
| `postgres/init/01-init.sql` | Creación de la base y del usuario de la aplicación con privilegios restringidos. |
| `postgres/init/02-schema.sql` | Esquema completo, idempotente: tablas, restricciones, índices, triggers y vistas. |
| `flask-app/app/models.py` | Mapeo de las tablas en SQLAlchemy. |

El historial de cambios del esquema está en git; ver la sección "Versionado
del esquema" de [MODELO_DATOS.md](MODELO_DATOS.md).

## Entregable 2: base de datos PostgreSQL desplegada con una muestra representativa

La base corre en el contenedor `lacs_postgres` (`docker-compose.yml`) con
volumen persistente. El catálogo de prueba se carga con:

```bash
docker exec lacs_flask_app flask --app wsgi.py seed-recursos
```

(`flask-app/app/seed.py`, idempotente). La muestra cubre los distintos
comportamientos y restricciones de préstamo:

| Tipo de activo | Ejemplos | Comportamiento que prueba |
|---|---|---|
| Equipo con autorización | Cámaras Sony A7III, Canon EOS R y R6, kit de luces LED, dron DJI Mavic | Nace `pendiente`; lo decide la LACS. |
| Equipo de acceso libre | Trípode Manfrotto, micrófono Rode NTG3 | Nace `confirmada` sin intermediación. |
| Accesorios (hijos) | Lentes Sony y Canon RF | Árbol padre/hijo: se ofrecen con su cámara. |
| Espacios | Estudio de grabación A, Sala de edición 1 | Reserva de espacios; no aplica salida de Barranquilla. |
| Activo no devuelto | Lente Canon RF 100-500mm | Queda fuera del catálogo hasta que la LACS lo resuelva. |

Estado de la base desplegada al 1 de octubre de 2026:

| Tabla | Filas |
|---|---|
| `recursos` | 16 (14 equipos, 2 espacios) |
| `reserva_grupos` | 27 |
| `reservas` | 44, en los nueve estados del ciclo (confirmada, pendiente, entregada, completada, devuelta tarde, no devuelta, no show, cancelada, denegada) |
| `reserva_estado_historial` | 124 transiciones |
| `reserva_actas` | 6 actas |
| `reserva_avisos` | 19 avisos |
| `auditoria_bot` | 373 interacciones |

Los datos son de prueba (sin información personal de la comunidad
universitaria) y alcanzan para ejercitar todas las reglas de préstamo, el
ciclo de estados y los informes del módulo de analítica.

## Entregable 3: modelo entidad-relación y caracterización del proceso actual

- [CARACTERIZACION_PROCESO.md](CARACTERIZACION_PROCESO.md): las dos vías de
  solicitud (Delta y correo), la confirmación del funcionario en el sistema
  interno del LACS, la entrega presencial y su correspondencia con el sistema nuevo.
- [MODELO_DATOS.md](MODELO_DATOS.md): diagrama entidad-relación, tablas,
  reglas de integridad y versionado.
