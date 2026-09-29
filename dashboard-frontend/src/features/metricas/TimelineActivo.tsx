import { useEffect } from "react"

import {
  ArrowRightLeft,
  CalendarClock,
  CalendarPlus,
  FileSignature,
  PackagePlus,
  RefreshCw,
  Trash2,
  type LucideIcon,
} from "lucide-react"

import { usePolling } from "@/hooks/use-polling"
import { ESTADOS_ACTIVO } from "@/features/activos/tree-utils"
import { EstadoReservaBadge } from "@/features/calendario/EstadoBadge"
import {
  fetchTimelineActivo,
  type EstadoReserva,
  type EventoTimeline,
  type FiltroMetricas,
  type TipoEventoTimeline,
} from "@/lib/api"
import { formatearFechaHoraLACS } from "@/lib/fechas"
import { legible, telefono, Vacio } from "./MetricasPanel"

// El timeline es la historia completa del activo: no sigue el periodo de las métricas.
const TODO: FiltroMetricas = { periodo: "todo", desde: "", hasta: "" }

const ETIQUETA_MOTIVO: Record<string, string> = {
  devuelto_tarde: "Devuelto tarde",
  encontrado: "Encontrado",
  otro: "Otro",
}

// El tipo de evento se distingue por icono, no por color: el color queda
// para el estado de la reserva (los badges), como en el resto del panel.
const ICONO: Record<TipoEventoTimeline, LucideIcon> = {
  alta: PackagePlus,
  reserva_creada: CalendarPlus,
  reserva_estado: ArrowRightLeft,
  reserva_horario: CalendarClock,
  reserva_eliminada: Trash2,
  acta: FileSignature,
  activo_estado: RefreshCw,
}

const estadoActivo = (e: string | null) =>
  e ? (ESTADOS_ACTIVO[e as keyof typeof ESTADOS_ACTIVO]?.texto ?? legible(e, {})) : "Alta"

/** "9 oct 2026, 5:00 p.m. – 8:00 p.m.": la fecha no se repite si empieza y termina el mismo día. */
function rango(inicio: string | null, fin: string | null) {
  const a = formatearFechaHoraLACS(inicio)
  const b = formatearFechaHoraLACS(fin)
  const [diaA] = a.split(", ")
  const [diaB, horaB] = b.split(", ")
  return diaA === diaB && horaB ? `${a} – ${horaB}` : `${a} – ${b}`
}

function Titulo({ e }: { e: EventoTimeline }) {
  const badge = (s: string | null) => (s ? <EstadoReservaBadge estado={s as EstadoReserva} /> : null)
  switch (e.tipo) {
    case "alta":
      return <>Activo dado de alta</>
    case "reserva_creada":
      return <>Reserva creada {badge(e.estado_nuevo)}</>
    case "reserva_estado":
      return <>{badge(e.estado_anterior)} → {badge(e.estado_nuevo)}</>
    case "reserva_horario":
      return <>Horario cambiado a {rango(e.inicio, e.fin)}</>
    case "reserva_eliminada":
      return <>Reserva eliminada (estaba en {badge(e.estado_anterior)})</>
    case "acta":
      return <>Acta de {e.momento === "entrega" ? "entrega" : "devolución"} {e.origen === "firmada" ? "firmada" : "subida"}</>
    case "activo_estado":
      return <>{estadoActivo(e.estado_anterior)} → {estadoActivo(e.estado_nuevo)}</>
  }
}

function segundaLinea(e: EventoTimeline) {
  const partes = [
    e.tipo === "reserva_creada" && e.inicio ? rango(e.inicio, e.fin) : null,
    e.usuario || e.telefono_wa ? (e.usuario ? `${e.usuario} · ${telefono(e.telefono_wa)}` : telefono(e.telefono_wa)) : null,
    e.reserva_id ? `Reserva N.º ${e.reserva_id}` : null,
    e.tipo === "activo_estado" && e.detalle
      ? e.detalle.split(" · ").map((d, i) => (i === 0 ? legible(d, ETIQUETA_MOTIVO) : d)).join(" · ")
      : e.detalle,
  ]
  return partes.filter(Boolean).join(" · ")
}

export function TimelineActivo({ activoId }: { activoId: number }) {
  const { data, error, reload } = usePolling(() => fetchTimelineActivo(activoId, TODO), 60000)
  useEffect(() => {
    reload()
  }, [activoId, reload])

  if (error && !data) return <p className="text-sm text-destructive-text">No se pudo cargar el timeline.</p>
  if (!data || data.recurso_id !== activoId) return null
  if (data.eventos.length === 0) return <Vacio>Este activo todavía no tiene eventos.</Vacio>

  // Agrupa por día (en hora de la LACS) conservando el orden, que ya viene de más reciente a más antiguo.
  const dias: { dia: string; eventos: { e: EventoTimeline; hora: string }[] }[] = []
  for (const e of data.eventos) {
    const texto = formatearFechaHoraLACS(e.fecha)
    const i = texto.indexOf(",")
    const [dia, hora] = [texto.slice(0, i), texto.slice(i + 1).trim()]
    if (dias.at(-1)?.dia !== dia) dias.push({ dia, eventos: [] })
    dias.at(-1)!.eventos.push({ e, hora })
  }

  return (
    <div className="space-y-5">
      <p className="text-xs text-muted-foreground">Toda la historia del activo, de lo más reciente a lo más antiguo.</p>
      {data.truncado && (
        <p className="text-xs text-warning-text">Se muestran los 500 eventos más recientes; los anteriores no caben en esta vista.</p>
      )}
      {dias.map(({ dia, eventos }) => (
        <section key={dia} className="space-y-2">
          <h3 className="text-xs font-medium text-muted-foreground">{dia}</h3>
          <ol className="divide-y rounded-md border">
            {eventos.map(({ e, hora }, i) => {
              const Icono = ICONO[e.tipo]
              const detalle = segundaLinea(e)
              return (
                <li key={i} className="flex gap-3 px-3 py-2.5 text-sm">
                  <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground" aria-hidden>
                    <Icono className="size-3.5" />
                  </span>
                  <div className="min-w-0 flex-1 space-y-0.5">
                    <span className="flex min-h-7 flex-wrap items-center gap-1 font-medium">
                      <Titulo e={e} />
                    </span>
                    {detalle && <span className="block text-xs text-muted-foreground">{detalle}</span>}
                  </div>
                  <time className="shrink-0 pt-1.5 text-xs tabular-nums text-muted-foreground" dateTime={e.fecha}>
                    {hora}
                  </time>
                </li>
              )
            })}
          </ol>
        </section>
      ))}
    </div>
  )
}
