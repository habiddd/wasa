import { useEffect, useMemo, useState } from "react"
import { Plus, Search } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
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
  ZONA_LACS,
  agruparEnEventos,
  codigoUsuario,
  FILTROS_ESTADO_COMUNES,
  diaDe,
  etiquetaEvento,
  horaCompacta,
  mismoDia,
  opcionesUsuarios,
  TIPOS_RECURSO,
  ubicarReservas,
  type EventoCalendario,
} from "@/features/calendario/calendario-utils"
import { EstadoEventoBadge } from "@/features/calendario/EstadoBadge"
import { FiltroMultiple } from "@/features/calendario/FiltroMultiple"
import { IconoTipo } from "@/features/calendario/IconoTipo"
import { ReservaDetalle } from "@/features/calendario/ReservaDetalle"
import { type ReservaCreada, type VentanaReservas } from "@/lib/api"

import { BotonActaPdf } from "./BotonActaPdf"
import { NuevaReservaDialog } from "./NuevaReservaDialog"
import { useReservasVentana } from "./use-reservas-ventana"

const ESTADOS_STORAGE_KEY = "dashboard-lacs-reservas-estados"

const FILTROS: { valor: string; etiqueta: string }[] = [
  { valor: "por_confirmar", etiqueta: "Por confirmar" },
  ...FILTROS_ESTADO_COMUNES,
]

// Igual que en el calendario, vacío = no mostrar nada; por defecto, todos.
function estadosIniciales(): string[] {
  try {
    const guardado = JSON.parse(localStorage.getItem(ESTADOS_STORAGE_KEY) ?? "null")
    if (Array.isArray(guardado)) return guardado.filter((v) => FILTROS.some((f) => f.valor === v))
  } catch {
    // localStorage no disponible o JSON inválido
  }
  return FILTROS.map((f) => f.valor)
}

// "por_confirmar" no es un estado de línea: junta las reservas que tienen
// algún activo pendiente de la LACS (parciales o pendientes del todo).
// "retrasada" tampoco: es el mismo pseudo-filtro, pero sobre
// evento.retrasada (derivado de estado == "entregada" && fin < ahora, ver
// calendario-utils) -- no hay una columna "retrasada" que filtrar en la BD.
// ¿El evento entra en la opción de estado `valor`? Basta con una de sus líneas.
function cumpleEstado(evento: EventoCalendario, valor: string) {
  if (valor === "por_confirmar") return evento.porConfirmar
  if (valor === "retrasada") return evento.retrasada
  return evento.reservas.some((r) => r.estado === valor)
}

const SEGURO_STORAGE_KEY = "dashboard-lacs-reservas-seguro"

function seguroInicial(): boolean {
  try {
    return localStorage.getItem(SEGURO_STORAGE_KEY) === "1"
  } catch {
    // localStorage no disponible
  }
  return false
}

const VENTANA_STORAGE_KEY = "dashboard-lacs-reservas-ventana"

const VENTANAS: { valor: VentanaReservas; etiqueta: string }[] = [
  { valor: "24h", etiqueta: "Últimas 24 horas" },
  { valor: "7d", etiqueta: "Última semana" },
  { valor: "30d", etiqueta: "Último mes" },
  { valor: "1a", etiqueta: "Último año" },
  { valor: "todas", etiqueta: "Todas" },
]

// "30d" por defecto: cubre el uso normal sin que la vista inicial arrastre el
// histórico entero (ver useReservasVentana y docs/ARQUITECTURA.md).
function ventanaInicial(): VentanaReservas {
  try {
    const guardado = localStorage.getItem(VENTANA_STORAGE_KEY)
    if (guardado && VENTANAS.some((v) => v.valor === guardado)) return guardado as VentanaReservas
  } catch {
    // localStorage no disponible
  }
  return "30d"
}

// Debe coincidir con MAX_RESERVAS_VENTANA en dashboard.py: es el tope de
// seguridad de "todas", no un recorte esperado (ver useReservasVentana).
const TOPE_RESERVAS_VENTANA = 5000

// N.º de reserva de mayor a menor: las creadas más recientemente arriba. Las
// legadas sin número van al final, por inicio.
function porNumeroDesc(a: EventoCalendario, b: EventoCalendario) {
  return (b.numeros[0] ?? 0) - (a.numeros[0] ?? 0) || b.inicio.getTime() - a.inicio.getTime()
}

// --- Formato corto -------------------------------------------------------
// La tabla tiene que caber en el ancho que haya, así que usa fechas cortas
// ("22 sept 2026", "9am") en vez de las largas del modal.

const FECHA = new Intl.DateTimeFormat("es-CO", {
  timeZone: ZONA_LACS,
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

// El acta es del grupo completo: con varios números (caso legado, reservas
// de antes del carrito) se usa el primero, igual que el resto de acciones de
// grupo del panel (confirmar, eliminar).
function grupoIdDe(evento: EventoCalendario): number | null {
  return evento.numeros[0] ?? null
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

/** Minúsculas y sin tildes: "Pérez" se encuentra escribiendo "perez". */
function normalizar(texto: string) {
  return texto.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase()
}

/** Todo lo que el mostrador puede tener a mano para encontrar una reserva. */
function textoBuscable(evento: EventoCalendario, placaDe: Map<number, string | null>) {
  const primera = evento.reservas[0]
  return normalizar(
    [
      evento.usuarioNombre,
      evento.usuarioTelefono,
      primera.usuario_codigo_uninorte,
      ...evento.numeros.map((n) => `${n} #${n}`),
      ...evento.reservas.flatMap((r) => [r.recurso_nombre, placaDe.get(r.recurso_id)]),
    ]
      .filter(Boolean)
      .join(" ")
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
  // Refresca el listado en cuanto se registra un acta (ver BotonActaPdf):
  // el estado de la reserva cambió y no hay por qué esperar al sondeo.
  onActaRegistrada: () => void
}

// --- Vistas ----------------------------------------------------------------
// Se eligen por el ancho del panel (container queries), no de la ventana:
// así también se adaptan cuando el sidebar se abre o se retrae.

/** Pantallas angostas: una tarjeta por reserva, sin columnas. */
function ListaReservas({ eventos, onSeleccionar, onActaRegistrada }: PropsVista) {
  return (
    <ul className="divide-y rounded-md border">
      {eventos.map((evento) => {
        const [dia, horas] = horario(evento)
        return (
          // La tarjeta y el botón del acta son hermanos, no anidados: un
          // <button> dentro de otro <button> es HTML inválido (y React lo
          // advierte). El botón principal se queda con casi todo el ancho.
          <li key={evento.clave} className="flex items-center pr-2 hover:bg-muted/50">
            <button
              type="button"
              onClick={() => onSeleccionar(evento)}
              className="w-full min-w-0 flex-1 space-y-1 px-3 py-2.5 text-left text-sm outline-none focus-visible:bg-muted/50"
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
            <BotonActaPdf
              grupoId={grupoIdDe(evento)}
              reservas={evento.reservas}
              usuarioNombre={evento.usuarioNombre}
              fin={evento.fin}
              onRegistrada={onActaRegistrada}
            />
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
function TablaReservas({ eventos, onSeleccionar, onActaRegistrada }: PropsVista) {
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
            <TableHead className="w-10">
              <span className="sr-only">Acta</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {eventos.map((evento) => {
            const [dia, horas] = horario(evento)
            return (
              <TableRow
                key={evento.clave}
                tabIndex={0}
                className="cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
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
                <TableCell>
                  <BotonActaPdf
                    grupoId={grupoIdDe(evento)}
                    reservas={evento.reservas}
                    usuarioNombre={evento.usuarioNombre}
                    fin={evento.fin}
                    onRegistrada={onActaRegistrada}
                  />
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
  const [ventana, setVentana] = useState<VentanaReservas>(ventanaInicial)
  const { reservas, cargando, refrescando, error, truncado, reload } = useReservasVentana(ventana)
  const [estados, setEstados] = useState<string[]>(estadosIniciales)
  const [soloSeguro, setSoloSeguro] = useState<boolean>(seguroInicial)
  const [busqueda, setBusqueda] = useState("")
  // Igual que en el calendario: en recursos y usuarios vacío = todos; en
  // `tipos` la selección es explícita y vacío = no mostrar nada.
  const [tipos, setTipos] = useState<string[]>(() => TIPOS_RECURSO.map((t) => t.valor))
  const [recursoIds, setRecursoIds] = useState<string[]>([])
  const [usuarioCodigos, setUsuarioCodigos] = useState<string[]>([])
  const [idSeleccionado, setIdSeleccionado] = useState<number | null>(null)
  const [creando, setCreando] = useState(false)

  // El modal de edición necesita el catálogo para elegir activos; el
  // contexto no se carga solo (ver CalendarioPanel).
  useEffect(() => {
    recargarActivos()
  }, [recargarActivos])

  const ubicadas = useMemo(() => ubicarReservas(reservas), [reservas])

  // A diferencia del calendario, se muestra la reserva completa: entra en un
  // estado si alguna de sus líneas lo tiene, y el modal ve todos sus activos.
  const todos = useMemo(() => agruparEnEventos(ubicadas).sort(porNumeroDesc), [ubicadas])

  const placaDe = useMemo(() => new Map(activos.map((a) => [a.id, a.placa])), [activos])

  const recursos = useMemo(
    () =>
      activos
        .filter((r) => tipos.includes(r.tipo))
        .sort((a, b) => a.nombre.localeCompare(b.nombre, "es"))
        .map((r) => ({ valor: String(r.id), etiqueta: r.nombre })),
    [activos, tipos]
  )
  const usuarios = useMemo(() => opcionesUsuarios(ubicadas, usuarioCodigos), [ubicadas, usuarioCodigos])

  // La búsqueda, "Necesita seguro" y los filtros de tipo, recurso y usuario
  // combinan con AND: se recalculan aparte para no rehacer el agrupamiento.
  // A diferencia del calendario, se filtra la reserva completa: basta con
  // que una de sus líneas coincida.
  const filtrados = useMemo(() => {
    const consulta = normalizar(busqueda.trim())
    const todosLosTipos = tipos.length === TIPOS_RECURSO.length
    if (!soloSeguro && !consulta && todosLosTipos && !recursoIds.length && !usuarioCodigos.length) {
      return todos
    }
    return todos.filter(
      (e) =>
        (!soloSeguro || necesitaSeguro(e)) &&
        (!consulta || textoBuscable(e, placaDe).includes(consulta)) &&
        e.reservas.some(
          (r) =>
            tipos.includes(r.recurso_tipo ?? "") &&
            (!recursoIds.length || recursoIds.includes(String(r.recurso_id))) &&
            (!usuarioCodigos.length || usuarioCodigos.includes(codigoUsuario(r)))
        )
    )
  }, [todos, soloSeguro, busqueda, placaDe, tipos, recursoIds, usuarioCodigos])

  const cantidades = useMemo(
    () => new Map(FILTROS.map(({ valor }) => [valor, filtrados.filter((e) => cumpleEstado(e, valor)).length])),
    [filtrados]
  )

  const soloPorConfirmar = estados.length === 1 && estados[0] === "por_confirmar"

  const eventos = useMemo(
    () => filtrados.filter((e) => estados.some((v) => cumpleEstado(e, v))),
    [filtrados, estados]
  )

  const pendientes = cantidades.get("por_confirmar") ?? 0

  const resumenEstados =
    estados.length === FILTROS.length
      ? "Todos los estados"
      : !estados.length
        ? "Ningún estado"
        : estados.length === 1
          ? (FILTROS.find((f) => f.valor === estados[0])?.etiqueta ?? estados[0])
          : `${estados.length} estados`

  // El modal se alimenta de la reserva completa, no de la filtrada: así
  // sigue abierto si una edición cambia el estado y la saca del filtro, y el
  // formulario no toma por activos de la reserva solo las líneas visibles.
  const seleccionado = useMemo(
    () => todos.find((e) => e.reservas.some((r) => r.id === idSeleccionado)) ?? null,
    [todos, idSeleccionado]
  )

  function cambiarEstados(nuevos: string[]) {
    setEstados(nuevos)
    try {
      localStorage.setItem(ESTADOS_STORAGE_KEY, JSON.stringify(nuevos))
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

  function cambiarTipos(nuevos: string[]) {
    setTipos(nuevos)
    // Los recursos elegidos pueden no pertenecer a los nuevos tipos.
    const permitidos = new Set(
      activos.filter((r) => nuevos.includes(r.tipo)).map((r) => String(r.id))
    )
    setRecursoIds((ids) => ids.filter((id) => permitidos.has(id)))
  }

  function cambiarVentana(nuevo: VentanaReservas) {
    setVentana(nuevo)
    try {
      localStorage.setItem(VENTANA_STORAGE_KEY, nuevo)
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
        <h1 className="text-xl font-semibold tracking-tight">
          Reservas
        </h1>
        <div className="flex gap-2">
          <Button size="sm" onClick={() => setCreando(true)}>
            <Plus />
            Nueva reserva
          </Button>
          <Button size="sm" variant="outline" onClick={() => reload()}>
            Refrescar
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="relative w-full sm:w-72">
          <Search
            className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            type="search"
            value={busqueda}
            onChange={(e) => setBusqueda(e.target.value)}
            placeholder="Estudiante, N.º de reserva o placa"
            aria-label="Buscar reservas por estudiante, número de reserva, activo o placa"
            className="h-8 pl-8"
          />
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <FiltroMultiple
            opciones={TIPOS_RECURSO}
            seleccion={tipos}
            onChange={cambiarTipos}
            textoTodos="Activos y lugares"
            resumen={tipos.length ? undefined : "Ningún tipo"}
          />
          <FiltroMultiple
            opciones={recursos}
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
            opciones={FILTROS.map(({ valor, etiqueta }) => ({
              valor,
              etiqueta: `${etiqueta} (${cargando ? "–" : (cantidades.get(valor) ?? 0)})`,
            }))}
            seleccion={estados}
            onChange={cambiarEstados}
            textoTodos="Todos los estados"
            resumen={`${resumenEstados} · ${cargando ? "–" : eventos.length}`}
          />
        </div>


        {/* Recorta en el servidor (ver useReservasVentana); los contadores de
            arriba pasan a contar solo dentro de esta ventana. */}
        <Select value={ventana} onValueChange={(v) => cambiarVentana(v as VentanaReservas)}>
          <SelectTrigger size="sm" className="min-w-40" aria-label="Filtrar por periodo">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {VENTANAS.map(({ valor, etiqueta }) => (
              <SelectItem key={valor} value={valor}>
                {etiqueta}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {/* Si no se está viendo solo "por confirmar", que las pendientes no
            se pierdan de vista igual. */}
        {!soloPorConfirmar && pendientes > 0 && (
          <p className="text-xs text-warning-text">
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

        {/* Sutil a propósito: nunca reemplaza la tabla por skeletons si ya
            hay algo que mostrar (ver useReservasVentana, `refrescando`). */}
        {refrescando && <p className="text-xs text-muted-foreground">Actualizando…</p>}
      </div>

      {truncado && (
        <p className="text-xs text-warning-text">
          Mostrando las {TOPE_RESERVAS_VENTANA} reservas más recientes; hay más histórico.
        </p>
      )}

      {cargando && (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      )}

      {error && reservas.length === 0 && (
        <p className="text-sm text-destructive-text">No se pudieron cargar las reservas: {error}</p>
      )}

      {!cargando && !(error && reservas.length === 0) && eventos.length === 0 && (
        busqueda.trim() ? (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md border p-4 text-sm text-muted-foreground">
            <p>
              Ninguna reserva coincide con «{busqueda.trim()}»
              {ventana !== "todas" && " en este periodo; prueba con un periodo más amplio"}.
            </p>
            <Button size="sm" variant="outline" onClick={() => setBusqueda("")}>
              Limpiar búsqueda
            </Button>
          </div>
        ) : (
          <p className="rounded-md border p-4 text-sm text-muted-foreground">
            {soloPorConfirmar && !soloSeguro
              ? "No hay reservas pendientes de confirmación."
              : soloSeguro
                ? "No hay reservas que requieran seguro para este filtro."
                : "No hay reservas para este filtro."}
          </p>
        )
      )}

      {!cargando && !(error && reservas.length === 0) && eventos.length > 0 && (
        <>
          {soloPorConfirmar && (
            <p className="text-sm text-muted-foreground">
              Abre una reserva para confirmar o denegar cada activo pendiente. El estudiante
              recibe un WhatsApp con cada decisión.
            </p>
          )}
          <div className="@2xl:hidden">
            <ListaReservas eventos={eventos} onSeleccionar={seleccionar} onActaRegistrada={reload} />
          </div>
          <div className="hidden @2xl:block">
            <TablaReservas eventos={eventos} onSeleccionar={seleccionar} onActaRegistrada={reload} />
          </div>
        </>
      )}

      <p className="text-xs text-muted-foreground">
        Horarios en hora local de la LACS (America/Bogota). Haz clic en una reserva para ver el
        detalle o editarla, o usa «Nueva reserva» para crear una desde acá.
      </p>

      <ReservaDetalle
        evento={seleccionado}
        recursos={activos}
        onClose={() => setIdSeleccionado(null)}
        onGuardado={reload}
      />

      {creando && (
        <NuevaReservaDialog
          recursos={activos}
          onClose={() => setCreando(false)}
          onCreada={async (r: ReservaCreada) => {
            await reload()
            setIdSeleccionado(r.reservas[0]?.id ?? null)
          }}
        />
      )}
    </div>
  )
}
