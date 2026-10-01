# OE4 — Back office con control de acceso, integridad y analítica en tiempo real

**Repositorio:** _______________________________________________

## Entregable 1: back office con controles de acceso, control de tasa de mensajes y política de retención de auditoría

| Control | Implementación |
|---|---|
| Autenticación del back office | Basic Auth (`DASHBOARD_USER` / `DASHBOARD_PASSWORD`) en Nginx y, de forma independiente, en cada llamada a `/dashboard/api/*` (`routes/dashboard.py`, `_requiere_auth`). Sin contraseña configurada responde 503, nunca queda abierto. |
| Autenticación de los puntos de acceso de analítica | `/api/analytics/*` usa la misma verificación (`routes/analytics.py`). Sin credenciales responde 401. |
| Exposición restringida de servicios internos | Todos los puertos se publican solo en `127.0.0.1` (`docker-compose.yml`); Redis no se publica. La base se usa con un usuario sin privilegios de superusuario. |
| Control de tasa de mensajes | `guardrails.excede_limite_mensajes`: `MAX_MESSAGES_PER_MINUTE` por número (por defecto 8); lo que excede queda en la auditoría como `rate_limit_excedido`. |
| Filtrado de inyección de instrucciones | `guardrails.mensaje_es_sospechoso`: compara contra frases conocidas sobre una forma normalizada del texto (sin tildes, sin separadores) y con tope de longitud. |
| Lista blanca de intenciones | `guardrails.intent_permitido` (ver OE2). |
| Registro de auditoría | `auditoria_bot`: cada interacción con su intención, respuesta, bloqueo, estado de entrega y latencia. |
| Política de retención | [POLITICA_RETENCION_AUDITORIA.md](POLITICA_RETENCION_AUDITORIA.md): anonimización automática a los 180 días. |

Checklist completa y riesgos aceptados: `docs/SEGURIDAD.md`.

## Entregable 2: módulo de analítica en operación, con consultas y tablero en tiempo real

Back office → **Métricas**, por periodo (30 días, 90 días, todo o rango
personalizado). Se refresca cada 60 s. Consultas en
`flask-app/app/routes/dashboard.py` (`api_metricas`, `api_metricas_activo`,
`api_metricas_uso`).

| Informe comprometido en el anteproyecto | Dónde está |
|---|---|
| Número de turnos por día | General → Cuándo se reserva → Por día |
| Número de turnos por semana y por mes | General → Cuándo se reserva → Por semana / Por mes |
| Demanda por equipo y espacio | General → Top 10 activos; Por activo; Por uso (con y sin NRC) |
| Franjas de mayor concurrencia | General → Por día de la semana y Por hora de inicio |
| Solicitudes resueltas sin intervención humana | **Rendimiento bot** → % de solicitudes del bot que no pasaron por la LACS |
| Tiempos de respuesta | **Rendimiento bot** → promedio, mediana y p95, y promedio por tipo de mensaje |
| Intenciones más frecuentes | **Rendimiento bot** → Tipos de mensaje más frecuentes |

Además: reservas por estado, puntualidad de entrega y devolución, top 10
usuarios, inventario, seguro, hábitos (duración, anticipación, canal),
mensajes bloqueados por guardrails, avisos automáticos enviados y fallidos, y
la historia día por día de cada activo. **Descargar Excel** exporta las
métricas del periodo (siete hojas con gráficos) o la base completa.

Cómo se calculan los dos indicadores del bot:

- **Sin intervención humana**: entre las solicitudes (grupos de reserva)
  creadas por WhatsApp en el periodo, las que ninguna de sus líneas pasó por
  `pendiente` según el historial de estados, es decir, las que la LACS no
  tuvo que decidir.
- **Tiempo de respuesta**: milisegundos entre la llegada del mensaje al
  webhook y el envío de la respuesta (`auditoria_bot.latencia_ms`). Se mide
  desde el 1 de octubre de 2026; los mensajes anteriores no tienen el dato.

## Entregable 3: aplicación integrada y desplegada en un entorno de pruebas, con su guía

- Despliegue: los cinco servicios orquestados con Docker Compose
  (`docker-compose.yml`), corriendo en el entorno de pruebas.
- [GUIA_DESPLIEGUE_Y_OPERACION.md](GUIA_DESPLIEGUE_Y_OPERACION.md): despliegue,
  vinculación de WhatsApp, operación diaria, respaldo, actualización y
  solución de problemas.
- Documentación técnica complementaria: `README.md` (raíz),
  `docs/ARQUITECTURA.md`, `docs/SEGURIDAD.md`, `docs/GUIA_META_WHATSAPP.md`.
