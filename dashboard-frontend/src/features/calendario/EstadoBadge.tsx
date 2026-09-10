import { Badge } from "@/components/ui/badge"
import type { EstadoReserva } from "@/lib/api"

import { estiloDe, etiquetaEstadoEvento, type EventoCalendario } from "./calendario-utils"

export function EstadoReservaBadge({
  estado,
  etiqueta,
}: {
  estado: EstadoReserva
  etiqueta?: string
}) {
  const estilo = estiloDe(estado)
  return (
    <Badge variant={estilo.badge} className={estilo.badgeClase}>
      {etiqueta ?? estado}
    </Badge>
  )
}

/**
 * Estado de una reserva completa: "parcial" si mezcla confirmados y
 * pendientes (ver EstadoReservaBadge, toma el color de "pendiente"). Para
 * una mezcla sin pendientes (ej. confirmada + cancelada), estiloDe(evento.estado)
 * daría el color de cualquiera de sus líneas -- típicamente el verde de
 * "confirmada" -- lo que se ve como si estuviera confirmada sin más. En vez
 * de eso, "estados mixtos" tiene su propio color: naranja sólido, para que
 * se note que hay que revisar la reserva línea por línea.
 */
export function EstadoEventoBadge({ evento }: { evento: EventoCalendario }) {
  if (evento.estadosMixtos && !evento.parcial) {
    // variant="outline" como base (no "warning"): esa variante trae su
    // propia dark:bg-warning/20 que le gana en oscuro a un bg-warning
    // sólido puesto por fuera, porque cn no considera que un dark:bg-* y
    // un bg-* sin prefijo sean el mismo conflicto (mismo motivo que
    // "pendiente"/"denegada" abajo, que arrancan de "outline").
    return (
      <Badge variant="outline" className="border-transparent bg-warning text-warning-foreground">
        estados mixtos
      </Badge>
    )
  }
  return <EstadoReservaBadge estado={evento.estado} etiqueta={etiquetaEstadoEvento(evento)} />
}
