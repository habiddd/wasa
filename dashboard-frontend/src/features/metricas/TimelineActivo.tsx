import { useEffect } from "react"

import { Badge } from "@/components/ui/badge"
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
import { filtroValido, legible, respuestaDe, telefono, Vacio } from "./MetricasPanel"

const ETIQUETA_MOTIVO: Record<string, string> = {
  devuelto_tarde: "Devuelto tarde",
  encontrado: "Encontrado",
  otro: "Otro",
}

// Un color de punto por tipo, con tokens del tema.
const PUNTO: Record<TipoEventoTimeline, string> = {
  alta: "bg-primary",
  reserva_creada: "bg-success",
  reserva_estado: "bg-muted-foreground",
  reserva_horario: "bg-warning",
  reserva_eliminada: "bg-destructive",
  acta: "bg-foreground",
  activo_estado: "bg-warning",
}

const estadoActivo = (e: string | null) =>
  e ? (ESTADOS_ACTIVO[e as keyof typeof ESTADOS_ACTIVO]?.texto ?? legible(e, {})) : "Alta"

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
      return <>Horario cambiado a {formatearFechaHoraLACS(e.inicio)} – {formatearFechaHoraLACS(e.fin)}</>
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
    e.tipo === "reserva_creada" && e.inicio ? `${formatearFechaHoraLACS(e.inicio)} – ${formatearFechaHoraLACS(e.fin)}` : null,
    e.usuario || e.telefono_wa ? (e.usuario ? `${e.usuario} · ${telefono(e.telefono_wa)}` : telefono(e.telefono_wa)) : null,
    e.reserva_id ? `Reserva N.º ${e.reserva_id}` : null,
    e.tipo === "activo_estado" && e.detalle
      ? e.detalle.split(" · ").map((d, i) => (i === 0 ? legible(d, ETIQUETA_MOTIVO) : d)).join(" · ")
      : e.detalle,
  ]
  return partes.filter(Boolean).join(" · ")
}

export function TimelineActivo({ activoId, filtro }: { activoId: number; filtro: FiltroMetricas }) {
  const { data, error, reload } = usePolling(
    () => (filtroValido(filtro) ? fetchTimelineActivo(activoId, filtro) : Promise.resolve(null)),
    60000,
  )
  useEffect(() => {
    reload()
  }, [activoId, filtro.periodo, filtro.desde, filtro.hasta, reload])

  if (error && !data) return <p className="text-sm text-destructive-text">No se pudo cargar el timeline.</p>
  if (!data || data.recurso_id !== activoId || !filtroValido(filtro) || !respuestaDe(data, filtro)) return null
  if (data.eventos.length === 0) return <Vacio>Sin eventos en este periodo.</Vacio>

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
    <div className="space-y-4">
      {data.truncado && (
        <Badge variant="warning">Se muestran los 500 eventos más recientes; acota el periodo para ver los anteriores.</Badge>
      )}
      {dias.map(({ dia, eventos }) => (
        <section key={dia} className="space-y-1">
          <h3 className="text-sm font-medium">{dia}</h3>
          <ol className="divide-y rounded-md border">
            {eventos.map(({ e, hora }, i) => (
              <li key={i} className="flex gap-3 px-3 py-2 text-sm">
                <span className={`mt-1.5 size-2 shrink-0 rounded-full ${PUNTO[e.tipo]}`} aria-hidden />
                <div className="min-w-0 flex-1 space-y-0.5">
                  <span className="flex flex-wrap items-center gap-1 font-medium">
                    <Titulo e={e} />
                  </span>
                  <span className="block text-xs text-muted-foreground">{segundaLinea(e)}</span>
                </div>
                <time className="shrink-0 text-xs tabular-nums text-muted-foreground" dateTime={e.fecha}>
                  {hora}
                </time>
              </li>
            ))}
          </ol>
        </section>
      ))}
    </div>
  )
}
