import { Badge } from "@/components/ui/badge"
import type { EstadoReserva } from "@/lib/api"

import {
  estiloDe,
  etiquetaEstado,
  etiquetaEstadoEvento,
  type EventoCalendario,
} from "./calendario-utils"

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
      {etiqueta ?? etiquetaEstado(estado)}
    </Badge>
  )
}

/**
 * Estado de una reserva completa: "parcial" si mezcla confirmados y
 * pendientes (ver EstadoReservaBadge, toma el color de "pendiente"). Para
 * una mezcla sin pendientes (ej. confirmada + cancelada), estiloDe(evento.estado)
 * daría el color de cualquiera de sus líneas -- típicamente el verde de
 * "confirmada" -- lo que se ve como si estuviera confirmada sin más. En vez
 * de eso, "estados mixtos" tiene su propio aspecto: contorno ámbar, para que
 * se note que hay que revisar la reserva línea por línea.
 *
 * "Retrasada" se pinta APARTE, no en vez de "entregada": el estado real
 * sigue siendo "entregada" (el equipo sigue prestado) y el retraso es un
 * dato adicional -- que además es derivado, no un estado persistido (ver
 * Sesion.retrasada) -- que le importa a quien mira el calendario.
 *
 * "Entrega tardía" se pinta con el mismo patrón: el estudiante recogió
 * después del margen de no-show pero dentro de su ventana (ver
 * Sesion.entrega_tardia), y esa marca importa aparte del estado "entregada".
 * A diferencia de "retrasada", esta SÍ es persistida -- sobrevive a la
 * devolución -- pero visualmente se trata igual: un dato adicional sobre el
 * estado, no un reemplazo. warning (no destructive): no es una incidencia
 * en curso como "retrasada", es historial de cómo se entregó.
 */
export function EstadoEventoBadge({ evento }: { evento: EventoCalendario }) {
  if (evento.estadosMixtos && !evento.parcial) {
    // Solo contorno ámbar (sólido, a diferencia del punteado de
    // "pendiente"): llama a revisar línea por línea sin ser lo más ruidoso
    // de la tabla, que es lo que pasaba con el relleno naranja.
    return (
      <Badge variant="outline" className="border-warning text-warning-text">
        Estados mixtos
      </Badge>
    )
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <EstadoReservaBadge estado={evento.estado} etiqueta={etiquetaEstadoEvento(evento)} />
      {evento.retrasada && <Badge variant="destructive">Retrasada</Badge>}
      {evento.entregaTardia && <Badge variant="warning">Entrega tardía</Badge>}
    </span>
  )
}
