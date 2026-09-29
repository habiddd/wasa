import { ChevronRight } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import type { EstadoReserva } from "@/lib/api"

import { estiloDe, etiquetaEstado } from "./calendario-utils"
import { EstadoReservaBadge } from "./EstadoBadge"
import { IconoTipo } from "./IconoTipo"

// En el orden de vida de un préstamo: primero lo que está en curso, al final
// lo que no llegó a pasar.
const ORDEN: EstadoReserva[] = [
  "pendiente",
  "confirmada",
  "entregada",
  "completada",
  "devuelta_tarde",
  "no_devuelta",
  "no_show",
  "cancelada",
  "denegada",
]

const CHIP = "inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-[11px] leading-tight"

// Los chips de la leyenda no son clicables: sin el hover de los reales.
const sinHover = (clases: string) => clases.replace(/hover:\S+/g, "")

/**
 * Qué significa cada chip del calendario, con los mismos estilos que usan las
 * vistas (ESTILO_ESTADO): relleno, punteado y tachado no se adivinan.
 * <details> nativo: se pliega sin estado ni JS propio.
 */
export function LeyendaEstados() {
  return (
    <details className="group text-xs">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1 rounded-md text-muted-foreground select-none hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
        <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" aria-hidden />
        Qué significa cada color
      </summary>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {ORDEN.map((estado) => (
          <span key={estado} className={cn(CHIP, sinHover(estiloDe(estado).chip))}>
            {etiquetaEstado(estado)}
          </span>
        ))}
        <span className="mx-1 h-4 w-px bg-border" aria-hidden />
        <span className={cn(CHIP, "border-transparent text-muted-foreground")}>
          <IconoTipo tipo="equipo" className="size-3" />
          Activo
        </span>
        <span className={cn(CHIP, "border-transparent text-muted-foreground")}>
          <IconoTipo tipo="espacio" className="size-3" />
          Lugar
        </span>
      </div>
      {/* Marcas que no son un estado propio sino un dato sobre la reserva:
          se ven en la tabla, el detalle y los tooltips, con estos mismos badges. */}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <span className="text-muted-foreground">En la tabla y el detalle:</span>
        <EstadoReservaBadge estado="pendiente" etiqueta="Parcial" />
        <span className="text-muted-foreground">parte confirmada, parte por confirmar ·</span>
        <Badge variant="outline" className="border-warning text-warning-text">
          Estados mixtos
        </Badge>
        <span className="text-muted-foreground">revisa línea por línea ·</span>
        <Badge variant="destructive">Retrasada</Badge>
        <span className="text-muted-foreground">entregada y ya vencida ·</span>
        <Badge variant="warning">Entrega tardía</Badge>
        <span className="text-muted-foreground">se recogió tarde</span>
      </div>
    </details>
  )
}
