import { useMemo, useState } from "react"
import { Ban, ChevronLeft, ChevronRight } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  claveDe,
  desplazar,
  diasDelPeriodo,
  etiquetaPeriodo,
  hoy,
  mismoDia,
  NOMBRES_DIA,
  type DiaCivil,
} from "@/features/calendario/calendario-utils"
import { cn } from "@/lib/utils"

import { detalleDia, ESTILO_FESTIVO_INFORMATIVO, ESTILO_ORIGEN, useExcepciones } from "./excepciones-utils"

export function VistaCalendarioExcepciones({
  version,
  onSeleccionarFecha,
}: {
  version: number
  onSeleccionarFecha: (fecha: string) => void
}) {
  const [ancla, setAncla] = useState<DiaCivil>(hoy)
  const diaActual = hoy()

  const dias = useMemo(() => diasDelPeriodo("mes", ancla), [ancla])
  const desde = claveDe(dias[0])
  const hasta = claveDe(dias[dias.length - 1])
  const { dias: mapaDias, reglas, cargando, error } = useExcepciones(desde, hasta, version)

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1">
          <Button
            size="icon-sm"
            variant="outline"
            aria-label="Mes anterior"
            onClick={() => setAncla((a) => desplazar("mes", a, -1))}
          >
            <ChevronLeft />
          </Button>
          <Button size="sm" variant="outline" onClick={() => setAncla(hoy())}>
            Hoy
          </Button>
          <Button
            size="icon-sm"
            variant="outline"
            aria-label="Mes siguiente"
            onClick={() => setAncla((a) => desplazar("mes", a, 1))}
          >
            <ChevronRight />
          </Button>
          <p className="ml-2 text-sm font-medium">{etiquetaPeriodo("mes", ancla)}</p>
        </div>
      </div>

      {error && <p className="text-sm text-destructive">No se pudo cargar: {error}</p>}

      {cargando && !mapaDias.size ? (
        <Skeleton className="h-96 w-full" />
      ) : (
        <div className="overflow-hidden rounded-md border">
          <div className="grid grid-cols-7 border-b bg-muted/40">
            {NOMBRES_DIA.map((nombre) => (
              <div
                key={nombre}
                className="px-2 py-1.5 text-center text-xs font-medium text-muted-foreground"
              >
                {nombre}
              </div>
            ))}
          </div>
          <div className="grid grid-cols-7">
            {dias.map((dia) => {
              const clave = claveDe(dia)
              const info = mapaDias.get(clave)
              const esDelMes = dia.mes === ancla.mes
              const esHoy = mismoDia(dia, diaActual)
              const esPasado = clave < claveDe(diaActual)

              return (
                <button
                  key={clave}
                  type="button"
                  onClick={() => onSeleccionarFecha(clave)}
                  title={info ? detalleDia(info, reglas) : undefined}
                  className={cn(
                    "min-h-20 space-y-1 border-t border-l p-1 text-left first:border-l-0 [&:nth-child(7n+1)]:border-l-0",
                    !esDelMes && "opacity-50",
                    esPasado && "opacity-70",
                    info && (info.origen ? ESTILO_ORIGEN[info.origen].celda : ESTILO_FESTIVO_INFORMATIVO)
                  )}
                >
                  <span
                    className={cn(
                      "flex size-6 items-center justify-center rounded-full text-xs tabular-nums",
                      esHoy && "bg-primary font-semibold text-primary-foreground"
                    )}
                  >
                    {dia.dia}
                  </span>
                  {info?.bloqueada && (
                    <span className="flex items-center gap-1 text-[11px] truncate">
                      <Ban className="size-3 shrink-0" />
                      <span className="truncate">
                        {info.excepcion?.motivo || info.festivo || "No hábil"}
                      </span>
                    </span>
                  )}
                  {info && !info.bloqueada && (
                    <span className="block text-[11px] truncate">
                      {info.excepcion?.motivo || info.festivo || "Desbloqueada"}
                    </span>
                  )}
                </button>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
