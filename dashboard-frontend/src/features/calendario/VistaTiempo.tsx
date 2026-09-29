import { useEffect, useRef, useState } from "react"

import { cn } from "@/lib/utils"

import {
  agruparPorDia,
  bloquesDelDia,
  claveDe,
  diaDeSemana,
  estiloDe,
  etiquetaEvento,
  finDelDia,
  hora,
  hoy,
  inicioDelDia,
  mismoDia,
  NOMBRES_DIA,
  type BloqueDia,
  type DiaCivil,
  type EventoCalendario,
} from "./calendario-utils"
import { IconoTipo } from "./IconoTipo"
import { TooltipEvento } from "./TooltipEvento"

const ALTO_HORA = 48
const HORAS = Array.from({ length: 24 }, (_, h) => h)
// La rejilla arranca desplazada a la mañana: las reservas de madrugada son
// raras y si no, se abre con seis horas vacías arriba.
const HORA_INICIAL_VISIBLE = 7

// Anchos (px) por debajo de los cuales el texto ya no cabe y solo estorba:
// con varias reservas solapadas la columna se parte en varias y el nombre
// terminaría rompiéndose letra a letra. El tooltip y el clic siguen dando
// toda la información.
const ANCHO_MINIMO_TEXTO = 44
const ANCHO_MINIMO_DETALLE = 100

function etiquetaHora(h: number) {
  if (h === 0) return "12am"
  if (h === 12) return "12pm"
  return h < 12 ? `${h}am` : `${h - 12}pm`
}

function BloqueEvento({
  bloque,
  anchoPx,
  onSeleccionar,
}: {
  bloque: BloqueDia<EventoCalendario>
  /** Ancho real del bloque; 0 mientras no se haya podido medir la columna. */
  anchoPx: number
  onSeleccionar: (evento: EventoCalendario) => void
}) {
  const estilo = estiloDe(bloque.estado)
  const ancho = 100 / bloque.columnas
  const soloIcono = anchoPx > 0 && anchoPx < ANCHO_MINIMO_TEXTO
  const compacto = bloque.altoPct < 3 || (anchoPx > 0 && anchoPx < ANCHO_MINIMO_DETALLE)

  return (
    <TooltipEvento evento={bloque}>
    <button
      type="button"
      onClick={() => onSeleccionar(bloque)}
      style={{
        top: `${bloque.topPct}%`,
        height: `${bloque.altoPct}%`,
        left: `calc(${bloque.columna * ancho}% + 2px)`,
        width: `calc(${ancho}% - 4px)`,
      }}
      className={cn(
        // flex-col porque un <button> centra su contenido: el texto debe
        // quedar arriba, junto a la hora de inicio del bloque.
        "absolute flex flex-col items-start overflow-hidden rounded-sm border px-1 py-0.5 text-left text-[11px] leading-tight transition-colors",
        estilo.chip,
        bloque.continuaAntes && "rounded-t-none border-t-dashed",
        bloque.continuaDespues && "rounded-b-none border-b-dashed"
      )}
    >
      <span className="flex w-full min-w-0 items-center gap-1 font-medium">
        <IconoTipo tipo={bloque.tipo} className="size-3 shrink-0" />
        {!soloIcono && <span className="truncate">{etiquetaEvento(bloque)}</span>}
      </span>
      {!compacto && (
        <>
          <span className="block w-full truncate tabular-nums">
            {hora(bloque.inicio)} – {hora(bloque.fin)}
          </span>
          <span className="block w-full truncate">{bloque.usuarioNombre}</span>
        </>
      )}
    </button>
    </TooltipEvento>
  )
}

function LineaAhora({ dia }: { dia: DiaCivil }) {
  const [ahora, setAhora] = useState(() => new Date())

  useEffect(() => {
    const id = setInterval(() => setAhora(new Date()), 60000)
    return () => clearInterval(id)
  }, [])

  const desde = inicioDelDia(dia).getTime()
  const hasta = finDelDia(dia).getTime()
  const t = ahora.getTime()
  if (t < desde || t >= hasta) return null

  return (
    <div
      className="pointer-events-none absolute inset-x-0 z-10 border-t-2 border-destructive"
      style={{ top: `${((t - desde) / (hasta - desde)) * 100}%` }}
    >
      <span className="absolute -top-1 -left-1 size-2 rounded-full bg-destructive" />
    </div>
  )
}

export function VistaTiempo({
  dias,
  eventos,
  onSeleccionar,
}: {
  dias: DiaCivil[]
  eventos: EventoCalendario[]
  onSeleccionar: (evento: EventoCalendario) => void
}) {
  const porDia = agruparPorDia(eventos, dias)
  const diaActual = hoy()
  const scrollRef = useRef<HTMLDivElement>(null)
  const columnaRef = useRef<HTMLDivElement>(null)
  const [anchoColumna, setAnchoColumna] = useState(0)

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = HORA_INICIAL_VISIBLE * ALTO_HORA
  }, [])

  // La rejilla llena lo que queda de ventana bajo ella, para que solo haya
  // una barra de desplazamiento (la de las horas) y no también la de la página.
  const [altoVisible, setAltoVisible] = useState<number>()
  useEffect(() => {
    const medir = () => {
      const el = scrollRef.current
      if (!el) return
      const top = el.getBoundingClientRect().top + window.scrollY
      // 24px = padding inferior del contenedor del panel (p-6, ver App.tsx), 1px = borde del recuadro.
      setAltoVisible(Math.max(240, window.innerHeight - top - 25))
    }
    medir()
    window.addEventListener("resize", medir)
    return () => window.removeEventListener("resize", medir)
  }, [])

  // Todas las columnas de día miden lo mismo (1fr): con el ancho de una basta
  // para saber cuánto texto cabe en cada bloque.
  useEffect(() => {
    const columna = columnaRef.current
    if (!columna || typeof ResizeObserver === "undefined") return
    const observador = new ResizeObserver(([entrada]) => setAnchoColumna(entrada.contentRect.width))
    observador.observe(columna)
    return () => observador.disconnect()
  }, [])

  const columnas = `3.5rem repeat(${dias.length}, minmax(0, 1fr))`
  const alto = 24 * ALTO_HORA

  return (
    <div className="overflow-hidden rounded-md border">
      <div className="grid border-b bg-muted/40" style={{ gridTemplateColumns: columnas }}>
        <div />
        {dias.map((dia) => {
          const esHoy = mismoDia(dia, diaActual)
          return (
            <div key={claveDe(dia)} className="border-l px-1 py-1.5 text-center">
              <p className="text-xs text-muted-foreground">{NOMBRES_DIA[diaDeSemana(dia)]}</p>
              <p
                className={cn(
                  "mx-auto flex size-6 items-center justify-center rounded-full text-xs tabular-nums",
                  esHoy && "bg-primary font-semibold text-primary-foreground"
                )}
              >
                {dia.dia}
              </p>
            </div>
          )
        })}
      </div>

      <div ref={scrollRef} className="overflow-y-auto" style={{ height: altoVisible ?? "65vh" }}>
        <div className="grid" style={{ gridTemplateColumns: columnas }}>
          <div className="relative" style={{ height: alto }}>
            {HORAS.map((h) => (
              <span
                key={h}
                className="absolute right-1 -translate-y-1/2 text-[10px] text-muted-foreground tabular-nums"
                style={{ top: h * ALTO_HORA }}
              >
                {h === 0 ? "" : etiquetaHora(h)}
              </span>
            ))}
          </div>

          {dias.map((dia, indice) => (
            <div
              key={claveDe(dia)}
              ref={indice === 0 ? columnaRef : undefined}
              className="relative border-l"
              style={{ height: alto }}
            >
              {HORAS.map((h) => (
                <div
                  key={h}
                  className={cn(
                    "absolute inset-x-0 border-t",
                    h % 6 === 0 ? "border-border" : "border-border/50"
                  )}
                  style={{ top: h * ALTO_HORA }}
                />
              ))}

              {mismoDia(dia, diaActual) && <LineaAhora dia={dia} />}

              {bloquesDelDia(porDia.get(claveDe(dia)) ?? [], dia).map((bloque) => (
                <BloqueEvento
                  key={bloque.clave}
                  bloque={bloque}
                  anchoPx={anchoColumna / bloque.columnas}
                  onSeleccionar={onSeleccionar}
                />
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
