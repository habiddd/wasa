# Política de retención del registro de auditoría

Actividad 3 del Objetivo Específico 4.

## Qué se guarda

La tabla `auditoria_bot` registra cada interacción del asistente:

| Campo | Contenido | ¿Dato personal? |
|---|---|---|
| `telefono_wa` | Número de WhatsApp del usuario | **Sí** |
| `mensaje_entrante` | Texto que escribió el usuario | **Sí**, puede contener nombres, cédulas o cualquier cosa que escriba |
| `respuesta_enviada` | Texto que respondió el bot | **Sí**, incluye datos de la reserva del usuario |
| `intent_detectado` | Intención clasificada | No |
| `permitido`, `motivo_bloqueo` | Si un guardrail bloqueó el mensaje y por qué | No |
| `estado_envio`, `mensaje_id` | Estado de entrega del mensaje | No |
| `latencia_ms` | Tiempo de respuesta | No |
| `creado_en` | Fecha y hora | No |

## Para qué se guarda

1. **Depurar el asistente** revisando conversaciones reales. Necesita el texto completo.
2. **Atender reclamos** de un usuario sobre lo que el bot le dijo. Necesita el
   texto y el teléfono, durante un plazo razonable después de la conversación.
3. **Métricas** del módulo de analítica (volumen, intenciones, bloqueos,
   tiempos de respuesta). Solo necesita los campos que no son personales.

## Regla

**Pasados 180 días desde la interacción, se anonimiza la fila:** se borran
`mensaje_entrante` y `respuesta_enviada`, y `telefono_wa` se reemplaza por
`anonimizado`. Se conservan intención, permitido, motivo de bloqueo, estado de
envío, latencia y fecha.

- 180 días cubren un semestre académico completo: alcanza para revisar el
  comportamiento del bot en ese periodo y para resolver cualquier reclamo
  sobre una reserva del semestre.
- La fila no se borra: las métricas históricas (mensajes por periodo,
  intenciones más frecuentes, bloqueos, tiempos de respuesta) siguen siendo
  exactas después de anonimizar.
- La anonimización es irreversible.

Esta política aplica solo a `auditoria_bot`. Las reservas, sus actas firmadas
y su historial de estados son el registro operativo y probatorio del préstamo
y no se anonimizan con ella.

## Cómo se aplica

- Implementación: `guardrails.anonimizar_auditoria_vieja()` en
  `flask-app/app/guardrails.py`, una sola sentencia `UPDATE` idempotente.
- Ejecución: el hilo de fondo `ticker.py` la corre en cada barrido (cada
  60 s, con el mismo bloqueo de Redis que los avisos), sin intervención
  humana. Cuando anonimiza filas lo deja en el log del contenedor.
- Plazo: variable `AUDITORIA_RETENCION_DIAS` en `.env` (por defecto 180).
  `0` desactiva la anonimización.
- Verificación: `entregables/OE3/pruebas/verificar_retencion.py`.

## Efecto visible

En el back office, la conversación de una sesión (Usuarios → Sesiones) deja
de mostrar los mensajes de más de 180 días. Las métricas no cambian.
