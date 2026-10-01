---
target: Servicios → QR / WhatsApp
total_score: 25
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 2
target_identity: "file:C:\\Users\\WinterOS\\Desktop\\TRABAJO\\proyectos\\wasa\\dashboard-frontend\\src\\features\\servicios\\ServiciosPanel.tsx"
target_fingerprint: "sha256:c216a4337bcdcab277b7e9e37ee1cd275a07e28df3609da5d312b031603dc2b6"
target_path: "C:\\Users\\WinterOS\\Desktop\\TRABAJO\\proyectos\\wasa\\dashboard-frontend\\src\\features\\servicios\\ServiciosPanel.tsx"
timestamp: 2026-10-01T15-34-18Z
slug: src-features-servicios-serviciospanel-tsx-b9761f3d
---
⚠️ DEGRADED: single-context (la sesión no lanza subagentes sin pedido explícito del usuario)

Puntaje: 25/40 (aceptable)
1 Visibilidad del estado 2 · 2 Lenguaje del usuario 2 · 3 Control 3 · 4 Consistencia 3 · 5 Prevención de errores 3 · 6 Reconocer antes que recordar 3 · 7 Flexibilidad 2 · 8 Estética y minimalismo 2 · 9 Recuperación de errores 2 · 10 Ayuda 3

Problemas prioritarios
- [P1] El botón del canal activo parece deshabilitado (gris lavado): no se lee como "seleccionado".
- [P1] Con Meta activo, la página se llama "Vincular WhatsApp" y abajo dice "WhatsApp conectado (número 573245927008)", que es Baileys: el estado se contradice y el dato real queda al final de una página larga.
- [P2] "Número y cuenta" estira etiqueta y valor a 1100 px de distancia: difícil de recorrer; 11 filas de una sola columna.
- [P2] Móvil: las 3 cifras de Gastos dejan una celda gris vacía; las fechas del eje X se pisan ("2 de sept 10 de sept").
- [P2] Textos crudos: "calidad GREEN", un bloqueo en inglés, "gratis po…" cortado, "en_US".
- [P3] Las 5 plantillas de ejemplo de Meta (jaspers_market_*) ocupan más que la propia.

Detector: 0 hallazgos en ServiciosPanel.tsx. Inyección en navegador: live-server no arrancó (sin salida en 90 s).
