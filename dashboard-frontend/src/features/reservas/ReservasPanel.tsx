import { useEffect, useMemo, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useActivosContext } from "@/features/activos/activos-context"
import {
  ZONA_CPA,
  agruparEnEventos,
  diaDe,
  etiquetaEvento,
  horaCompacta,
  mismoDia,
  ubicarReservas,
  type EventoCalendario,
} from "@/features/calendario/calendario-utils"
import { EstadoEventoBadge } from "@/features/calendario/EstadoBadge"
import { IconoTipo } from "@/features/calendario/IconoTipo"
import { ReservaDetalle } from "@/features/calendario/ReservaDetalle"
import { usePolling } from "@/hooks/use-polling"
import { fetchSesiones, type EstadoReserva } from "@/lib/api"
import { cn } from "@/lib/utils"

const FILTRO_STORAGE_KEY = "dashboard-cpa-reservas-estado"

// "por_confirmar" no es un estado de línea: junta las reservas que tienen
// algún activo pendiente de la CPA (parciales o pendientes del todo).
type FiltroEstado = "todas" | "por_confirmar" | EstadoReserva

const FILTROS: { valor: FiltroEstado; etiqueta: string }[] = [
  { valor: "todas", etiqueta: "Todas" },
  { valor: "por_confirmar", etiqueta: "Por confirmar" },
  { valor: "confirmada", etiqueta: "Confirmadas" },
  { valor: "completada", etiqueta: "Completadas" },
  { valor: "cancelada", etiqueta: "Canceladas" },
  { valor: "denegada", etiqueta: "Denegadas" },
  { valor: "no_show", etiqueta: "No show" },
]

function filtroInicial(): FiltroEstado {
  try {
    const guardado = localStorage.getItem(FILTRO_STORAGE_KEY)
    if (guardado && FILTROS.some((f) => f.valor === guardado)) return guardado as FiltroEstado
  } catch {
    // localStorage no disponible
  }
  return "todas"
}

const SEGURO_STORAGE_KEY = "dashboard-cpa-reservas-seguro"

function seguroInicial(): boolean {
  try {
    return localStorage.getItem(SEGURO_STORAGE_KEY) === "1"
  } catch {
    // localStorage no disponible
  }
  return false
}

// Más recientes arriba: las próximas y las de hoy son las que se consultan.
function porInicioDesc(a: EventoCalendario, b: EventoCalendario) {
  return b.inicio.getTime() - a.inicio.getTime()
}

function porInicioAsc(a: EventoCalendario, b: EventoCalendario) {
  return a.inicio.getTime() - b.inicio.getTime()
}

// --- Formato corto -------------------------------------------------------
// La tabla tiene que caber en el ancho que haya, así que usa fechas cortas
// ("22 sept 2026", "9am") en vez de las largas del modal.

const FECHA = new Intl.DateTimeFormat("es-CO", {
  timeZone: ZONA_CPA,
  day: "numeric",
  month: "short",
  year: "numeric",
})

function fechaCorta(fecha: Date) {
  return `${FECHA.format(fecha)}, ${horaCompacta(fecha)}`
}

/** Dos líneas: el día y el rango de horas, o inicio y fin si cambia de día. */
function horario(evento: EventoCalendario): [string, string] {
  const { inicio, fin } = evento
  if (mismoDia(diaDe(inicio), diaDe(fin))) {
    return [FECHA.format(inicio), `${horaCompacta(inicio)} – ${horaCompacta(fin)}`]
  }
  return [fechaCorta(inicio), `→ ${fechaCorta(fin)}`]
}

function numero(evento: EventoCalendario) {
  return evento.numeros.length ? evento.numeros.join(", ") : "-"
}

function uso(evento: EventoCalendario) {
  const primera = evento.reservas[0]
  return primera.tipo_uso === "asignatura" ? `NRC ${primera.nrc}` : "Particular"
}

function creada(evento: EventoCalendario) {
  const valor = evento.reservas[0].creado_en
  return valor ? fechaCorta(new Date(valor)) : "-"
}

function Activos({ evento }: { evento: EventoCalendario }) {
  return (
    <span className="flex items-start gap-1.5">
      <IconoTipo tipo={evento.tipo} className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
      <span className="min-w-0 break-words">{etiquetaEvento(evento)}</span>
    </span>
  )
}

/** Misma condición que el badge "Sale de Barranquilla · seguro". El dato es del grupo. */
function necesitaSeguro(evento: EventoCalendario) {
  return evento.reservas[0].sale_de_barranquilla === true
}

/** Aviso de que la reserva requiere trámite de seguro (ver ReservaDetalle). */
function SaleDeBarranquilla({ evento, className }: { evento: EventoCalendario; className?: string }) {
  if (!necesitaSeguro(evento)) return null
  return (
    <Badge
      variant="warning"
      className={className}
      title="Sale de Barranquilla: requiere trámite de seguro (mín. 5 días hábiles)"
    >
      Sale de Barranquilla · seguro
    </Badge>
  )
}

type PropsVista = {
  eventos: EventoCalendario[]
  onSeleccionar: (evento: EventoCalendario) => void
}

// --- Vistas ----------------------------------------------------------------
// Se eligen por el ancho del panel (container queries), no de la ventana:
// así también se adaptan cuando el sidebar se abre o se retrae.

/** Pantallas angostas: una tarjeta por reserva, sin columnas. */
function ListaReservas({ eventos, onSeleccionar }: PropsVista) {
  return (
    <ul className="divide-y rounded-md border">
      {eventos.map((evento) => {
        const [dia, horas] = horario(evento)
        return (
          <li key={evento.clave}>
            <button
              type="button"
              onClick={() => onSeleccionar(evento)}
              className="w-full space-y-1 px-3 py-2.5 text-left text-sm outline-none transition-colors hover:bg-muted/50 focus-visible:bg-muted/50"
            >
              <span className="flex items-start justify-between gap-2">
                <Activos evento={evento} />
                <span className="flex gap-1">
                  <SaleDeBarranquilla evento={evento} />
                  <EstadoEventoBadge evento={evento} />
                </span>
              </span>
              <span className="block text-xs text-muted-foreground">
                N.º {numero(evento)} · {evento.usuarioNombre} · {uso(evento)}
              </span>
              <span className="block text-xs text-muted-foreground">
                {dia} · {horas}
              </span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}

/**
 * Desde ~670 px: tabla. Usuario y Uso tienen columna propia solo si hay
 * espacio; si no, van en una línea bajo los activos. Creada es lo primero
 * que sobra.
 */
function TablaReservas({ eventos, onSeleccionar }: PropsVista) {
  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-12">N.º</TableHead>
            <TableHead>Activos</TableHead>
            <TableHead className="hidden @4xl:table-cell">Usuario</TableHead>
            <TableHead className="hidden @4xl:table-cell">Uso</TableHead>
            <TableHead>Horario</TableHead>
            <TableHead>Estado</TableHead>
            <TableHead className="hidden @5xl:table-cell">Creada</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {eventos.map((evento) => {
            const [dia, horas] = horario(evento)
            return (
              <TableRow
                key={evento.clave}
                tabIndex={0}
                className="cursor-pointer"
                onClick={() => onSeleccionar(evento)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault()
                    onSeleccionar(evento)
                  }
                }}
              >
                <TableCell className="tabular-nums">{numero(evento)}</TableCell>
                <TableCell className="whitespace-normal">
                  <Activos evento={evento} />
                  <SaleDeBarranquilla evento={evento} className="mt-1" />
                  <p className="mt-0.5 text-xs text-muted-foreground @4xl:hidden">
                    {evento.usuarioNombre} · {uso(evento)}
                  </p>
                </TableCell>
                <TableCell className="hidden whitespace-normal @4xl:table-cell">
                  <p>{evento.usuarioNombre}</p>
                  <p className="text-xs text-muted-foreground">{evento.usuarioTelefono}</p>
                </TableCell>
                <TableCell className="hidden @4xl:table-cell">{uso(evento)}</TableCell>
                <TableCell>
                  <p>{dia}</p>
                  <p className="text-xs text-muted-foreground">{horas}</p>
                </TableCell>
                <TableCell>
                  <EstadoEventoBadge evento={evento} />
                </TableCell>
                <TableCell className="hidden text-muted-foreground @5xl:table-cell">
                  {creada(evento)}
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}

export function ReservasPanel() {
  const { activos, reload: recargarActivos } = useActivosContext()
  const { data, error, loading, reload } = usePolling(fetchSesiones, 30000)
  const [filtro, setFiltro] = useState<FiltroEstado>(filtroInicial)
  const [soloSeguro, setSoloSeguro] = useState<boolean>(seguroInicial)
  const [idSeleccionado, setIdSeleccionado] = useState<number | null>(null)

  // El modal de edición necesita el catálogo para elegir activos; el
  // contexto no se carga solo (ver CalendarioPanel).
  useEffect(() => {
    recargarActivos()
  }, [recargarActivos])

  const ubicadas = useMemo(() => ubicarReservas(data ?? []), [data])

  // Igual que el calendario, se filtra por línea antes de agrupar: una
  // reserva con estados mixtos aparece en cada filtro con las líneas que
  // le corresponden.
  const eventosPorFiltro = useMemo(() => {
    const porFiltro = new Map(
      FILTROS.filter(({ valor }) => valor !== "por_confirmar").map(({ valor }) => [
        valor,
        agruparEnEventos(
          valor === "todas" ? ubicadas : ubicadas.filter((u) => u.reserva.estado === valor)
        ).sort(porInicioDesc),
      ])
    )
    // "Por confirmar" es una lista de pendientes por atender: va la reserva
    // completa (para ver también qué quedó confirmado) y la más próxima
    // primero.
    porFiltro.set(
      "por_confirmar",
      (porFiltro.get("todas") ?? []).filter((e) => e.porConfirmar).sort(porInicioAsc)
    )
    return porFiltro
  }, [ubicadas])

  // El checkbox "Necesita seguro" combina con AND sobre el filtro de estado
  // ya calculado; se recalcula aparte para no rehacer el agrupamiento.
  const eventosVisibles = useMemo(() => {
    if (!soloSeguro) return eventosPorFiltro
    return new Map<FiltroEstado, EventoCalendario[]>(
      [...eventosPorFiltro].map(([k, lista]) => [k, lista.filter(necesitaSeguro)])
    )
  }, [eventosPorFiltro, soloSeguro])

  const eventos = eventosVisibles.get(filtro) ?? []
  const pendientes = eventosVisibles.get("por_confirmar")?.length ?? 0

  // El modal se alimenta de la reserva completa, no de la filtrada: así
  // sigue abierto si una edición cambia el estado y la saca del filtro, y el
  // formulario no toma por activos de la reserva solo las líneas visibles.
  const seleccionado = useMemo(
    () =>
      eventosPorFiltro
        .get("todas")
        ?.find((e) => e.reservas.some((r) => r.id === idSeleccionado)) ?? null,
    [eventosPorFiltro, idSeleccionado]
  )

  function cambiarFiltro(nuevo: FiltroEstado) {
    setFiltro(nuevo)
    try {
      localStorage.setItem(FILTRO_STORAGE_KEY, nuevo)
    } catch {
      // localStorage no disponible
    }
  }

  function cambiarSeguro(nuevo: boolean) {
    setSoloSeguro(nuevo)
    try {
      localStorage.setItem(SEGURO_STORAGE_KEY, nuevo ? "1" : "0")
    } catch {
      // localStorage no disponible
    }
  }

  function seleccionar(evento: EventoCalendario) {
    setIdSeleccionado(evento.reservas[0].id)
  }

  return (
    <div className="@container space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Reservas
        </h2>
        <Button size="sm" variant="outline" onClick={() => reload()}>
          Refrescar
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <Select value={filtro} onValueChange={(v) => cambiarFiltro(v as FiltroEstado)}>
          <SelectTrigger size="sm" className="min-w-44" aria-label="Filtrar por estado">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {FILTROS.map(({ valor, etiqueta }) => {
              const cantidad = eventosVisibles.get(valor)?.length ?? 0
              // Las que esperan a la CPA resaltan para que no se queden sin atender.
              const resaltar = valor === "por_confirmar" && cantidad > 0
              return (
                <SelectItem key={valor} value={valor}>
                  {etiqueta}
                  <span
                    className={cn(
                      "text-xs tabular-nums",
                      resaltar ? "font-semibold text-warning" : "text-muted-foreground"
                    )}
                  >
                    {data ? cantidad : "–"}
                  </span>
                </SelectItem>
              )
            })}
          </SelectContent>
        </Select>

        {/* Si el filtro activo no es "por confirmar", que las pendientes no
            se pierdan de vista igual. */}
        {filtro !== "por_confirmar" && pendientes > 0 && (
          <p className="text-xs text-warning">
            {pendientes} por confirmar
          </p>
        )}

        <div className="flex items-center gap-2">
          <Checkbox
            id="reservas-solo-seguro"
            checked={soloSeguro}
            onCheckedChange={(v) => cambiarSeguro(v === true)}
          />
          <Label htmlFor="reservas-solo-seguro" className="cursor-pointer">
            Necesita seguro
          </Label>
        </div>
      </div>

      {loading && !data && (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      )}

      {error && !data && (
        <p className="text-sm text-destructive">No se pudieron cargar las reservas: {error}</p>
      )}

      {data && eventos.length === 0 && (
        <p className="rounded-md border p-4 text-sm text-muted-foreground">
          {filtro === "por_confirmar" && !soloSeguro
            ? "No hay reservas pendientes de confirmación."
            : soloSeguro
              ? "No hay reservas que requieran seguro para este filtro."
              : "No hay reservas para este filtro."}
        </p>
      )}

      {data && eventos.length > 0 && (
        <>
          {filtro === "por_confirmar" && (
            <p className="text-sm text-muted-foreground">
              Abre una reserva para confirmar o denegar cada activo pendiente. El estudiante
              recibe un WhatsApp con cada decisión.
            </p>
          )}
          <div className="@2xl:hidden">
            <ListaReservas eventos={eventos} onSeleccionar={seleccionar} />
          </div>
          <div className="hidden @2xl:block">
            <TablaReservas eventos={eventos} onSeleccionar={seleccionar} />
          </div>
        </>
      )}

      <p className="text-xs text-muted-foreground">
        Horarios en hora local de la CPA (America/Bogota). Haz clic en una reserva para ver el
        detalle o editarla.
      </p>

      <ReservaDetalle
        evento={seleccionado}
        recursos={activos}
        onClose={() => setIdSeleccionado(null)}
        onGuardado={reload}
      />
    </div>
  )
}
