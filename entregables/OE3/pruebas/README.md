# Pruebas automatizadas

Entregable 2 del Objetivo Específico 3: pruebas de concurrencia y del ciclo
de préstamo sobre casos representativos, versionadas en el repositorio.

Cada prueba es un script que se ejecuta **dentro** del contenedor de la
aplicación, con la base real, y termina con una línea `OK: ...` o con un
`AssertionError` que dice qué falló:

```bash
docker exec -i lacs_flask_app python - < entregables/OE3/pruebas/verificar_concurrencia.py
```

Para correr todas las que no mandan mensajes ni dejan datos:

```bash
for t in concurrencia retencion equipos_acta items_acta nota_acta celular_registro \
         bot_no_disponible robustez_bot meta_whatsapp excel_raw; do
  docker exec -i lacs_flask_app python - < entregables/OE3/pruebas/verificar_$t.py || echo "FALLÓ: $t"
done
```

## Lista

Columna "Efecto": **ninguno** = revierte todo o solo lee; **borra** = crea
datos y los borra al final; **deja evidencia** = deja las filas a propósito
para revisarlas en el dashboard; **WhatsApp** = manda un mensaje real al
usuario de pruebas.

| Prueba | Qué verifica | Efecto | Resultado 01/10/2026 |
|---|---|---|---|
| `verificar_concurrencia.py` | 8 hilos ven el mismo equipo libre y reservan la misma franja a la vez: solo una reserva queda, las otras 7 reciben "choque". Solapamiento parcial rechazado, franja contigua aceptada, reserva cancelada no ocupa. | borra | OK (3 de 3 corridas) |
| `verificar_retencion.py` | Política de retención: una fila de 400 días se anonimiza y conserva intención y latencia; una de hoy queda intacta; la purga es idempotente y se puede desactivar. | borra | OK |
| `verificar_equipos_acta.py` | Entrega parcial: lo no entregado se cancela; no se devuelve lo no entregado; lo no devuelto queda `no_devuelta`. | ninguno | OK |
| `verificar_items_acta.py` | Marcas por activo en el acta PDF (entrega y devolución) y que el acta quepa en una página. | ninguno | OK |
| `verificar_nota_acta.py` | Nota del administrador en el acta y rechazo de una nota demasiado larga. | ninguno | OK |
| `verificar_celular_registro.py` | Registro por WhatsApp: el celular se pide solo si WhatsApp no lo dio; resumen y confirmación. | ninguno | OK |
| `verificar_bot_no_disponible.py` | El bot dice "no está disponible" por un equipo no devuelto, no lo cambia por otro modelo y no deja reservarlo. | ninguno | OK |
| `verificar_robustez_bot.py` | Hallazgos F1–F18 de las pruebas en vivo y F19 de la ronda con usuarios piloto (ver `entregables/OE2/REGISTRO_PRUEBAS_INTERPRETACION.md`). | ninguno | OK |
| `verificar_meta_whatsapp.py` | Canal de Meta: firma del webhook, deduplicación, ventana de 24 h, selector de proveedor y métricas. | ninguno | OK |
| `verificar_excel_raw.py` | Exportación de la base a Excel, partiendo las tablas que pasan el límite de filas. | ninguno | OK |
| `verificar_historial.py` | Historial de estados, vista de puntualidad y bandera de entrega tardía en todo el ciclo. | deja evidencia | — |
| `verificar_no_devuelto.py` | Ciclo completo contra los endpoints reales: entrega de dos equipos y devolución de uno; el otro queda "no devuelto". | borra (el historial queda) | — |
| `verificar_no_devuelto_evidencia.py` | El mismo caso, dejando reservas, actas e historial para revisarlos en el dashboard. | deja evidencia | — |
| `verificar_aviso_devolucion.py` | Aviso automático antes del vencimiento del préstamo: se manda una vez y no se duplica. | WhatsApp | — |

Las cuatro últimas no se corrieron el 1 de octubre para no dejar datos ni
mandar mensajes en la base desplegada; sus resultados están en
`docs/PRUEBAS_HISTORIAL_2026-09-28.md` y `docs/PRUEBAS_NO_DEVUELTO_2026-09-28.md`.

## Verificaciones de la aplicación (`flask-app/tests/`)

Desde el 16 de octubre hay tres verificaciones más, junto al código de la
aplicación. Tampoco necesitan pytest y se corren en el mismo contenedor:

```bash
docker exec -w /app -e PYTHONPATH=/app lacs_flask_app python tests/<archivo>.py
```

| Verificación | Qué verifica | Efecto | Resultado 16/10/2026 |
|---|---|---|---|
| `test_menu_service.py` | El menú de opciones solo entiende un número, el nombre de una opción o una palabra de salida; cualquier otra frase vuelve al clasificador. | ninguno | OK |
| `test_config_llm.py` | Configuración del modelo desde el back office: se guarda, se borra y no devuelve la clave completa. | usa una clave de Redis de prueba y la borra | OK |
| `test_horario_prestamo.py` | Horario de atención (inicio y fin, extremos incluidos) y préstamos de más de siete días como pendientes. | ninguno | OK |

## Hallazgo de la prueba de concurrencia

La primera corrida de `verificar_concurrencia.py` encontró un defecto real.
Con varias inserciones simultáneas, Postgres no siempre responde con una
violación de la restricción `EXCLUDE`: a veces detecta un interbloqueo
(*deadlock*, código `40P01`) entre las verificaciones de dos transacciones y
aborta una. `crear_reservas_grupo` solo atrapaba `IntegrityError`, así que el
usuario que perdía la carrera recibía un error interno en vez de "no está
disponible". La integridad nunca se rompió (quedaba una sola reserva). Se
corrigió en `flask-app/app/recursos_service.py`: ese interbloqueo solo
ocurre cuando dos inserciones chocan, así que se trata como un choque.
