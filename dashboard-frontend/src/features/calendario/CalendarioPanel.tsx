import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { ChevronLeft, ChevronRight, Plus, Search } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
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
  type ReservaCreada,
  type Sesion,
} from "@/lib/api"

import {
  agruparEnEventos,
  codigoUsuario,
  FILTROS_ESTADO_COMUNES,
  desplazar,
  diaDe,
  diasDelPeriodo,
  etiquetaPeriodo,
  finDelDia,
  hoy,
  inicioDelDia,
  opcionesUsuarios,
  TIPOS_RECURSO,
  ubicarReservas,
  type DiaCivil,
  type Vista,
} from "./calendario-utils"
import { FiltroMultiple } from "./FiltroMultiple"
import { LeyendaEstados } from "./LeyendaEstados"
import { ReservaDetalle } from "./ReservaDetalle"
import { VistaMes } from "./VistaMes"
import { VistaTiempo } from "./VistaTiempo"

const PREFIJO_STORAGE = "dashboard-lacs-calendario-"
const REFRESCO_MS = 60000

// Los estados salen de FILTROS_ESTADO_COMUNES, igual que en ReservasPanel: es el mismo
// concepto de estado, solo que acá vive en un selector múltiple con búsqueda
// en vez de una barra de chips. "retrasada" no es un estado de línea (ver el
// mismo comentario en ReservasPanel): es un pseudo-filtro sobre
// evento.retrasada, derivado de estado == "entregada" && fin < ahora.
const ESTADOS: { valor: string; etiqueta: string }[] = [
  { valor: "pendiente", etiqueta: "Por confirmar" },
  ...FILTROS_ESTADO_COMUNES,
]
const ESTADOS_POR_DEFECTO = ESTADOS.map((e) => e.valor).filter(
  (v) => v !== "cancelada" && v !== "denegada"
)

function guardado<T extends string>(clave: string, valores: readonly T[], porDefecto: T): T {
  try {
    const valor = localStorage.getItem(PREFIJO_STORAGE + clave) as T | null
    if (valor && valores.includes(valor)) return valor
  } catch {
    // localStorage no disponible
  }
  return porDefecto
}

// Lee un array JSON y descarta lo que no esté en la lista permitida; si falla
// el parse o no es un array, devuelve el valor por defecto.
function guardadoLista(clave: string, permitidos: string[], porDefecto: string[]): string[] {
  try {
    const valor = JSON.parse(localStorage.getItem(PREFIJO_STORAGE + clave) ?? "null")
    if (Array.isArray(valor)) return valor.filter((v) => permitidos.includes(v))
  } catch {
    // localStorage no disponible o JSON inválido
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
  // En recursos y usuarios vacío = todos; en `tipos` y `estados` la selección
  // es explícita y vacío = no mostrar nada.
  const [tipos, setTipos] = useState<string[]>(() =>
    guardadoLista("tipos-marcados", TIPOS_RECURSO.map((t) => t.valor), TIPOS_RECURSO.map((t) => t.valor))
  )
  const [recursoIds, setRecursoIds] = useState<string[]>([])
  const [usuarioCodigos, setUsuarioCodigos] = useState<string[]>([])
  const [numero, setNumero] = useState("")
  const [estados, setEstados] = useState<string[]>(() =>
    guardadoLista("estados", ESTADOS.map((e) => e.valor), ESTADOS_POR_DEFECTO)
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
    const lista = activos.filter((r) => tipos.includes(r.tipo))
    return [...lista].sort((a, b) => a.nombre.localeCompare(b.nombre, "es"))
  }, [activos, tipos])

  const usuarios = useMemo(() => opcionesUsuarios(ubicadas, usuarioCodigos), [ubicadas, usuarioCodigos])

  const filtradas = useMemo(() => {
    // "#123" o "123"; vacío o no numérico = sin filtro.
    const buscado = Number(numero.trim().replace(/^#/, "")) || null
    return ubicadas.filter(({ reserva }) => {
      if (buscado !== null && reserva.grupo_id !== buscado) return false
      if (!tipos.includes(reserva.recurso_tipo ?? "")) return false
      if (recursoIds.length && !recursoIds.includes(String(reserva.recurso_id))) return false
      if (usuarioCodigos.length && !usuarioCodigos.includes(codigoUsuario(reserva))) return false
      // A diferencia de los demás estados, "retrasada" no compara contra
      // `reserva.estado`: es el campo derivado que manda el backend (ver
      // Sesion.retrasada / actas_service.esta_retrasada).
      return estados.includes(reserva.estado) || (estados.includes("retrasada") && reserva.retrasada)
    })
  }, [ubicadas, tipos, recursoIds, usuarioCodigos, estados, numero])

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

  function cambiarTipos(nuevos: string[]) {
    setTipos(nuevos)
    guardar("tipos-marcados", JSON.stringify(nuevos))
    // Los recursos elegidos pueden no pertenecer a los nuevos tipos.
    const permitidos = new Set(
      activos.filter((r) => nuevos.includes(r.tipo)).map((r) => String(r.id))
    )
    setRecursoIds((ids) => ids.filter((id) => permitidos.has(id)))
  }

  function cambiarEstados(nuevos: string[]) {
    setEstados(nuevos)
    guardar("estados", JSON.stringify(nuevos))
  }

  const mismoConjunto = (a: string[], b: string[]) =>
    a.length === b.length && a.every((v) => b.includes(v))
  const resumenEstados = mismoConjunto(estados, ESTADOS_POR_DEFECTO)
    ? "Sin canceladas ni denegadas"
    : estados.length === ESTADOS.length
      ? "Todos los estados"
      : !estados.length
        ? "Ningún estado"
        : estados.length === 1
          ? (ESTADOS.find((e) => e.valor === estados[0])?.etiqueta ?? estados[0])
          : `${estados.length} estados`

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
          <div className="relative w-32">
            <Search
              className="pointer-events-none absolute top-1/2 left-2 size-4 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <Input
              type="search"
              inputMode="numeric"
              value={numero}
              onChange={(e) => setNumero(e.target.value)}
              placeholder="N.º reserva"
              aria-label="Buscar por número de reserva"
              className="h-7 pl-7"
            />
          </div>

          <FiltroMultiple
            opciones={TIPOS_RECURSO}
            seleccion={tipos}
            onChange={cambiarTipos}
            textoTodos="Activos y lugares"
            resumen={tipos.length ? undefined : "Ningún tipo"}
          />

          <FiltroMultiple
            opciones={recursos.map((r) => ({ valor: String(r.id), etiqueta: r.nombre }))}
            seleccion={recursoIds}
            onChange={setRecursoIds}
            textoTodos="Todos los recursos"
            className="max-w-52"
          />

          <FiltroMultiple
            opciones={usuarios}
            seleccion={usuarioCodigos}
            onChange={setUsuarioCodigos}
            textoTodos="Todos los usuarios"
            className="max-w-60"
          />

          <FiltroMultiple
            opciones={ESTADOS}
            seleccion={estados}
            onChange={cambiarEstados}
            textoTodos="Todos los estados"
            resumen={resumenEstados}
          />

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
