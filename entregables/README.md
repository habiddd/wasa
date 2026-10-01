# Entregables del proyecto de prácticas

**Sistema de reservas asistido por inteligencia artificial para el préstamo de
equipos y espacios del Laboratorio de Producción Transmedia Álvaro Cepeda
Samudio (LACS) — Universidad del Norte**

Practicante: Habid Abdala Navarro · Ingeniería Electrónica · 200085000
Jefe inmediato: Carlos Barrios Márquez, Coordinador de Producción

**Repositorio del código fuente:** [github.com/habiddd/wasa](https://github.com/habiddd/wasa)

Esta carpeta reúne, por objetivo específico, los entregables comprometidos en
el anteproyecto. El código fuente no se duplica aquí: cada documento indica la
ruta del repositorio donde vive.

| OE | Objetivo específico | Entregables | Carpeta |
|---|---|---|---|
| 1 | Caracterizar el proceso actual y construir el modelo de datos relacional | (1) Repositorio con DDL y migraciones versionadas · (2) Base PostgreSQL desplegada con muestra representativa · (3) Modelo entidad-relación y caracterización del proceso | [OE1/](OE1/README.md) |
| 2 | Implementar el asistente conversacional sobre WhatsApp con clasificación de intenciones | (1) Servicio de mensajería integrado con WhatsApp · (2) Módulo de clasificación con lista blanca y prompt versionado · (3) Registro de pruebas de interpretación y correcciones | [OE2/](OE2/README.md) |
| 3 | Desarrollar la lógica de reservas con validación transaccional y confirmación explícita | (1) Módulo de reservas: consulta, reserva, cancelación, entrega, devolución y avisos · (2) Pruebas automatizadas de concurrencia y del ciclo de préstamo | [OE3/](OE3/README.md) |
| 4 | Implementar el back office con control de acceso, integridad y analítica | (1) Back office con controles de acceso, tasa de mensajes y retención de auditoría · (2) Módulo de analítica en tiempo real · (3) Aplicación integrada y desplegada, con guía de despliegue y operación | [OE4/](OE4/README.md) |

## Cómo verificar

El sistema completo se levanta con `docker compose up -d --build` (ver
[OE4/GUIA_DESPLIEGUE_Y_OPERACION.md](OE4/GUIA_DESPLIEGUE_Y_OPERACION.md)).
Las pruebas automatizadas se corren dentro del contenedor de la aplicación;
la lista y el comando de cada una están en
[OE3/pruebas/README.md](OE3/pruebas/README.md).
