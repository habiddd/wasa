import { useState, type FormEvent, type ReactNode } from "react"
import { Check, Pencil, Plus, Trash2, X } from "lucide-react"
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
import { Textarea } from "@/components/ui/textarea"
import {
  actualizarReserva,
  actualizarReservaGrupo,
  actualizarReservasBloque,
  confirmarReservaGrupo,
  decidirReserva,
  eliminarReservaGrupo,
  type CambiosReserva,
  type EstadoReserva,
  type Modalidad,
  type Recurso,
  type Sesion,
  type TipoUso,
} from "@/lib/api"

import {
  aInputLocal,
  claveDe,
  desdeInputLocal,
  diaDe,
  DIAS_MINIMOS_SEGURO,
  etiquetaEvento,
  etiquetaNumeros,
  etiquetaReserva,
  fechaCivilLarga,
  fechaHora,
  NOTA_MAX_CARACTERES,
  type EventoCalendario,
} from "./calendario-utils"
import { EstadoEventoBadge, EstadoReservaBadge } from "./EstadoBadge"
import { IconoTipo } from "./IconoTipo"
import { IntegrantesEditor, TAMANO_GRUPO_MAX, TAMANO_GRUPO_MIN } from "./IntegrantesEditor"
import { AvisosFechasReserva, useAvisosFechasReserva } from "./use-avisos-fechas"

const ESTADOS: EstadoReserva[] = [
  "confirmada",
  "pendiente",
  "completada",
  "cancelada",
  "denegada",
  "no_show",
]

/** Activos de la reserva que esperan la confirmación de la LACS, sin repetir modelo. */
function nombresPendientes(evento: EventoCalendario) {
  return [
    ...new Set(evento.reservas.filter((r) => r.estado === "pendiente").map(etiquetaReserva)),
  ]
}

type AccionPendiente = "confirmar" | "denegar"

/** Lo que espera el diálogo: un activo pendiente, o todos a la vez (reserva null). */
type Decision = { reserva: Sesion | null; accion: AccionPendiente }

function textoDecision(evento: EventoCalendario, { reserva, accion }: Decision) {
  const numero = etiquetaNumeros(evento)
  if (!reserva) {
    const pendientes = nombresPendientes(evento)
    return {
      titulo: `¿Confirmar todo lo pendiente de la reserva ${numero}?`,
      descripcion: `Se ${pendientes.length === 1 ? "confirmará" : "confirmarán"} ${pendientes.join(", ")} y se le enviará un WhatsApp a ${evento.usuarioNombre} avisándole que su reserva quedó confirmada.`,
    }
  }
  const nombre = etiquetaReserva(reserva)
  if (accion === "confirmar") {
    return {
      titulo: `¿Confirmar ${nombre}?`,
      descripcion: `Queda confirmado en la reserva ${numero} y se le enviará un WhatsApp a ${evento.usuarioNombre} avisándole.`,
    }
  }
  return {
    titulo: `¿Denegar ${nombre}?`,
    descripcion: `Sale de la reserva ${numero}, queda libre en ese horario para otra persona y se le enviará un WhatsApp a ${evento.usuarioNombre} avisándole que la LACS no aprobó el préstamo.`,
  }
}

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

function Detalle({
  evento,
  recursos,
  onDecidir,
}: {
  evento: EventoCalendario
  recursos: Recurso[]
  /** Botones Confirmar / Denegar de cada activo pendiente. */
  onDecidir: (reserva: Sesion, accion: AccionPendiente) => void
}) {
  const lineas = evento.reservas.map((reserva) => ({
    reserva,
    padreId: recursos.find((recurso) => recurso.id === reserva.recurso_id)?.padre_id ?? null,
  }))
  const ids = new Set(lineas.map(({ reserva }) => reserva.recurso_id))
  const raices = lineas.filter(({ padreId }) => padreId === null || !ids.has(padreId))
  const primera = evento.reservas[0]
  // `fecha_minima_seguro` ya viene calculada por el backend (5 días
  // hábiles desde `creado_en`, festivos de Colombia, ver
  // recursos_service.fecha_minima_sale_de_barranquilla) -- acá solo se
  // compara, nunca se recalcula el plazo en el frontend.
  const incumplePlazoDesdeCreacion =
    primera.sale_de_barranquilla === true &&
    !!primera.fecha_minima_seguro &&
    claveDe(diaDe(evento.inicio)) < primera.fecha_minima_seguro

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
        {/* flex-wrap: en pantallas angostas los botones bajan a su propia
            línea en vez de dejar el nombre en tres letras. */}
        <div
          className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1 py-0.5"
          style={{ paddingLeft: `${nivel * 1.25}rem` }}
        >
          <span className="flex min-w-0 items-center gap-1.5">
            <IconoTipo
              tipo={reserva.recurso_tipo}
              className="size-3.5 shrink-0 text-muted-foreground"
            />
            <span className="truncate">{etiquetaReserva(reserva)}</span>
          </span>
          <span className="ml-auto flex items-center gap-1.5">
            {(evento.estadosMixtos || reserva.estado === "pendiente") && (
              <EstadoReservaBadge estado={reserva.estado} />
            )}
            {reserva.estado === "pendiente" && (
              <>
                <Button size="xs" variant="outline" onClick={() => onDecidir(reserva, "confirmar")}>
                  <Check />
                  Confirmar
                </Button>
                <Button
                  size="xs"
                  variant="outline"
                  className="text-destructive hover:text-destructive"
                  onClick={() => onDecidir(reserva, "denegar")}
                >
                  <X />
                  Denegar
                </Button>
              </>
            )}
          </span>
        </div>
        {hijosDe(reserva.recurso_id).map((hijo) => renderLinea(hijo, nivel + 1))}
      </li>
    )
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <EstadoEventoBadge evento={evento} />
        <Badge variant="outline">{evento.reservas[0].canal_origen}</Badge>
        <Badge variant="outline">{evento.usuarioRol}</Badge>
      </div>

      {evento.porConfirmar && (
        <p className="rounded-md border border-dashed border-warning bg-warning/5 px-3 py-2 text-sm">
          {evento.parcial && "Reserva parcial. "}Esperan la confirmación de la LACS:{" "}
          {nombresPendientes(evento).join(", ")}. Confirma o deniega cada uno en la lista de
          abajo; el estudiante recibe un WhatsApp con cada decisión.
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        <Dato etiqueta="Inicio">{fechaHora(evento.inicio)}</Dato>
        <Dato etiqueta="Fin">{fechaHora(evento.fin)}</Dato>
        <Dato etiqueta="Duración">{duracion(evento.inicio, evento.fin)}</Dato>
        <Dato etiqueta="Usuario">
          <p>{evento.usuarioNombre}</p>
          <p className="text-muted-foreground">{evento.usuarioTelefono}</p>
        </Dato>
        <Dato etiqueta="Uso">
          {evento.reservas[0].tipo_uso === "asignatura"
            ? `Asignatura · NRC ${evento.reservas[0].nrc}`
            : "Particular"}
        </Dato>
        <Dato etiqueta="Grupo">
          {evento.reservas[0].modalidad === "grupo" ? (
            <>
              <p>Grupo de {evento.reservas[0].tamano_grupo}</p>
              <ul className="text-muted-foreground">
                {evento.reservas[0].integrantes.map((codigo, i) => (
                  <li key={i}>
                    {codigo}
                    {i === 0 && " (quien reserva)"}
                  </li>
                ))}
              </ul>
            </>
          ) : (
            "Individual"
          )}
        </Dato>
        <Dato etiqueta="Salida de Barranquilla">
          {primera.sale_de_barranquilla === true ? (
            <>
              <Badge variant="warning">Sale de Barranquilla</Badge>
              <p className="text-muted-foreground">
                Requiere trámite de seguro (mín. {DIAS_MINIMOS_SEGURO} días hábiles)
              </p>
            </>
          ) : primera.sale_de_barranquilla === false ? (
            "No sale de Barranquilla"
          ) : (
            <span className="text-muted-foreground">Sin registrar</span>
          )}
        </Dato>
      </div>

      {incumplePlazoDesdeCreacion && (
        <p className="rounded-md border border-dashed border-warning bg-warning/5 px-3 py-2 text-sm">
          Los activos salen de Barranquilla y la reserva empieza antes del plazo del seguro (5
          días hábiles desde que se creó; fecha mínima: {fechaCivilLarga(primera.fecha_minima_seguro!)}
          ): verifica que el seguro alcance a tramitarse.
        </p>
      )}

      {primera.nota && (
        <Dato etiqueta="Nota">
          <p className="whitespace-pre-wrap break-words">{primera.nota}</p>
        </Dato>
      )}

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
  // Con estados mixtos (ej. una reserva parcial) no hay un estado único que
  // mostrar: el Select arranca vacío y solo se envía si se elige uno. Si no,
  // guardar otro cambio (el horario, por ejemplo) pisaba el estado de cada
  // línea y confirmaba lo pendiente sin avisarle al estudiante.
  const [estado, setEstado] = useState<EstadoReserva | "">(
    evento.estadosMixtos ? "" : evento.estado
  )
  const [tipoUso, setTipoUso] = useState<TipoUso>(primera.tipo_uso)
  const [nrc, setNrc] = useState(primera.nrc ?? "")
  const [modalidad, setModalidad] = useState<Modalidad>(primera.modalidad)
  const [integrantes, setIntegrantes] = useState<string[]>(
    primera.modalidad === "grupo" ? primera.integrantes : []
  )
  const [saleBarranquilla, setSaleBarranquilla] = useState<"si" | "no" | "">(
    primera.sale_de_barranquilla === true ? "si" : primera.sale_de_barranquilla === false ? "no" : ""
  )
  const [dejarNota, setDejarNota] = useState(!!primera.nota)
  const [nota, setNota] = useState(primera.nota ?? "")
  const [guardando, setGuardando] = useState(false)
  const [agregandoActivo, setAgregandoActivo] = useState(false)
  const [activosParaAgregar, setActivosParaAgregar] = useState<number[]>([])

  // Fecha mínima de HOY (no de creación, a diferencia del aviso de solo
  // lectura en Detalle) para el aviso del Select "¿Sale de Barranquilla?", y
  // lista negra de fechas (pestaña "Excepciones") del inicio/fin editados --
  // el dashboard NO bloquea el guardado con ninguno de los dos, solo avisa
  // (ver docs/ARQUITECTURA.md, secciones "Cláusula de seguro" y "Excepciones").
  const inicioEditado = desdeInputLocal(inicio)
  const finEditado = desdeInputLocal(fin)
  const { minimaHoy, incumplePlazo: incumplePlazoDesdeHoy, avisosBloqueo } = useAvisosFechasReserva(
    inicioEditado,
    finEditado,
    saleBarranquilla === "si"
  )

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

  // Al pasar a "Grupo" desde "Individual" la fila 1 arranca con el código
  // del titular (ya lo tenemos, es el mismo dato que llenó al registrarse) y
  // queda editable por si el dashboard necesita corregirlo.
  function cambiarModalidad(valor: Modalidad) {
    setModalidad(valor)
    if (valor === "grupo" && integrantes.length === 0) {
      setIntegrantes([primera.usuario_codigo_uninorte ?? ""])
    }
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
    const cambios: CambiosReserva & {
      recurso_ids?: number[]
      tipo_uso?: TipoUso
      nrc?: string | null
      modalidad?: Modalidad
      integrantes?: string[]
      sale_de_barranquilla?: boolean
      nota?: string | null
    } = {}
    if (nuevoInicio.getTime() !== evento.inicio.getTime()) cambios.inicio = nuevoInicio.toISOString()
    if (nuevoFin.getTime() !== evento.fin.getTime()) cambios.fin = nuevoFin.toISOString()
    if (estado && (estado !== evento.estado || evento.estadosMixtos)) cambios.estado = estado
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

    // Uso (particular/asignatura) solo se edita cuando hay un único número
    // de reserva: es un dato del grupo, no de cada línea suelta.
    if (evento.numeros.length === 1) {
      if (tipoUso === "asignatura" && !/^\d{4}$/.test(nrc)) {
        toast.error("El NRC debe tener exactamente 4 dígitos.")
        return
      }
      const nrcOriginal = primera.nrc ?? ""
      if (tipoUso !== primera.tipo_uso || (tipoUso === "asignatura" && nrc !== nrcOriginal)) {
        cambios.tipo_uso = tipoUso
        cambios.nrc = tipoUso === "asignatura" ? nrc : null
      }

      // Modalidad (individual/grupo), igual de independiente de tipo_uso acá
      // que en el wizard del bot.
      if (modalidad === "grupo") {
        const limpios = integrantes.map((codigo) => codigo.trim())
        if (
          limpios.length < TAMANO_GRUPO_MIN ||
          limpios.length > TAMANO_GRUPO_MAX ||
          limpios.some((codigo) => !codigo)
        ) {
          toast.error(
            `El grupo debe tener entre ${TAMANO_GRUPO_MIN} y ${TAMANO_GRUPO_MAX} integrantes, todos con ID.`
          )
          return
        }
        if (new Set(limpios).size !== limpios.length) {
          toast.error("No puede haber integrantes repetidos.")
          return
        }
        const integrantesOriginales = primera.modalidad === "grupo" ? primera.integrantes : []
        const cambiaronIntegrantes =
          modalidad !== primera.modalidad ||
          limpios.length !== integrantesOriginales.length ||
          limpios.some((codigo, i) => codigo !== integrantesOriginales[i])
        if (cambiaronIntegrantes) {
          cambios.modalidad = "grupo"
          cambios.integrantes = limpios
        }
      } else if (modalidad !== primera.modalidad) {
        cambios.modalidad = "individual"
        cambios.integrantes = []
      }

      // "" (sin tocar el Select) nunca se envía -- no bloquea, y el dashboard
      // no tiene por qué forzar una respuesta que reservas viejas no tienen.
      if (saleBarranquilla !== "") {
        const saleBarranquillaNueva = saleBarranquilla === "si"
        if (saleBarranquillaNueva !== primera.sale_de_barranquilla) {
          cambios.sale_de_barranquilla = saleBarranquillaNueva
        }
      }

      // Desmarcar el checkbox borra la nota (manda null); si no cambió, no
      // se manda nada.
      const notaNueva = dejarNota && nota.trim() ? nota.trim() : null
      if (notaNueva !== (primera.nota ?? null)) {
        cambios.nota = notaNueva
      }
    }

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
            <SelectValue placeholder="Sin cambios (estados mixtos)" />
          </SelectTrigger>
          <SelectContent>
            {ESTADOS.map((e) => (
              <SelectItem key={e} value={e}>
                {e}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {evento.porConfirmar && (
          <p className="text-xs text-muted-foreground">
            Para confirmar o denegar lo pendiente y avisarle al estudiante por WhatsApp, usa los
            botones de cada activo en el detalle.
          </p>
        )}
      </div>

      {evento.numeros.length === 1 && (
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="reserva-uso">Uso</Label>
            <Select value={tipoUso} onValueChange={(v) => setTipoUso(v as TipoUso)}>
              <SelectTrigger id="reserva-uso" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="particular">Particular</SelectItem>
                <SelectItem value="asignatura">Asignatura</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {tipoUso === "asignatura" && (
            <div className="space-y-1.5">
              <Label htmlFor="reserva-nrc">NRC</Label>
              <Input
                id="reserva-nrc"
                inputMode="numeric"
                maxLength={4}
                value={nrc}
                onChange={(e) => setNrc(e.target.value)}
              />
            </div>
          )}
        </div>
      )}

      {evento.numeros.length === 1 && (
        <div className="space-y-1.5">
          <Label htmlFor="reserva-modalidad">Grupo</Label>
          <Select value={modalidad} onValueChange={(v) => cambiarModalidad(v as Modalidad)}>
            <SelectTrigger id="reserva-modalidad" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="individual">Individual</SelectItem>
              <SelectItem value="grupo">Grupo</SelectItem>
            </SelectContent>
          </Select>
        </div>
      )}

      {evento.numeros.length === 1 && (
        <div className="space-y-1.5">
          <Label htmlFor="reserva-sale-barranquilla">¿Sale de Barranquilla?</Label>
          <Select value={saleBarranquilla} onValueChange={(v) => setSaleBarranquilla(v as "si" | "no")}>
            <SelectTrigger id="reserva-sale-barranquilla" className="w-full">
              <SelectValue placeholder="Sin registrar" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="si">Sí, requiere seguro</SelectItem>
              <SelectItem value="no">No</SelectItem>
            </SelectContent>
          </Select>
          {saleBarranquilla === "si" && (
            <p className="text-xs text-muted-foreground">
              Requiere tramitar el seguro de los activos: plazo mínimo de {DIAS_MINIMOS_SEGURO} días
              hábiles (lunes a viernes, sin festivos).
            </p>
          )}
        </div>
      )}

      {evento.numeros.length === 1 && (
        <div className="space-y-1.5">
          <div className="flex items-center gap-2">
            <Checkbox
              id="reserva-dejar-nota"
              checked={dejarNota}
              onCheckedChange={(v) => {
                const marcado = v === true
                setDejarNota(marcado)
                if (!marcado) setNota("")
              }}
              disabled={guardando}
            />
            <Label htmlFor="reserva-dejar-nota" className="cursor-pointer">
              Dejar una nota
            </Label>
          </div>
          {dejarNota && (
            <>
              <Textarea
                id="reserva-nota"
                value={nota}
                onChange={(e) => setNota(e.target.value)}
                maxLength={NOTA_MAX_CARACTERES}
                disabled={guardando}
                placeholder="Aclaración sobre el uso de la reserva…"
                className="resize-none"
              />
              <p className="text-xs text-muted-foreground">
                {nota.length}/{NOTA_MAX_CARACTERES} caracteres
              </p>
            </>
          )}
        </div>
      )}

      <AvisosFechasReserva minimaHoy={minimaHoy} incumplePlazo={incumplePlazoDesdeHoy} avisosBloqueo={avisosBloqueo} />

      {evento.numeros.length === 1 && modalidad === "grupo" && (
        <IntegrantesEditor integrantes={integrantes} onChange={setIntegrantes} disabled={guardando} />
      )}

      <p className="text-xs text-muted-foreground">
        Horarios en hora local de la LACS (America/Bogota). Dos reservas confirmadas o pendientes del mismo activo no pueden cruzarse.
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
  // La decisión se conserva al cerrar el diálogo (solo se baja `abierto`)
  // para que el texto no desaparezca durante la animación de salida.
  const [decision, setDecision] = useState<Decision | null>(null)
  const [dialogoDecision, setDialogoDecision] = useState(false)
  const [decidiendo, setDecidiendo] = useState(false)

  // "Confirmar todo" solo si hay más de un activo pendiente: con uno, sus
  // botones propios ya lo cubren. Va por número de reserva; las pendientes
  // siempre tienen uno (las crea el bot, ver recursos_service.crear_reservas_grupo).
  const lineasPendientes = evento.reservas.filter((r) => r.estado === "pendiente")
  const puedeConfirmarTodo = lineasPendientes.length > 1 && evento.numeros.length === 1

  function pedirDecision(nueva: Decision) {
    setDecision(nueva)
    setDialogoDecision(true)
  }

  async function aplicarDecision() {
    if (!decision) return
    const { reserva, accion } = decision
    setDecidiendo(true)
    try {
      const { notificado } = reserva
        ? await decidirReserva(reserva.id, accion)
        : await confirmarReservaGrupo(evento.numeros[0])
      setDialogoDecision(false)
      const hecho = reserva
        ? `${etiquetaReserva(reserva)} ${accion === "confirmar" ? "confirmado" : "denegado"}.`
        : "Reserva confirmada."
      if (notificado) {
        toast.success(`${hecho} Se le avisó a ${evento.usuarioNombre} por WhatsApp.`)
      } else {
        toast.warning(`${hecho} No se pudo enviar el WhatsApp: avísale al estudiante por otro medio.`)
      }
      onGuardado()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setDecidiendo(false)
    }
  }

  const texto = decision ? textoDecision(evento, decision) : null

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
          <Detalle
            evento={evento}
            recursos={recursos}
            onDecidir={(reserva, accion) => pedirDecision({ reserva, accion })}
          />
          <DialogFooter>
            <Button size="sm" variant="destructive" onClick={solicitarEliminacion}>
              <Trash2 />
              Eliminar reserva
            </Button>
            <Button size="sm" variant="outline" onClick={() => setEditando(true)}>
              <Pencil />
              Editar reserva
            </Button>
            {puedeConfirmarTodo && (
              <Button size="sm" onClick={() => pedirDecision({ reserva: null, accion: "confirmar" })}>
                <Check />
                Confirmar todo
              </Button>
            )}
          </DialogFooter>
        </>
      )}

      <Dialog
        open={dialogoDecision}
        onOpenChange={(abierto) => {
          if (!decidiendo) setDialogoDecision(abierto)
        }}
      >
        <DialogContent showCloseButton={false} className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{texto?.titulo}</DialogTitle>
            <DialogDescription>{texto?.descripcion}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setDialogoDecision(false)}
              disabled={decidiendo}
            >
              Cancelar
            </Button>
            {decision?.accion === "denegar" ? (
              <Button type="button" variant="destructive" onClick={aplicarDecision} disabled={decidiendo}>
                <X />
                {decidiendo ? "Guardando…" : "Denegar y avisar"}
              </Button>
            ) : (
              <Button type="button" onClick={aplicarDecision} disabled={decidiendo}>
                <Check />
                {decidiendo ? "Guardando…" : "Confirmar y avisar"}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

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
