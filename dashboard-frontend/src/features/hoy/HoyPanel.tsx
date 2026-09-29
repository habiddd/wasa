import { useEffect, useMemo, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { useActivosContext } from "@/features/activos/activos-context"
import {
  agruparEnEventos,
  diaDe,
  etiquetaEvento,
  etiquetaPeriodo,
  hora,
  hoy,
  mismoDia,
  ubicarReservas,
  type EventoCalendario,
} from "@/features/calendario/calendario-utils"
import { IconoTipo } from "@/features/calendario/IconoTipo"
import { ReservaDetalle } from "@/features/calendario/ReservaDetalle"
import { BotonActaPdf } from "@/features/reservas/BotonActaPdf"
import { useReservasVentana } from "@/features/reservas/use-reservas-ventana"
import type { EstadoRecurso, Sesion } from "@/lib/api"
import { cn } from "@/lib/utils"

type Bloque = "atrasados" | "por_entregar" | "por_recibir" | "por_confirmar"

// El orden del array solo controla cómo se pinta. `bloqueDe` decide el bloque
// con su propio orden de chequeo (atrasados primero), así un evento vencido
// nunca cae en por_recibir y no aparece dos veces.
const BLOQUES: { valor: Bloque; titulo: string; vacio: string }[] = [
  { valor: "por_entregar", titulo: "Por entregar hoy", vacio: "No hay entregas pendientes hoy." },
  { valor: "por_recibir", titulo: "Por recibir hoy", vacio: "No hay devoluciones pendientes hoy." },
  { valor: "por_confirmar", titulo: "Por confirmar", vacio: "No hay solicitudes esperando respuesta." },
  { valor: "atrasados", titulo: "Retrasados y no devueltos", vacio: "Nada retrasado ni sin devolver." },
]

const HORA_MS = 60 * 60 * 1000

// Una línea no_devuelta sigue abierta mientras su activo siga en "no_devuelto".
// Activo desconocido (catálogo aún cargando) = abierta, para no parpadear.
function sinDevolver(r: Sesion, estadoDe: Map<number, EstadoRecurso>) {
  return r.estado === "no_devuelta" && (estadoDe.get(r.recurso_id) ?? "no_devuelto") === "no_devuelto"
}

function bloqueDe(
  evento: EventoCalendario,
  dia: ReturnType<typeof hoy>,
  ahora: number,
  estadoDe: Map<number, EstadoRecurso>
): Bloque | null {
  const estados = new Set(evento.reservas.map((r) => r.estado))
  // No devuelta: el préstamo se cerró sin el equipo -- sigue en Hoy hasta que el
  // activo salga de "no_devuelto" (aunque ya no esté "retrasada", que solo aplica a entregada).
  if (evento.retrasada || evento.reservas.some((r) => sinDevolver(r, estadoDe))) return "atrasados"
  if (estados.has("confirmada") && mismoDia(diaDe(evento.inicio), dia)) return "por_entregar"
  if (estados.has("entregada") && mismoDia(diaDe(evento.fin), dia)) return "por_recibir"
  if (evento.porConfirmar && evento.fin.getTime() > ahora) return "por_confirmar"
  return null
}

// La hora que importa en cada bloque: cuándo se entrega o cuándo vence.
function momentoDe(evento: EventoCalendario, bloque: Bloque) {
  return bloque === "por_recibir" || bloque === "atrasados" ? evento.fin : evento.inicio
}

const RELATIVO = new Intl.RelativeTimeFormat("es", { numeric: "auto", style: "short" })

/** "en 20 min", "hace 2 h", "hace 3 días": lo que el monitor lee de un vistazo. */
function relativo(fecha: Date, ahora: number) {
  const minutos = Math.round((fecha.getTime() - ahora) / 60000)
  if (Math.abs(minutos) < 60) return RELATIVO.format(minutos, "minute")
  const horas = Math.round(minutos / 60)
  if (Math.abs(horas) < 24) return RELATIVO.format(horas, "hour")
  return RELATIVO.format(Math.round(horas / 24), "day")
}

function uso(evento: EventoCalendario) {
  const primera = evento.reservas[0]
  return primera.tipo_uso === "asignatura" ? `NRC ${primera.nrc}` : "Particular"
}

function Fila({
  evento,
  bloque,
  ahora,
  sinDevolver,
  onAbrir,
  onRegistrada,
}: {
  evento: EventoCalendario
  bloque: Bloque
  ahora: number
  sinDevolver: boolean
  onAbrir: () => void
  onRegistrada: () => void
}) {
  const momento = momentoDe(evento, bloque)
  const falta = momento.getTime() - ahora
  // Atrasado, o ya pasó la hora de entrega: el tiempo se lee como alerta.
  const vencido = bloque === "atrasados" || (bloque === "por_entregar" && falta < 0)
  const pronto = !vencido && falta >= 0 && falta <= HORA_MS
  const firmable = bloque === "por_entregar" || bloque === "por_recibir"

  return (
    <li className="grid grid-cols-[5.5rem_minmax(0,1fr)] items-start gap-x-3 gap-y-2 px-3 py-2.5 sm:flex sm:items-center sm:gap-x-4">
      <div className="sm:w-24 sm:shrink-0">
        <p className="text-sm font-medium tabular-nums">{hora(momento)}</p>
        <p
          className={cn(
            "text-xs tabular-nums",
            vencido ? "text-destructive-text" : pronto ? "text-warning-text" : "text-muted-foreground"
          )}
        >
          {relativo(momento, ahora)}
        </p>
      </div>

      <button
        type="button"
        onClick={onAbrir}
        className="min-w-0 rounded-md text-left sm:flex-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="flex items-start gap-1.5 text-sm font-medium">
          <IconoTipo tipo={evento.tipo} className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
          <span className="min-w-0 break-words">{etiquetaEvento(evento)}</span>
        </span>
        <span className="mt-0.5 block text-xs text-muted-foreground">
          {evento.usuarioNombre} · {uso(evento)}
          {evento.numeros.length > 0 && ` · N.º ${evento.numeros.join(", ")}`}
        </span>
      </button>

      <div className="col-start-2 flex items-center gap-2 sm:col-start-auto sm:shrink-0">
        {evento.parcial && <Badge variant="warning">Parcial</Badge>}
        {sinDevolver && <Badge variant="destructive">No devuelta</Badge>}
        {firmable ? (
          <BotonActaPdf
            grupoId={evento.numeros[0] ?? null}
            reservas={evento.reservas}
            usuarioNombre={evento.usuarioNombre}
            fin={evento.fin}
            variante="boton"
            principal
            onRegistrada={onRegistrada}
          />
        ) : (
          <Button size="sm" variant="outline" onClick={onAbrir}>
            {bloque === "por_confirmar" ? "Revisar y confirmar" : "Ver reserva"}
          </Button>
        )}
      </div>
    </li>
  )
}

/**
 * Pantalla de inicio del mostrador: qué hay que hacer ahora (los retrasados
 * y no devueltos van al final). No trae datos propios -- es la misma ventana de Reservas
 * (useReservasVentana, sondeo cada 30 s) filtrada en cliente.
 */
export function HoyPanel() {
  const { activos, reload: recargarActivos } = useActivosContext()
  // incluirAbiertas: un equipo entregado o no devuelto sigue en "Retrasados y no devueltos"
  // aunque su plazo haya vencido hace más de los 30 días de la ventana.
  const { reservas, cargando, error, reload } = useReservasVentana("30d", true)
  const [idSeleccionado, setIdSeleccionado] = useState<number | null>(null)

  // El detalle necesita el catálogo para editar (igual que Reservas y Calendario).
  useEffect(() => {
    recargarActivos()
  }, [recargarActivos])

  const { porBloque, eventos, estadoDe } = useMemo(() => {
    const ahora = Date.now()
    const dia = hoy()
    const eventos = agruparEnEventos(ubicarReservas(reservas))
    const estadoDe = new Map(activos.map((a) => [a.id, a.estado]))
    const porBloque = new Map<Bloque, EventoCalendario[]>(BLOQUES.map((b) => [b.valor, []]))
    for (const evento of eventos) {
      const bloque = bloqueDe(evento, dia, ahora, estadoDe)
      if (bloque) porBloque.get(bloque)!.push(evento)
    }
    for (const [bloque, lista] of porBloque) {
      lista.sort((a, b) => momentoDe(a, bloque).getTime() - momentoDe(b, bloque).getTime())
    }
    return { porBloque, eventos, estadoDe }
  }, [reservas, activos])

  const seleccionado = useMemo(
    () => eventos.find((e) => e.reservas.some((r) => r.id === idSeleccionado)) ?? null,
    [eventos, idSeleccionado]
  )

  const ahora = Date.now()
  const total = BLOQUES.reduce((n, b) => n + (porBloque.get(b.valor)?.length ?? 0), 0)

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Hoy</h1>
        <p className="text-sm text-muted-foreground">{etiquetaPeriodo("dia", hoy())}</p>
      </div>

      {error && !reservas.length ? (
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-sm text-destructive-text">No se pudieron cargar las reservas: {error}</p>
          <Button size="sm" variant="outline" onClick={reload}>
            Reintentar
          </Button>
        </div>
      ) : cargando && !reservas.length ? (
        <div className="space-y-6">
          {BLOQUES.map((b) => (
            <Skeleton key={b.valor} className="h-24 w-full" />
          ))}
        </div>
      ) : total === 0 ? (
        <p className="text-sm text-muted-foreground">
          Sin entregas, devoluciones ni solicitudes pendientes hoy.
        </p>
      ) : (
        BLOQUES.map(({ valor, titulo, vacio }) => {
          const lista = porBloque.get(valor) ?? []
          return (
            <section key={valor} aria-labelledby={`hoy-${valor}`} className="space-y-2">
              <h2 id={`hoy-${valor}`} className="flex items-baseline gap-2 text-sm font-semibold">
                {titulo}
                <span
                  className={cn(
                    "tabular-nums",
                    valor === "atrasados" && lista.length > 0
                      ? "text-destructive-text"
                      : "font-normal text-muted-foreground"
                  )}
                >
                  {lista.length}
                </span>
              </h2>
              {lista.length === 0 ? (
                <p className="text-sm text-muted-foreground">{vacio}</p>
              ) : (
                <ul className="divide-y rounded-md border">
                  {lista.map((evento) => (
                    <Fila
                      key={evento.clave}
                      evento={evento}
                      bloque={valor}
                      ahora={ahora}
                      sinDevolver={evento.reservas.some((r) => sinDevolver(r, estadoDe))}
                      onAbrir={() => setIdSeleccionado(evento.reservas[0].id)}
                      onRegistrada={reload}
                    />
                  ))}
                </ul>
              )}
            </section>
          )
        })
      )}

      <ReservaDetalle
        evento={seleccionado}
        recursos={activos}
        onClose={() => setIdSeleccionado(null)}
        onGuardado={reload}
      />
    </div>
  )
}
