import { useState, type FormEvent, type ReactNode } from "react"
import { Pencil, Plus, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  actualizarReserva,
  actualizarReservaGrupo,
  actualizarReservasBloque,
  eliminarReservaGrupo,
  type CambiosReserva,
  type EstadoReserva,
  type Recurso,
} from "@/lib/api"

import {
  aInputLocal,
  desdeInputLocal,
  estiloDe,
  etiquetaEvento,
  etiquetaNumeros,
  etiquetaReserva,
  fechaHora,
  type EventoCalendario,
} from "./calendario-utils"
import { IconoTipo } from "./IconoTipo"

const ESTADOS: EstadoReserva[] = ["confirmada", "completada", "cancelada", "no_show"]

function duracion(inicio: Date, fin: Date) {
  const minutos = Math.round((fin.getTime() - inicio.getTime()) / 60000)
  const dias = Math.floor(minutos / 1440)
  const horas = Math.floor((minutos % 1440) / 60)
  const resto = minutos % 60
  const partes = []
  if (dias) partes.push(`${dias} d`)
  if (horas) partes.push(`${horas} h`)
  if (resto || !partes.length) partes.push(`${resto} min`)
  return partes.join(" ")
}

function Dato({ etiqueta, children }: { etiqueta: string; children: ReactNode }) {
  return (
    <div className="space-y-0.5">
      <p className="text-xs uppercase tracking-wide text-muted-foreground">{etiqueta}</p>
      <div className="text-sm">{children}</div>
    </div>
  )
}

function Detalle({ evento, recursos }: { evento: EventoCalendario; recursos: Recurso[] }) {
  const lineas = evento.reservas.map((reserva) => ({
    reserva,
    padreId: recursos.find((recurso) => recurso.id === reserva.recurso_id)?.padre_id ?? null,
  }))
  const ids = new Set(lineas.map(({ reserva }) => reserva.recurso_id))
  const raices = lineas.filter(({ padreId }) => padreId === null || !ids.has(padreId))

  function hijosDe(recursoId: number) {
    return lineas.filter(({ padreId }) => padreId === recursoId)
  }

  function renderLinea(
    linea: (typeof lineas)[number],
    nivel = 0
  ): ReactNode {
    const { reserva } = linea
    return (
      <li key={reserva.id}>
        <div
          className="flex items-center justify-between gap-2"
          style={{ paddingLeft: `${nivel * 1.25}rem` }}
        >
          <span className="flex min-w-0 items-center gap-1.5">
            <IconoTipo
              tipo={reserva.recurso_tipo}
              className="size-3.5 shrink-0 text-muted-foreground"
            />
            <span className="truncate">{etiquetaReserva(reserva)}</span>
          </span>
          {evento.estadosMixtos && <Badge variant={estiloDe(reserva.estado).badge}>{reserva.estado}</Badge>}
        </div>
        {hijosDe(reserva.recurso_id).map((hijo) => renderLinea(hijo, nivel + 1))}
      </li>
    )
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <Badge variant={estiloDe(evento.estado).badge}>
          {evento.estadosMixtos ? "estados mixtos" : evento.estado}
        </Badge>
        <Badge variant="outline">{evento.reservas[0].canal_origen}</Badge>
        <Badge variant="outline">{evento.usuarioRol}</Badge>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Dato etiqueta="Inicio">{fechaHora(evento.inicio)}</Dato>
        <Dato etiqueta="Fin">{fechaHora(evento.fin)}</Dato>
        <Dato etiqueta="Duración">{duracion(evento.inicio, evento.fin)}</Dato>
        <Dato etiqueta="Usuario">
          <p>{evento.usuarioNombre}</p>
          <p className="text-muted-foreground">{evento.usuarioTelefono}</p>
        </Dato>
      </div>

      <Dato
        etiqueta={
          evento.reservas.length === 1
            ? "Activo reservado"
            : `Activos reservados (${evento.reservas.length})`
        }
      >
        <ul className="space-y-1">
          {raices.map((linea) => renderLinea(linea))}
        </ul>
      </Dato>

      {evento.reservas[0].creado_en && (
        <Dato etiqueta="Creada">{fechaHora(new Date(evento.reservas[0].creado_en))}</Dato>
      )}
    </div>
  )
}

function FormularioEdicion({
  evento,
  recursos,
  onCancelar,
  onGuardado,
  onEliminar,
}: {
  evento: EventoCalendario
  recursos: Recurso[]
  onCancelar: () => void
  onGuardado: () => void
  onEliminar: () => void
}) {
  const unaLinea = evento.reservas.length === 1
  const primera = evento.reservas[0]

  const [recursoIds, setRecursoIds] = useState(() => evento.reservas.map((r) => r.recurso_id))
  const [inicio, setInicio] = useState(aInputLocal(evento.inicio))
  const [fin, setFin] = useState(aInputLocal(evento.fin))
  const [estado, setEstado] = useState<EstadoReserva>(evento.estado)
  const [guardando, setGuardando] = useState(false)
  const [agregandoActivo, setAgregandoActivo] = useState(false)
  const [activosParaAgregar, setActivosParaAgregar] = useState<number[]>([])

  const actuales = evento.reservas.map(
    (r) => recursos.find((recurso) => recurso.id === r.recurso_id) ??
      ({
        id: r.recurso_id,
        nombre: etiquetaReserva(r),
        tipo: r.recurso_tipo ?? "equipo",
        padre_id: null,
      } as Recurso)
  )
  const idsActuales = new Set(actuales.map((recurso) => recurso.id))
  const opciones = [...actuales, ...recursos.filter((recurso) => !idsActuales.has(recurso.id))]
  const seleccionados = opciones.filter((recurso) => recursoIds.includes(recurso.id))
  const seleccionadosIds = new Set(seleccionados.map((recurso) => recurso.id))
  const disponiblesParaAgregar = opciones.filter((recurso) => !seleccionadosIds.has(recurso.id))
  const disponiblesIds = new Set(disponiblesParaAgregar.map((recurso) => recurso.id))
  const raices = seleccionados.filter(
    (recurso) => recurso.padre_id === null || !seleccionadosIds.has(recurso.padre_id)
  )
  const raicesDisponibles = disponiblesParaAgregar.filter(
    (recurso) => recurso.padre_id === null || !disponiblesIds.has(recurso.padre_id)
  )

  function descendientesDe(recursoId: number) {
    return seleccionados.filter((recurso) => recurso.padre_id === recursoId)
  }

  function descendientesDisponiblesDe(recursoId: number) {
    return disponiblesParaAgregar.filter((recurso) => recurso.padre_id === recursoId)
  }

  function renderActivo(recurso: Recurso, nivel = 0): ReactNode {
    return (
      <div key={recurso.id}>
        <div
          className="flex items-center gap-2 py-1"
          style={{ paddingLeft: `${nivel * 1.25}rem` }}
        >
          <IconoTipo tipo={recurso.tipo} className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="min-w-0 flex-1 truncate text-sm">{recurso.nombre}</span>
          <Button
            type="button"
            size="icon-sm"
            variant="ghost"
            aria-label={`Quitar ${recurso.nombre}`}
            title={`Quitar ${recurso.nombre}`}
            onClick={() => alternarRecurso(recurso.id, false)}
            disabled={guardando || recursoIds.length === 1}
          >
            <Trash2 />
          </Button>
        </div>
        {descendientesDe(recurso.id).map((hijo) => renderActivo(hijo, nivel + 1))}
      </div>
    )
  }

  function renderActivoParaAgregar(recurso: Recurso, nivel = 0): ReactNode {
    return (
      <div key={recurso.id}>
        <label
          htmlFor={`agregar-recurso-${recurso.id}`}
          className="flex items-center gap-2 py-1 text-sm"
          style={{ paddingLeft: `${nivel * 1.25}rem` }}
        >
          <Checkbox
            id={`agregar-recurso-${recurso.id}`}
            checked={activosParaAgregar.includes(recurso.id)}
            onCheckedChange={(marcado) =>
              setActivosParaAgregar((anteriores) =>
                marcado === true
                  ? anteriores.includes(recurso.id)
                    ? anteriores
                    : [...anteriores, recurso.id]
                  : anteriores.filter((id) => id !== recurso.id)
              )
            }
            disabled={guardando}
          />
          <IconoTipo tipo={recurso.tipo} className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="truncate">{recurso.nombre}</span>
        </label>
        {descendientesDisponiblesDe(recurso.id).map((hijo) =>
          renderActivoParaAgregar(hijo, nivel + 1)
        )}
      </div>
    )
  }

  function alternarRecurso(id: number, marcado: boolean) {
    setRecursoIds((anteriores) => {
      if (marcado) return anteriores.includes(id) ? anteriores : [...anteriores, id]
      return anteriores.filter((recursoId) => recursoId !== id)
    })
  }

  function agregarActivosSeleccionados() {
    setRecursoIds((anteriores) => [...new Set([...anteriores, ...activosParaAgregar])])
    setActivosParaAgregar([])
  }

  async function guardar(evt: FormEvent) {
    evt.preventDefault()

    const nuevoInicio = desdeInputLocal(inicio)
    const nuevoFin = desdeInputLocal(fin)
    if (!nuevoInicio || !nuevoFin) {
      toast.error("Revisa las fechas de inicio y fin.")
      return
    }
    if (nuevoFin <= nuevoInicio) {
      toast.error("La hora de fin debe ser posterior a la de inicio.")
      return
    }

    // Solo se envía lo que cambió: así una edición de estado no reescribe el
    // horario y no choca con el EXCLUDE de solapamiento de Postgres.
    const cambios: CambiosReserva & { recurso_ids?: number[] } = {}
    if (nuevoInicio.getTime() !== evento.inicio.getTime()) cambios.inicio = nuevoInicio.toISOString()
    if (nuevoFin.getTime() !== evento.fin.getTime()) cambios.fin = nuevoFin.toISOString()
    if (estado !== evento.estado || evento.estadosMixtos) cambios.estado = estado
    const idsOriginales = evento.reservas.map((r) => r.recurso_id).sort((a, b) => a - b)
    const idsSeleccionados = [...recursoIds].sort((a, b) => a - b)
    const cambiaronActivos =
      idsOriginales.length !== idsSeleccionados.length ||
      idsOriginales.some((id, indice) => id !== idsSeleccionados[indice])
    if (cambiaronActivos && recursoIds.length === 0) {
      toast.error("La reserva debe conservar al menos un activo.")
      return
    }
    if (cambiaronActivos) cambios.recurso_ids = recursoIds

    if (Object.keys(cambios).length === 0) {
      onCancelar()
      return
    }

    setGuardando(true)
    try {
      if (evento.numeros.length === 1) {
        await actualizarReservaGrupo(evento.numeros[0], cambios)
      } else if (unaLinea) {
        const { recurso_ids: _, ...cambiosLinea } = cambios
        if (Object.keys(cambiosLinea).length > 0) {
          await actualizarReserva(primera.id, cambiosLinea)
        }
      } else {
        const { recurso_ids: _, ...cambiosBloque } = cambios
        // Este caso solo aplica a datos históricos sin un grupo único.
        // Todas las líneas se mueven juntas o ninguna.
        if (Object.keys(cambiosBloque).length > 0) {
          await actualizarReservasBloque(
            evento.reservas.map((r) => r.id),
            cambiosBloque
          )
        }
      }
      toast.success("Reserva actualizada.")
      onGuardado()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setGuardando(false)
    }
  }

  return (
    <form onSubmit={guardar} className="space-y-3">
      <fieldset className="space-y-1.5">
        <div className="flex items-center justify-between gap-2">
          <legend className="text-sm font-medium">Activos reservados</legend>
          <Button
            type="button"
            size="icon-sm"
            variant="outline"
            aria-label="Agregar activo a la reserva"
            title="Agregar activo a la reserva"
            onClick={() => setAgregandoActivo((abierto) => !abierto)}
            disabled={guardando || disponiblesParaAgregar.length === 0}
          >
            <Plus />
          </Button>
        </div>
        <div className="max-h-40 overflow-y-auto rounded-md border p-3">
          {raices.map((recurso) => renderActivo(recurso))}
        </div>
        {agregandoActivo && (
          <div className="space-y-2 rounded-md border p-3">
            <p className="text-xs font-medium text-muted-foreground">Activos disponibles para agregar</p>
            <div className="max-h-48 overflow-y-auto">
              {raicesDisponibles.map((recurso) => renderActivoParaAgregar(recurso))}
            </div>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={agregarActivosSeleccionados}
              disabled={guardando || activosParaAgregar.length === 0}
            >
              <Plus />
              Agregar seleccionados ({activosParaAgregar.length})
            </Button>
          </div>
        )}
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <span>{recursoIds.length} activo{recursoIds.length === 1 ? "" : "s"} en la reserva.</span>
          {agregandoActivo && <span>Marca uno o varios para agregarlos.</span>}
        </div>
      </fieldset>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="reserva-inicio">Inicio</Label>
          <Input
            id="reserva-inicio"
            type="datetime-local"
            value={inicio}
            onChange={(e) => setInicio(e.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="reserva-fin">Fin</Label>
          <Input
            id="reserva-fin"
            type="datetime-local"
            value={fin}
            onChange={(e) => setFin(e.target.value)}
          />
        </div>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="reserva-estado">Estado</Label>
        <Select value={estado} onValueChange={(v) => setEstado(v as EstadoReserva)}>
          <SelectTrigger id="reserva-estado" className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {ESTADOS.map((e) => (
              <SelectItem key={e} value={e}>
                {e}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <p className="text-xs text-muted-foreground">
        Horarios en hora local de la CPA (America/Bogota). Dos reservas confirmadas del mismo activo no pueden cruzarse.
      </p>

      <DialogFooter>
        <Button type="button" variant="destructive" onClick={onEliminar} disabled={guardando}>
          <Trash2 />
          Eliminar reserva
        </Button>
        <Button type="button" variant="outline" onClick={onCancelar} disabled={guardando}>
          Cancelar
        </Button>
        <Button type="submit" disabled={guardando}>
          {guardando ? "Guardando…" : "Guardar cambios"}
        </Button>
      </DialogFooter>
    </form>
  )
}

/** Se monta por evento (key): abrir otro siempre empieza en modo detalle. */
function Contenido({
  evento,
  recursos,
  onGuardado,
  onClose,
}: {
  evento: EventoCalendario
  recursos: Recurso[]
  onGuardado: () => void
  onClose: () => void
}) {
  const [editando, setEditando] = useState(false)
  const [confirmandoEliminacion, setConfirmandoEliminacion] = useState(false)
  const [eliminando, setEliminando] = useState(false)

  function solicitarEliminacion() {
    if (evento.numeros.length !== 1) {
      toast.error("No se pudo identificar una reserva única para eliminar.")
      return
    }
    setConfirmandoEliminacion(true)
  }

  async function confirmarEliminacion() {
    setEliminando(true)
    try {
      await eliminarReservaGrupo(evento.numeros[0])
      setConfirmandoEliminacion(false)
      toast.success("Reserva eliminada.")
      onClose()
      onGuardado()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setEliminando(false)
    }
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle className="flex items-center gap-2">
          <IconoTipo tipo={evento.tipo} className="size-4 shrink-0" />
          {etiquetaEvento(evento)}
        </DialogTitle>
        <DialogDescription>
          Reserva {etiquetaNumeros(evento)} · {evento.reservas.length}{" "}
          {evento.reservas.length === 1 ? "activo" : "activos"}
          {editando && " · editando"}
        </DialogDescription>
      </DialogHeader>

      {editando ? (
        <FormularioEdicion
          evento={evento}
          recursos={recursos}
          onCancelar={() => setEditando(false)}
          onGuardado={() => {
            setEditando(false)
            onGuardado()
          }}
          onEliminar={solicitarEliminacion}
        />
      ) : (
        <>
          <Detalle evento={evento} recursos={recursos} />
          <DialogFooter>
            <Button size="sm" variant="destructive" onClick={solicitarEliminacion}>
              <Trash2 />
              Eliminar reserva
            </Button>
            <Button size="sm" variant="outline" onClick={() => setEditando(true)}>
              <Pencil />
              Editar reserva
            </Button>
          </DialogFooter>
        </>
      )}

      <Dialog open={confirmandoEliminacion} onOpenChange={setConfirmandoEliminacion}>
        <DialogContent showCloseButton={false} className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>¿Eliminar reserva {etiquetaNumeros(evento)}?</DialogTitle>
            <DialogDescription>
              Se eliminarán todos los activos de esta reserva. Esta acción no se puede deshacer.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setConfirmandoEliminacion(false)}
              disabled={eliminando}
            >
              Cancelar
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={confirmarEliminacion}
              disabled={eliminando}
            >
              <Trash2 />
              {eliminando ? "Eliminando…" : "Eliminar reserva"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

export function ReservaDetalle({
  evento,
  recursos,
  onClose,
  onGuardado,
}: {
  evento: EventoCalendario | null
  recursos: Recurso[]
  onClose: () => void
  onGuardado: () => void
}) {
  return (
    <Dialog open={evento !== null} onOpenChange={(abierto) => !abierto && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        {evento && (
          <Contenido
            key={evento.clave}
            evento={evento}
            recursos={recursos}
            onClose={onClose}
            onGuardado={onGuardado}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}
