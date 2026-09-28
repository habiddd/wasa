import type { ReactElement } from "react"

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import type { EstadoReserva } from "@/lib/api"
import { cn } from "@/lib/utils"

import { tooltipEvento, type EventoCalendario } from "./calendario-utils"

// Mismo color que el bloque del calendario (ESTILO_ESTADO.chip), pero sólido
// para que se lea sobre cualquier fondo. [&_svg] pinta también la flecha.
const COLOR_TOOLTIP: Record<EstadoReserva, string> = {
  confirmada: "bg-success text-success-foreground [&_svg]:bg-success [&_svg]:fill-success",
  pendiente: "bg-warning text-warning-foreground [&_svg]:bg-warning [&_svg]:fill-warning",
  no_show: "bg-warning text-warning-foreground [&_svg]:bg-warning [&_svg]:fill-warning",
  entregada: "bg-primary text-primary-foreground [&_svg]:bg-primary [&_svg]:fill-primary",
  cancelada: "bg-destructive text-white [&_svg]:bg-destructive [&_svg]:fill-destructive",
  denegada: "bg-destructive text-white [&_svg]:bg-destructive [&_svg]:fill-destructive",
  completada: "bg-muted-foreground text-background [&_svg]:bg-muted-foreground [&_svg]:fill-muted-foreground",
  devuelta_tarde:
    "border-2 border-warning bg-muted-foreground text-background [&_svg]:bg-muted-foreground [&_svg]:fill-muted-foreground",
  no_devuelta: "bg-destructive text-white [&_svg]:bg-destructive [&_svg]:fill-destructive",
}

export function TooltipEvento({ evento, children }: { evento: EventoCalendario; children: ReactElement }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent className={cn("whitespace-pre-line", COLOR_TOOLTIP[evento.estado] ?? COLOR_TOOLTIP.completada)}>
        {tooltipEvento(evento)}
      </TooltipContent>
    </Tooltip>
  )
}
