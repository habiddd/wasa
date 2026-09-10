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

/** Estado de una reserva completa: "parcial" si mezcla confirmados y pendientes. */
export function EstadoEventoBadge({ evento }: { evento: EventoCalendario }) {
  return <EstadoReservaBadge estado={evento.estado} etiqueta={etiquetaEstadoEvento(evento)} />
}
