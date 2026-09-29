import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { ChevronLeft, ChevronRight, Plus } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { useActivosContext } from "@/features/activos/activos-context"
import { NuevaReservaDialog } from "@/features/reservas/NuevaReservaDialog"
import {
  fetchReservasRango,
  type EstadoReserva,
  type ReservaCreada,
  type Sesion,
} from "@/lib/api"

import {
  agruparEnEventos,
  FILTROS_ESTADO_COMUNES,
  desplazar,
  diaDe,
  diasDelPeriodo,
  etiquetaPeriodo,
  finDelDia,
  hoy,
  inicioDelDia,
  ubicarReservas,
  type DiaCivil,
  type Vista,
} from "./calendario-utils"
import { LeyendaEstados } from "./LeyendaEstados"
import { ReservaDetalle } from "./ReservaDetalle"
import { VistaMes } from "./VistaMes"
import { VistaTiempo } from "./VistaTiempo"

const PREFIJO_STORAGE = "dashboard-lacs-calendario-"
const REFRESCO_MS = 60000

// "retrasada" no es un estado de línea (ver el mismo comentario en
// ReservasPanel): es un pseudo-filtro sobre evento.retrasada, derivado de
// estado == "entregada" && fin < ahora.
type FiltroEstado = "vigentes" | "todas" | "retrasada" | EstadoReserva
type FiltroTipo = "todos" | "equipo" | "espacio"
const USUARIO_SIN_CODIGO = "__sin_codigo__"

// Los estados salen de FILTROS_ESTADO_COMUNES, igual que en ReservasPanel: es el mismo
// concepto de estado, solo que acá vive en un <Select> en vez de una barra
// de chips.
const ESTADOS: { valor: FiltroEstado; etiqueta: string }[] = [
  { valor: "vigentes", etiqueta: "Sin canceladas ni denegadas" },
  { valor: "todas", etiqueta: "Todos los estados" },
  { valor: "pendiente", etiqueta: "Por confirmar" },
  ...FILTROS_ESTADO_COMUNES,
]

function guardado<T extends string>(clave: string, valores: readonly T[], porDefecto: T): T {
  try {
    const valor = localStorage.getItem(PREFIJO_STORAGE + clave) as T | null
    if (valor && valores.includes(valor)) return valor
  } catch {
    // localStorage no disponible
  }
  return porDefecto
}

function guardar(clave: string, valor: string) {
  try {
    localStorage.setItem(PREFIJO_STORAGE + clave, valor)
  } catch {
    // localStorage no disponible
  }
}

export function CalendarioPanel() {
  const { activos, reload: recargarActivos } = useActivosContext()

  const [vista, setVista] = useState<Vista>(() =>
    guardado("vista", ["mes", "semana", "dia"] as const, "mes")
  )
  const [ancla, setAncla] = useState<DiaCivil>(hoy)
  const [tipo, setTipo] = useState<FiltroTipo>(() =>
    guardado("tipo", ["todos", "equipo", "espacio"] as const, "todos")
  )
  const [recursoId, setRecursoId] = useState("todos")
  const [usuarioCodigo, setUsuarioCodigo] = useState("todos")
  const [filtroEstado, setFiltroEstado] = useState<FiltroEstado>(() =>
    guardado("estado", ESTADOS.map((e) => e.valor) as FiltroEstado[], "vigentes")
  )

  const [reservas, setReservas] = useState<Sesion[]>([])
  const [cargando, setCargando] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [idSeleccionado, setIdSeleccionado] = useState<number | null>(null)
  const [creando, setCreando] = useState(false)

  // El catálogo de recursos alimenta el filtro por activo/lugar; el contexto
  // no se carga solo (ver ActivosPanel/LugaresPanel).
  useEffect(() => {
    recargarActivos()
  }, [recargarActivos])

  const dias = useMemo(() => diasDelPeriodo(vista, ancla), [vista, ancla])
  const desde = useMemo(() => inicioDelDia(dias[0]), [dias])
  const hasta = useMemo(() => finDelDia(dias[dias.length - 1]), [dias])

  // Descarta respuestas viejas: al navegar rápido entre meses las peticiones
  // pueden volver desordenadas.
  const peticionRef = useRef(0)

  const cargar = useCallback(async () => {
    const peticion = ++peticionRef.current
    try {
      setCargando(true)
      const data = await fetchReservasRango(desde, hasta)
      if (peticion !== peticionRef.current) return
      setReservas(data)
      setError(null)
    } catch (err) {
      if (peticion !== peticionRef.current) return
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      if (peticion === peticionRef.current) setCargando(false)
    }
  }, [desde, hasta])

  useEffect(() => {
    cargar()
    const id = setInterval(cargar, REFRESCO_MS)
    return () => clearInterval(id)
  }, [cargar])

  const ubicadas = useMemo(() => ubicarReservas(reservas), [reservas])

  const recursos = useMemo(() => {
    const lista = tipo === "todos" ? activos : activos.filter((r) => r.tipo === tipo)
    return [...lista].sort((a, b) => a.nombre.localeCompare(b.nombre, "es"))
  }, [activos, tipo])

  const usuarios = useMemo(() => {
    const vistos = new Map<string, string>()
    for (const { reserva } of ubicadas) {
      const codigo = reserva.usuario_codigo_uninorte || USUARIO_SIN_CODIGO
      if (!vistos.has(codigo)) {
        vistos.set(codigo, reserva.usuario_nombre || reserva.usuario_telefono)
      }
    }
    return [...vistos.entries()].sort(([codigoA], [codigoB]) =>
      codigoA.localeCompare(codigoB, "es")
    )
  }, [ubicadas])

  const filtradas = useMemo(
    () =>
      ubicadas.filter(({ reserva }) => {
        if (tipo !== "todos" && reserva.recurso_tipo !== tipo) return false
        if (recursoId !== "todos" && String(reserva.recurso_id) !== recursoId) return false
        if (usuarioCodigo !== "todos") {
          const codigo = reserva.usuario_codigo_uninorte || USUARIO_SIN_CODIGO
          if (codigo !== usuarioCodigo) return false
        }
        if (filtroEstado === "vigentes") {
          return reserva.estado !== "cancelada" && reserva.estado !== "denegada"
        }
        // A diferencia de los demás valores de FiltroEstado, "retrasada" no
        // compara contra `reserva.estado`: es el campo derivado que manda el
        // backend (ver Sesion.retrasada / actas_service.esta_retrasada).
        if (filtroEstado === "retrasada") return reserva.retrasada
        if (filtroEstado !== "todas") return reserva.estado === filtroEstado
        return true
      }),
    [ubicadas, tipo, recursoId, usuarioCodigo, filtroEstado]
  )

  // Se agrupa después de filtrar: si se filtra por un activo concreto, el
  // evento muestra solo esa línea, no toda la reserva.
  const eventos = useMemo(() => agruparEnEventos(filtradas), [filtradas])

  // Se guarda el id de una de sus reservas, no el objeto ni la clave: así el
  // modal sigue abierto y con los datos nuevos aunque la edición cambie el
  // horario (que es parte de la clave del evento), y se cierra solo si esa
  // reserva deja de caer en el periodo visible.
  const seleccionado = useMemo(
    () => eventos.find((e) => e.reservas.some((r) => r.id === idSeleccionado)) ?? null,
    [eventos, idSeleccionado]
  )

  function cambiarVista(nueva: Vista) {
    setVista(nueva)
    guardar("vista", nueva)
  }

  function cambiarTipo(nuevo: FiltroTipo) {
    setTipo(nuevo)
    guardar("tipo", nuevo)
    // El recurso elegido puede no pertenecer al nuevo tipo.
    setRecursoId("todos")
  }

  function cambiarEstado(nuevo: FiltroEstado) {
    setFiltroEstado(nuevo)
    guardar("estado", nuevo)
  }

  function abrirDia(dia: DiaCivil) {
    setAncla(dia)
    cambiarVista("dia")
  }

  // La reserva nueva puede caer fuera del periodo visible: se lleva el
  // calendario a su día para que el modal de detalle pueda abrirla. Si el
  // periodo cambia, el efecto de `cargar` la trae con el rango nuevo.
  async function reservaCreada(r: ReservaCreada) {
    const primera = r.reservas[0]
    if (primera?.inicio) setAncla(diaDe(new Date(primera.inicio)))
    await cargar()
    setIdSeleccionado(primera?.id ?? null)
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold tracking-tight">
          Calendario de reservas
        </h1>
        <div className="flex flex-wrap items-center gap-2">
          <Select value={tipo} onValueChange={(v) => cambiarTipo(v as FiltroTipo)}>
            <SelectTrigger size="sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="todos">Activos y lugares</SelectItem>
              <SelectItem value="equipo">Solo activos</SelectItem>
              <SelectItem value="espacio">Solo lugares</SelectItem>
            </SelectContent>
          </Select>

          <Select value={recursoId} onValueChange={setRecursoId}>
            <SelectTrigger size="sm" className="max-w-52">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="todos">Todos los recursos</SelectItem>
              {recursos.map((r) => (
                <SelectItem key={r.id} value={String(r.id)}>
                  {r.nombre}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select value={usuarioCodigo} onValueChange={setUsuarioCodigo}>
            <SelectTrigger size="sm" className="max-w-60">
              <SelectValue placeholder="Todos los usuarios" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="todos">Todos los usuarios</SelectItem>
              {usuarios.map(([codigo, nombre]) => (
                <SelectItem key={codigo} value={codigo}>
                  {codigo === USUARIO_SIN_CODIGO ? "Sin código Uninorte" : `${codigo} · ${nombre}`}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select value={filtroEstado} onValueChange={(v) => cambiarEstado(v as FiltroEstado)}>
            <SelectTrigger size="sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {ESTADOS.map((e) => (
                <SelectItem key={e.valor} value={e.valor}>
                  {e.etiqueta}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Button size="sm" onClick={() => setCreando(true)}>
            <Plus />
            Nueva reserva
          </Button>
          <Button size="sm" variant="outline" onClick={() => cargar()}>
            Refrescar
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1">
          <Button
            size="icon-sm"
            variant="outline"
            aria-label="Periodo anterior"
            onClick={() => setAncla((a) => desplazar(vista, a, -1))}
          >
            <ChevronLeft />
          </Button>
          <Button size="sm" variant="outline" onClick={() => setAncla(hoy())}>
            Hoy
          </Button>
          <Button
            size="icon-sm"
            variant="outline"
            aria-label="Periodo siguiente"
            onClick={() => setAncla((a) => desplazar(vista, a, 1))}
          >
            <ChevronRight />
          </Button>
          <p className="ml-2 text-sm font-medium">{etiquetaPeriodo(vista, ancla)}</p>
        </div>

        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">
            {eventos.length} reserva{eventos.length === 1 ? "" : "s"}
            {filtradas.length !== eventos.length && ` · ${filtradas.length} activos`}
          </span>
          <Select value={vista} onValueChange={(v) => cambiarVista(v as Vista)}>
            <SelectTrigger size="sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="mes">Mes</SelectItem>
              <SelectItem value="semana">Semana</SelectItem>
              <SelectItem value="dia">Día</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      <LeyendaEstados />

      {error && (
        <p className="text-sm text-destructive-text">No se pudieron cargar las reservas: {error}</p>
      )}

      {cargando && !reservas.length ? (
        <Skeleton className="h-96 w-full" />
      ) : vista === "mes" ? (
        <VistaMes
          dias={dias}
          mesVisible={ancla.mes}
          eventos={eventos}
          onSeleccionar={(evento) => setIdSeleccionado(evento.reservas[0].id)}
          onAbrirDia={abrirDia}
        />
      ) : (
        <VistaTiempo
          dias={dias}
          eventos={eventos}
          onSeleccionar={(evento) => setIdSeleccionado(evento.reservas[0].id)}
        />
      )}

      <ReservaDetalle
        evento={seleccionado}
        recursos={activos}
        onClose={() => setIdSeleccionado(null)}
        onGuardado={cargar}
      />

      {creando && (
        <NuevaReservaDialog
          recursos={activos}
          onClose={() => setCreando(false)}
          onCreada={reservaCreada}
        />
      )}
    </div>
  )
}
