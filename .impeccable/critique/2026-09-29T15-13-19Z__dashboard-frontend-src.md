---
target: dashboard
total_score: 24
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 3
target_identity: "file:C:\\Users\\WinterOS\\Desktop\\TRABAJO\\proyectos\\wasa\\dashboard-frontend\\src"
timestamp: 2026-09-29T15-13-19Z
slug: dashboard-frontend-src
---
Method: A as an isolated sub-agent (partial browser pass: desktop, light mode only). B run afterwards: the CLI detector in the parent (clean, `[]`) and the browser overlay in an isolated sub-agent. B was delayed because the safety classifier was down; A finished before any detector output was seen.

# Critique: LACS Reservas Bot dashboard (dashboard-frontend/src)

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|---|---|---|
| 1 | Visibility of System Status | 3 | No auto-refresh and no count of today's pickups and returns. |
| 2 | Match System / Real World | 2 | Raw DB values show in the UI (`no_devuelta`, `devuelta_tarde`, `no_show`, `open`). English leaks in: "guardrails", "Intent", "Toggle Sidebar". |
| 3 | User Control and Freedom | 3 | Escape and Cancelar work. Both signatures may be lost if the acta dialog closes at step 4 (not tested). |
| 4 | Consistency and Standards | 2 | Badge text mixes lowercase raw values and capitalized labels. Estado and Métricas have no title. Nav text (18px) is bigger than page titles. |
| 5 | Error Prevention | 3 | Good guardrails. An activo's state changes with no confirmation. |
| 6 | Recognition Rather Than Recall | 2 | The calendar has no legend for its 9 states, and chip text is truncated. |
| 7 | Flexibility and Efficiency | 1 | No search, no shortcuts, no bulk actions, no deep links. |
| 8 | Aesthetic and Minimalist Design | 3 | Calm and dense. Métricas has columns that carry no information and a histogram with no axis. |
| 9 | Error Recovery | 2 | Toasts pass `err.message` through as-is. The WhatsApp-failed warning is exemplary. |
| 10 | Help and Documentation | 3 | Good inline notes. No glossary of states. |
| **Total** | | **24/40** | **Acceptable** |

## Design Specificity Verdict

**LLM assessment:** the surface is generic shadcn admin. The specificity lives in the logic:
- 9 states drawn with 3 hues plus dashed and strikethrough styles
- the 4-step signed acta
- the "Retrasada" and "Entrega tardía" add-on badges

The "Mostrador de Préstamos" North Star shows up in the palette but not in the information architecture. The app opens on infrastructure health, not on today's pickups and returns.

**Deterministic scan:** the CLI detector was clean (`[]`), but it barely covers Tailwind-in-TSX. The browser overlay found:
- `layout-transition` ×3: shadcn sidebar vendor code
- `nested-cards` ×21: false positives, because the page surface is treated as the outer card
- `clipped-overflow-container` ×1: the stock `overflow-hidden` on the Card
- `text-overflow` ×13: intentional truncation of the calendar chips, up to 99px hidden per name

There are no true positives.

## Priority Issues

**[P1] Raw DB values used as labels.** `etiquetaEstadoEvento` (`calendario-utils.ts:263`) returns `evento.estado` as-is. The same happens with the Métricas intents and the tooltips. **Fix:** a full map from `EstadoReserva` to a Spanish sentence-case label. **Command:** /impeccable clarify

**[P1] Amber text fails contrast.** `text-warning` (oklch 0.75) is about 2.2:1 on white. It is used 19 times across 11 files, including 11px chips. **Fix:** add a `--warning-text` token around oklch(0.52 0.13 65) for text; keep 0.75 for fills and borders. **Command:** /impeccable audit

**[P1] The information architecture serves the system, not the counter.** The default tab is `servicios-estado` (`App.tsx:114`). **Fix:** a "Hoy" panel with three blocks (to confirm, to hand over, to receive or overdue), plus a health dot in the sidebar footer. **Command:** /impeccable shape

**[P2] No legend and truncated chips on the calendar.** **Fix:** a collapsible legend strip, and the item name before the time. **Command:** /impeccable clarify

**[P2] Weak page hierarchy and action priority.** Titles are 14px labels next to 18px nav items. In `ReservaDetalle.tsx:938-953`, "Eliminar" sits between the equally weighted actions. **Fix:**
- only one primary button: the next step in the loan lifecycle
- "Eliminar" moved to the far left
- nav text reduced to text-sm

**Command:** /impeccable layout

## Persona Red Flags

**Alex:**
- no search in Reservas, Usuarios or Activos
- no shortcuts
- no bulk actions
- no URL state
- "Nueva reserva" has about 10 fields in one column, with the submit button below the fold

**Sam:**
- nested `<main>` (`SidebarInset` + `App.tsx:249`)
- the sidebar toggle is labelled "Toggle Sidebar" in English (`sidebar.tsx:274`)
- the Activos filter chips have no `aria-pressed`
- the activo state select has no label and is 20px tall
- badge text is the raw DB value

## Minor Observations

- A blue `--sidebar-primary` leaks into dark mode.
- There are three date formats.
- In Métricas, "Leído" and "Entregado" are the same green, and 000000000000 test rows are mixed into real data.
- The solid orange "estados mixtos" badge is the loudest element in the table.
- `text-[11px]` is off the type scale.

## Questions to Consider

- If a monitor could see only one screen all shift, would it be one of the 8 current panels?
- Is "estados mixtos" a state, or a sign that the reservation should be shown line by line?
- Should "no devuelto" be a side effect of *not* ticking a box, or an explicit act?
