---
name: LACS Reservas Bot — Dashboard
description: Panel de monitoreo y operación de préstamos de equipos y lugares del laboratorio.
colors:
  grafito: "oklch(0.205 0 0)"
  tinta: "oklch(0.145 0 0)"
  papel: "oklch(1 0 0)"
  papel-lateral: "oklch(0.985 0 0)"
  niebla: "oklch(0.97 0 0)"
  lapiz: "oklch(0.556 0 0)"
  trazo: "oklch(0.922 0 0)"
  anillo: "oklch(0.708 0 0)"
  verde-confirmado: "oklch(0.6 0.15 145)"
  ambar-pendiente: "oklch(0.75 0.15 75)"
  rojo-incidencia: "oklch(0.577 0.245 27.325)"
typography:
  display:
    fontFamily: "'Geist Variable', sans-serif"
    fontSize: "1.5rem"
    fontWeight: 700
    lineHeight: 1.333
  title:
    fontFamily: "'Geist Variable', sans-serif"
    fontSize: "1rem"
    fontWeight: 500
    lineHeight: 1.375
  nav:
    fontFamily: "'Geist Variable', sans-serif"
    fontSize: "1.125rem"
    fontWeight: 400
    lineHeight: 1.556
  body:
    fontFamily: "'Geist Variable', sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.429
  label:
    fontFamily: "'Geist Variable', sans-serif"
    fontSize: "0.875rem"
    fontWeight: 600
    lineHeight: 1.429
    letterSpacing: "0.025em"
  caption:
    fontFamily: "'Geist Variable', sans-serif"
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: 1.333
  micro:
    fontFamily: "'Geist Variable', sans-serif"
    fontSize: "11px"
    fontWeight: 400
    lineHeight: 1.25
    fontFeature: "'tnum'"
rounded:
  sm: "6px"
  md: "8px"
  lg: "10px"
  xl: "14px"
  pill: "26px"
spacing:
  hairline: "2px"
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
components:
  button-primary:
    backgroundColor: "{colors.grafito}"
    textColor: "{colors.papel}"
    typography: "{typography.body}"
    rounded: "{rounded.lg}"
    padding: "0 10px"
    height: "32px"
  button-outline:
    backgroundColor: "{colors.papel}"
    textColor: "{colors.tinta}"
    rounded: "{rounded.lg}"
    padding: "0 10px"
    height: "32px"
  button-outline-hover:
    backgroundColor: "{colors.niebla}"
  button-ghost-hover:
    backgroundColor: "{colors.niebla}"
    textColor: "{colors.tinta}"
  button-destructive:
    textColor: "{colors.rojo-incidencia}"
    rounded: "{rounded.lg}"
    height: "32px"
  input:
    textColor: "{colors.tinta}"
    typography: "{typography.body}"
    rounded: "{rounded.lg}"
    padding: "4px 10px"
    height: "32px"
  card:
    backgroundColor: "{colors.papel}"
    textColor: "{colors.tinta}"
    rounded: "{rounded.xl}"
    padding: "16px"
  badge:
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: "2px 8px"
    height: "20px"
  badge-confirmada:
    textColor: "{colors.verde-confirmado}"
  badge-pendiente:
    textColor: "{colors.ambar-pendiente}"
  badge-incidencia:
    textColor: "{colors.rojo-incidencia}"
  event-chip:
    typography: "{typography.micro}"
    rounded: "{rounded.sm}"
    padding: "2px 4px"
  section-heading:
    textColor: "{colors.lapiz}"
    typography: "{typography.label}"
---

# Design System: LACS Reservas Bot — Dashboard

## Overview

**Creative North Star: "El Mostrador de Préstamos"**

El dashboard es el mostrador de un laboratorio que presta equipos y lugares: una superficie gris, sobria y utilitaria, donde la gente trabaja todos los días. El papel, el grafito y el trazo fino se encargan de la estructura. El color no decora: solo dice **en qué estado está cada préstamo**. Si algo en pantalla es verde, ámbar o rojo, es porque una reserva está confirmada, pendiente o en falta.

La densidad es media: controles bajos (32px), texto base de 14px y tablas que muestran más columnas a medida que crece el contenedor. La interfaz se aparta para dejar ver los datos. Las superficies son planas y se separan con bordes o anillos al 10% de la tinta. La sombra solo aparece en lo que flota: diálogos, menús y tooltips.

El sistema nace del preset `radix-nova` de shadcn con base neutral y lo respeta. La identidad no está en una paleta de marca sino en el **vocabulario de estados**: relleno sólido, borde punteado y texto tachado distinguen nueve estados de reserva con solo tres colores.

**Key Characteristics:**
- Escala de grises acromática (croma 0) para todo lo estructural.
- Tres colores semánticos, y solo tres: verde confirmado, ámbar pendiente, rojo incidencia.
- Tintes al 10% en lugar de rellenos sólidos para estados y acciones secundarias.
- Plano con bordes; la sombra se reserva para capas flotantes.
- Una sola familia tipográfica (Geist Variable) con pesos 400/500/600/700.
- Modo claro y oscuro con paridad completa, alternados desde la barra lateral.

## Colors

Paleta acromática de papel y grafito, con tres acentos semánticos que se usan solo para comunicar estado.

### Primary
- **Grafito** (`grafito`): botón primario, badge y chip de estado `entregada` (el equipo está afuera, en manos del estudiante) y barras del histograma de métricas al 60%. En modo oscuro se invierte a oklch(0.922 0 0).

### Tertiary (semánticos de estado)
- **Verde Confirmado** (`verde-confirmado`): reserva `confirmada`. Chip con tinte al 10% y borde al 40%, y barra sólida en la vista de tiempo. En oscuro sube a oklch(0.7 0.17 145).
- **Ámbar Pendiente** (`ambar-pendiente`): `pendiente` (borde punteado), `no_show` y `entrega tardía` (tinte), `estados mixtos` (sólido, con texto en tinta). En oscuro sube a oklch(0.8 0.16 75).
- **Rojo Incidencia** (`rojo-incidencia`): `cancelada` (tinte + tachado), `denegada` (solo borde + tachado), `no_devuelta` y `Retrasada`, además de acciones destructivas. En oscuro pasa a oklch(0.704 0.191 22.216).

### Neutral
- **Tinta** (`tinta`): texto principal y fondo de los tooltips del histograma (con el texto en papel).
- **Papel** (`papel`): fondo de la app, de las tarjetas y de los popovers.
- **Papel Lateral** (`papel-lateral`): fondo de la barra lateral, un punto por debajo del papel.
- **Niebla** (`niebla`): superficies `muted`/`secondary`/`accent`: hover de botones fantasma, cabecera de la grilla del calendario al 40% y chip de `completada`.
- **Lápiz** (`lapiz`): texto secundario, descripciones, encabezados de sección y barra de `completada`/`devuelta_tarde`.
- **Trazo** (`trazo`): bordes y contorno de inputs. En oscuro es blanco al 10% (bordes) y al 15% (inputs).
- **Anillo** (`anillo`): anillo de foco, siempre al 50% de opacidad.

### Named Rules
**La Regla del Semáforo.** El color cromático solo expresa el estado de un préstamo o el riesgo de una acción. Ningún encabezado, icono de navegación ni fondo decorativo lleva verde, ámbar o rojo.

**La Regla de los Tokens.** No se usan colores de la paleta de Tailwind (`bg-red-500`, `text-green-600`). Todo sale de un token semántico (`success`, `warning`, `destructive`, `muted`…) y así el modo oscuro funciona gratis. Hoy el código no tiene ni una excepción.

**La Regla del Diez por Ciento.** Los estados se pintan como tinte: fondo al 10%, borde al 40% y texto al 100% del color. El relleno sólido se reserva para las barras de la vista de tiempo y para `estados mixtos`, que tiene que gritar.

## Typography

**Display Font:** Geist Variable (con sans-serif)
**Body Font:** Geist Variable (con sans-serif)

**Character:** Una sola sans geométrica y neutra, sin pareja tipográfica. La jerarquía se construye con peso, tamaño y el color lápiz, nunca con una segunda familia.

### Hierarchy
- **Display** (700, 1.5rem, 1.333): cifras KPI de Métricas ("reservas 30d", "bloqueadas 30d"). Es lo único grande de la app.
- **Nav** (400, 1.125rem): ítems de primer nivel de la barra lateral. Son más grandes que el cuerpo para que la navegación sea fácil de tocar y escanear.
- **Title** (500, 1rem, 1.375): títulos de tarjeta y de diálogo.
- **Label** (600, 0.875rem, tracking 0.025em, MAYÚSCULAS, color lápiz): el encabezado `h2` de cada panel ("CALENDARIO DE RESERVAS"). Es el mismo patrón en los ocho paneles.
- **Body** (400, 0.875rem, 1.429): tablas, formularios, cuerpo de tarjetas y diálogos. Los inputs suben a 16px en móvil para que iOS no haga zoom.
- **Caption** (400, 0.75rem): metadatos bajo una fila, badges y cabeceras de la grilla del calendario. Es el tamaño más usado de la app.
- **Micro** (400, 11px, cifras tabulares): chips de evento dentro de las celdas del mes.

### Named Rules
**La Regla del Encabezado Susurrado.** Los paneles no tienen un título grande. Se anuncian con una etiqueta en mayúsculas y color lápiz y dejan que los datos ocupen la vista.

**La Regla de las Cifras Alineadas.** Horas, contadores y columnas numéricas llevan `tabular-nums`.

## Layout

La app es una barra lateral más un área de contenido. La barra lateral se retrae a iconos en escritorio (≥768px) y en móvil pasa a ser un panel flotante, que se abre con un botón fijo arriba a la izquierda. Los grupos del menú (Servicios, Usuarios) son desplegables. Con la barra retraída, el clic en un grupo lleva directo a su pestaña activa.

El contenido se centra con 24px de margen y un ancho máximo de 72rem. Desde 1536px el máximo sube a 80rem y desde 1800px no hay límite. Las tablas responden a **container queries**, no al viewport: muestran más columnas en `@3xl`/`@4xl` y en `@6xl` suben a texto base con más padding vertical.

El espaciado es la escala de Tailwind de 4px. Los valores habituales son 8px entre controles, 12px entre bloques de un panel, 16px de padding de tarjeta (12px en tarjetas `sm`) y 24px alrededor del área principal. La cabecera de cada panel es una fila flexible que se envuelve, con el título a la izquierda y los filtros y acciones a la derecha.

## Elevation & Depth

Es un sistema plano con bordes. Las superficies en reposo no tienen sombra: las tarjetas se delimitan con un anillo de 1px de la tinta al 10%, y tablas, listas y la grilla del calendario con un borde `trazo`. La profundidad solo aparece cuando algo flota por encima del flujo.

### Shadow Vocabulary
- **Menú flotante** (`box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)`, `shadow-md`): desplegables y selects, siempre con anillo de tinta al 10%.
- **Capa modal** (`box-shadow: 0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1)`, `shadow-lg`): diálogos y submenús.

### Named Rules
**La Regla del Mostrador Plano.** Lo que vive en la página no proyecta sombra. Si algo necesita separarse, lleva un borde o un anillo al 10%.

## Shapes

Las esquinas son suaves y se derivan de un único radio base de 10px (`--radius: 0.625rem`). Los chips del calendario usan 6px, los controles (botones, inputs, selects y menús) 10px, y las tarjetas y diálogos 14px. Los badges son píldoras completas (26px) y los avatares o indicadores de punto son círculos. Las barras del histograma solo redondean la parte superior (2px). Los bordes son siempre de 1px, sólidos por defecto y **punteados solo para `pendiente`**. El punteado significa "todavía no está confirmado".

## Components

### Buttons
Son compactos y discretos: no compiten con los datos.
- **Shape:** esquinas suaves (10px), 32px de alto y 10px de padding horizontal. Tallas `xs` de 24px, `sm` de 28px y `lg` de 36px. Los iconos miden 16px (14px o 12px en tallas chicas).
- **Primary:** fondo grafito y texto papel. En hover baja al 80% de opacidad.
- **Outline:** fondo papel y borde trazo, con niebla en hover. En oscuro usa el input al 30%.
- **Ghost:** transparente, con niebla en hover. Es el botón por defecto dentro de tablas y cabeceras.
- **Destructive:** tinte rojo al 10% con texto rojo (20% en hover). Nunca rojo sólido.
- **Hover / Focus:** transición de color. Al enfocar, el borde pasa a anillo y aparece un halo de 3px del anillo al 50%. Al presionar, el botón baja 1px.

### Chips (badges de estado)
- **Style:** píldora de 20px con texto caption en peso 500. Las variantes semánticas son tinte al 10% con texto al 100%.
- **Estados:** `success` (confirmada), `warning` (no_show, entrega tardía), `destructive` (cancelada, no_devuelta, Retrasada), `outline` punteado ámbar (pendiente), `outline` rojo (denegada), `secondary` (completada, devuelta_tarde) y `default` grafito (entregada).
- **Composición:** `Retrasada` y `Entrega tardía` se agregan **junto al** estado, no lo reemplazan. Son datos adicionales sobre el préstamo.

### Cards / Containers
- **Corner Style:** 14px.
- **Background:** papel (en oscuro, oklch(0.205 0 0)).
- **Shadow Strategy:** ninguna (ver Elevation & Depth).
- **Border:** anillo de 1px de tinta al 10%.
- **Internal Padding:** 16px, o 12px en la talla `sm`. El pie de tarjeta lleva niebla al 50% y borde superior.

### Inputs / Fields
- **Style:** 32px de alto, borde trazo, fondo transparente (input al 30% en oscuro) y esquinas de 10px.
- **Focus:** el borde pasa a anillo y aparece un halo de 3px al 50%.
- **Error / Disabled:** con `aria-invalid`, borde rojo y halo rojo al 20%. Deshabilitado queda al 50% de opacidad sobre fondo input.

### Navigation
- **Style:** la barra lateral usa el fondo papel lateral. La cabecera lleva el nombre "LACS Reservas Bot" (600, 14px) y el subtítulo "Panel de monitoreo" (caption, lápiz). Los ítems miden 36px de alto, con texto nav (18px) y un icono Lucide.
- **Estados:** el ítem activo lleva fondo `sidebar-accent`. Los grupos muestran un chevron que gira 90° al abrirse. Retraída, cada ítem muestra un tooltip con su nombre.
- **Pie:** el conmutador de tema claro/oscuro.

### Chip de evento (signature)
Es la pieza que define el sistema. Cada reserva en la vista de mes es un botón de ancho completo con esquinas de 6px, texto micro (11px), un icono de tipo (caja para activos, pin para lugares), la hora con cifras tabulares al 80% de opacidad y el nombre truncado. Su piel sale de `ESTILO_ESTADO`:
- **Sólido tintado:** confirmada (verde), no_show (ámbar), entregada (grafito), cancelada y no_devuelta (rojo).
- **Punteado:** pendiente (borde ámbar punteado y fondo al 5%).
- **Tachado:** cancelada y denegada. Denegada es solo borde, sin relleno.
- **Apagado:** completada (niebla y lápiz); devuelta_tarde es apagado con borde ámbar.

En la vista de tiempo, el mismo estado se traduce a una **barra**: sólida para los estados firmes y solo con borde para pendiente y denegada. Al pasar el cursor aparece un tooltip con el detalle, y el clic abre un diálogo centrado (nunca un panel lateral).

## Do's and Don'ts

### Do:
- **Do** tomar todo color de un token semántico (`success`, `warning`, `destructive`, `primary`, `muted`, `border`) para que el modo oscuro funcione sin tocar nada.
- **Do** pintar los estados como tinte: fondo al 10%, borde al 40% y texto al 100% del color semántico.
- **Do** agregar un estado nuevo como una entrada más en `ESTILO_ESTADO` (chip, barra y badge) y combinar relleno, punteado y tachado antes de sumar un color nuevo.
- **Do** abrir los paneles con el encabezado susurrado: `h2` de 14px, peso 600, en mayúsculas, con tracking amplio y color lápiz.
- **Do** mostrar el detalle de un elemento en un diálogo centrado con opción de editar.
- **Do** usar container queries (`@3xl`, `@4xl`, `@6xl`) para revelar columnas de tabla.

### Don't:
- **Don't** usar colores de la paleta de Tailwind (`bg-red-500`, `text-emerald-600`) ni hex sueltos en componentes.
- **Don't** sumar un cuarto color cromático. Un estado nuevo se distingue por forma (punteado, tachado, sólido), no por matiz.
- **Don't** poner sombra en tarjetas, tablas o superficies en reposo.
- **Don't** usar rojo sólido en botones destructivos; se usa el tinte al 10%.
- **Don't** mostrar detalles en un panel lateral (`Sheet`) cuando se puede usar un diálogo.
- **Don't** introducir una segunda familia tipográfica ni títulos de panel grandes.
