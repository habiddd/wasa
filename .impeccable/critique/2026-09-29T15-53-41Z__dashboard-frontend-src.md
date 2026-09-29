---
target: dashboard
total_score: 23
max_score: 40
na_heuristics: 
p0_count: 1
p1_count: 2
target_identity: "file:C:\\Users\\WinterOS\\Desktop\\TRABAJO\\proyectos\\wasa\\dashboard-frontend\\src"
timestamp: 2026-09-29T15-53-41Z
slug: dashboard-frontend-src
---
Method: dual-agent (A: af0d1854c142f49a5 · B: a1aa7321899f39f1e). A covered desktop fully but mobile only partially, because the safety classifier failed.

# Critique: LACS Reservas Bot dashboard (second run)

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|---|---|---|
| 1 | Visibility of System Status | 3 | Hoy has no "actualizado hace…", and Activos does not auto-refresh. |
| 2 | Match System / Real World | 3 | Counter Spanish is clean now. Internal terms leak: "+23", "2 hijos", Postgres/Redis. |
| 3 | User Control and Freedom | 2 | Escape or a click outside in the acta wipes the signatures (FirmarActaDialog.tsx:244). No URLs and no Back. |
| 4 | Consistency and Standards | 2 | Atrasados / Retrasadas / Retrasada name one concept. Calendario and Reservas have different status filters. |
| 5 | Error Prevention | 3 | Good confirmations. The signature-loss hole takes points off. |
| 6 | Recognition Rather Than Recall | 2 | The legend lacks the derived states (Estados mixtos, Parcial, Retrasada, Entrega tardía). Chips are truncated. |
| 7 | Flexibility and Efficiency | 1 | No search or shortcuts. Name and cédula are retyped on every acta. |
| 8 | Aesthetic and Minimalist Design | 3 | Calm overall. Activos stacks too many pills per row, and the "Permitido" column adds nothing. |
| 9 | Error Recovery | 3 | Errors come in Spanish (from the backend plus the new fallback). Hoy has Reintentar. |
| 10 | Help and Documentation | 1 | Only the legend and one footnote. |
| **Total** | | **23/40** | **Acceptable** |

The 24→23 change does not mean the product got worse. This reviewer was stricter and found new problems (keyboard access, signatures). All the P1/P2 issues from the first critique are closed.

## Design Specificity Verdict

Hoy is the first surface that is specific to this product. Calendario, Reservas, Métricas and Activos still read as generic CRUD. The CLI scan was clean. The overlay flagged about 45 items, almost all false positives. There was one true positive: muted-foreground on muted text at 4.3:1 on the 11px Completada and Devuelta tarde chips.

## Priority Issues

**[P0] Sesiones, Estado and QR / WhatsApp are unreachable by keyboard.** SidebarMenuSubButton renders an `<a>` with no href (sidebar.tsx:660). **Fix:** use asChild with a `<button>` in App.tsx:226. **Command:** /impeccable audit

**[P1] The acta wipes signatures on Escape or an outside click.** **Fix:**
- from step 2, block closing and ask "¿Descartar las firmas?"
- remember the monitor's name and cédula

**Command:** /impeccable harden

**[P1] Hoy hides no_devuelta equipment.** bloqueDe (HoyPanel.tsx:40) only handles retrasada. **Fix:** route no_devuelta to Atrasados, with no 30-day ceiling. **Command:** /impeccable harden

**[P2] Status vocabulary drifts and the legend is incomplete.** **Fix:** one term per concept, a shared ESTADOS_FILTRO constant, and the derived states in the legend. **Command:** /impeccable clarify

**[P2] Low contrast on the focus ring and muted text.** --ring at about 2.5:1, and chip text at 4.3:1 at 11px. **Command:** /impeccable audit

## Persona Red Flags

**Alex:**
- no search
- no URLs
- acta details retyped every time
- Métricas "64" vs Reservas "21" with no explanation

**Sam:**
- sub-menu items unreachable
- rows have no role or accessible name
- weak focus ring
- theme menu shows no checked state

**Counter monitor:**
- no_devuelta missing from Hoy
- signatures can be lost
- no "actualizado hace…"
- every row has the same primary button
- Métricas sits second in the nav

## Minor Observations

- Scrollbar causes an 8px layout shift.
- "+23" is cryptic.
- Empty Hoy blocks have the same weight as full ones.
- The month view shows 4 empty weeks before the current one.
- "Devuelta tarde" and "Entrega tardía" look the same.
- Activos shows "Disponible" for equipment that is lent out.

## Questions to Consider

- Could Reservas be "Hoy with search and a date picker"?
- Could the acta fit on one screen?
- Should the asset be the primary object and the reservation secondary?
