import { cn } from "@/lib/utils"

import {
  agruparPorDia,
  claveDe,
  estiloDe,
  etiquetaEvento,
  hora,
  horaCompacta,
  hoy,
  mismoDia,
  NOMBRES_DIA,
  type DiaCivil,
  type EventoCalendario,
} from "./calendario-utils"
import { IconoTipo } from "./IconoTipo"

// Más de esto en una celda y el mes deja de leerse; el resto se ve entrando
// al día.
const MAX_CHIPS = 3

function ChipEvento({
  evento,
  onSeleccionar,
}: {
  evento: EventoCalendario
  onSeleccionar: (evento: EventoCalendario) => void
}) {
  const estilo = estiloDe(evento.estado)
  return (
    <button
      type="button"
      onClick={() => onSeleccionar(evento)}
      title={`${hora(evento.inicio)} · ${etiquetaEvento(evento)} · ${evento.usuarioNombre}`}
      className={cn(
        "flex w-full items-center gap-1 rounded-sm border px-1 py-0.5 text-left text-[11px] leading-tight transition-colors",
        estilo.chip
      )}
    >
      <IconoTipo tipo={evento.tipo} className="size-3 shrink-0" />
      <span className="shrink-0 tabular-nums opacity-80">{horaCompacta(evento.inicio)}</span>
      <span className="truncate">{etiquetaEvento(evento)}</span>
    </button>
  )
}

export function VistaMes({
  dias,
  mesVisible,
  eventos,
  onSeleccionar,
  onAbrirDia,
}: {
  dias: DiaCivil[]
  mesVisible: number
  eventos: EventoCalendario[]
  onSeleccionar: (evento: EventoCalendario) => void
  onAbrirDia: (dia: DiaCivil) => void
}) {
  const porDia = agruparPorDia(eventos, dias)
  const diaActual = hoy()

  return (
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
          const delDia = porDia.get(clave) ?? []
          const esDelMes = dia.mes === mesVisible
          const esHoy = mismoDia(dia, diaActual)

          return (
            <div
              key={clave}
              className={cn(
                "min-h-28 space-y-1 border-t border-l p-1 first:border-l-0 [&:nth-child(7n+1)]:border-l-0",
                !esDelMes && "bg-muted/30"
              )}
            >
              <button
                type="button"
                onClick={() => onAbrirDia(dia)}
                className={cn(
                  "flex size-6 items-center justify-center rounded-full text-xs tabular-nums transition-colors hover:bg-accent",
                  !esDelMes && "text-muted-foreground",
                  esHoy && "bg-primary font-semibold text-primary-foreground hover:bg-primary/90"
                )}
                title="Ver este día"
              >
                {dia.dia}
              </button>

              {delDia.slice(0, MAX_CHIPS).map((evento) => (
                <ChipEvento key={evento.clave} evento={evento} onSeleccionar={onSeleccionar} />
              ))}

              {delDia.length > MAX_CHIPS && (
                <button
                  type="button"
                  onClick={() => onAbrirDia(dia)}
                  className="w-full px-1 text-left text-[11px] text-muted-foreground hover:text-foreground hover:underline"
                >
                  +{delDia.length - MAX_CHIPS} más
                </button>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
