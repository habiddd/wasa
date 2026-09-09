import { useState, type FormEvent, type ReactNode } from "react"
import { Pencil } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
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
  actualizarReservasBloque,
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

function Detalle({ evento }: { evento: EventoCalendario }) {
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
          {evento.reservas.map((r) => (
            <li key={r.id} className="flex items-center justify-between gap-2">
              <span className="flex min-w-0 items-center gap-1.5">
                <IconoTipo
                  tipo={r.recurso_tipo}
                  className="size-3.5 shrink-0 text-muted-foreground"
                />
                <span className="truncate">{etiquetaReserva(r)}</span>
              </span>
              {evento.estadosMixtos && <Badge variant={estiloDe(r.estado).badge}>{r.estado}</Badge>}
            </li>
          ))}
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
}: {
  evento: EventoCalendario
  recursos: Recurso[]
  onCancelar: () => void
  onGuardado: () => void
}) {
  // El recurso solo se puede cambiar cuando la reserva es de un único activo:
  // en un evento de varios, ese cambio es por línea, no del bloque.
  const unaLinea = evento.reservas.length === 1
  const primera = evento.reservas[0]

  const [recursoId, setRecursoId] = useState(String(primera.recurso_id))
  const [inicio, setInicio] = useState(aInputLocal(evento.inicio))
  const [fin, setFin] = useState(aInputLocal(evento.fin))
  const [estado, setEstado] = useState<EstadoReserva>(evento.estado)
  const [guardando, setGuardando] = useState(false)

  // Si el catálogo de activos aún no cargó, el recurso actual igual debe
  // poder mostrarse en el select.
  const opciones = recursos.some((r) => r.id === primera.recurso_id)
    ? recursos
    : [
        {
          id: primera.recurso_id,
          nombre: etiquetaReserva(primera),
          tipo: primera.recurso_tipo ?? "equipo",
        } as Recurso,
        ...recursos,
      ]

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
    const cambios: CambiosReserva = {}
    if (nuevoInicio.getTime() !== evento.inicio.getTime()) cambios.inicio = nuevoInicio.toISOString()
    if (nuevoFin.getTime() !== evento.fin.getTime()) cambios.fin = nuevoFin.toISOString()
    if (estado !== evento.estado || evento.estadosMixtos) cambios.estado = estado
    if (unaLinea && Number(recursoId) !== primera.recurso_id) {
      cambios.recurso_id = Number(recursoId)
    }

    if (Object.keys(cambios).length === 0) {
      onCancelar()
      return
    }

    setGuardando(true)
    try {
      if (unaLinea) {
        await actualizarReserva(primera.id, cambios)
      } else {
        // Todas las líneas se mueven juntas o ninguna.
        const { recurso_id: _, ...cambiosBloque } = cambios
        await actualizarReservasBloque(
          evento.reservas.map((r) => r.id),
          cambiosBloque
        )
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
      {unaLinea ? (
        <div className="space-y-1.5">
          <Label htmlFor="reserva-recurso">Activo</Label>
          <Select value={recursoId} onValueChange={setRecursoId}>
            <SelectTrigger id="reserva-recurso" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {opciones.map((r) => (
                <SelectItem key={r.id} value={String(r.id)}>
                  {r.nombre}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      ) : (
        <Dato etiqueta={`Activos de esta reserva (${evento.reservas.length})`}>
          <ul className="text-muted-foreground">
            {evento.reservas.map((r) => (
              <li key={r.id} className="truncate">
                {etiquetaReserva(r)}
              </li>
            ))}
          </ul>
        </Dato>
      )}

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
        {unaLinea
          ? "Horarios en hora local de la CPA (America/Bogota). Dos reservas confirmadas del mismo activo no pueden cruzarse."
          : `El horario y el estado se aplican a los ${evento.reservas.length} activos de esta reserva. Horarios en hora local de la CPA (America/Bogota).`}
      </p>

      <DialogFooter>
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
}: {
  evento: EventoCalendario
  recursos: Recurso[]
  onGuardado: () => void
}) {
  const [editando, setEditando] = useState(false)

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
        />
      ) : (
        <>
          <Detalle evento={evento} />
          <DialogFooter>
            <Button size="sm" variant="outline" onClick={() => setEditando(true)}>
              <Pencil />
              Editar reserva
            </Button>
          </DialogFooter>
        </>
      )}
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
            onGuardado={onGuardado}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}
