import { useEffect, useState, type FormEvent } from "react"
import { toast } from "sonner"

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
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import { desdeInputLocal, DIAS_MINIMOS_SEGURO, NOTA_MAX_CARACTERES } from "@/features/calendario/calendario-utils"
import { IntegrantesEditor, TAMANO_GRUPO_MAX, TAMANO_GRUPO_MIN } from "@/features/calendario/IntegrantesEditor"
import { AvisosFechasReserva, useAvisosFechasReserva } from "@/features/calendario/use-avisos-fechas"
import {
  crearReserva,
  fetchOcupados,
  type Modalidad,
  type NuevaReserva,
  type Recurso,
  type ReservaCreada,
  type TipoUso,
} from "@/lib/api"

import { SelectorActivos } from "./SelectorActivos"
import { SelectorCliente, type SeleccionCliente } from "./SelectorCliente"

/**
 * "Nueva reserva" desde el dashboard: mismos campos que el wizard del bot
 * por WhatsApp (ver recursos_service._avanzar_wizard), en el mismo orden,
 * pero sin sus bloqueos "blandos" (fecha bloqueada, plazo del seguro, inicio
 * ya pasado) -- se avisan, no impiden guardar (ver
 * docs/ARQUITECTURA.md, sección "Crear reservas desde el dashboard").
 */
export function NuevaReservaDialog({
  recursos,
  onClose,
  onCreada,
}: {
  recursos: Recurso[]
  onClose: () => void
  onCreada: (reserva: ReservaCreada) => void
}) {
  const [cliente, setCliente] = useState<SeleccionCliente>(null)
  const [recursoIds, setRecursoIds] = useState<number[]>([])
  const [tipoUso, setTipoUso] = useState<TipoUso>("particular")
  const [nrc, setNrc] = useState("")
  const [modalidad, setModalidad] = useState<Modalidad>("individual")
  const [integrantes, setIntegrantes] = useState<string[]>([])
  const [saleBarranquilla, setSaleBarranquilla] = useState<"si" | "no" | "">("")
  const [dejarNota, setDejarNota] = useState(false)
  const [nota, setNota] = useState("")
  const [inicio, setInicio] = useState("")
  const [fin, setFin] = useState("")
  const [notificar, setNotificar] = useState(false)
  const [guardando, setGuardando] = useState(false)
  const [ocupados, setOcupados] = useState<Set<number>>(new Set())

  const inicioFecha = desdeInputLocal(inicio)
  const finFecha = desdeInputLocal(fin)
  const { minimaHoy, incumplePlazo, avisosBloqueo } = useAvisosFechasReserva(
    inicioFecha,
    finFecha,
    saleBarranquilla === "si"
  )
  const inicioYaPaso = !!inicioFecha && inicioFecha.getTime() < Date.now()

  // Si un activo ya marcado queda ocupado en el rango elegido (otra reserva
  // de por medio, o simplemente se cambió el horario), se destilda solo y se
  // avisa -- así nunca se manda algo que el backend igual va a rechazar (ver
  // GET /reservas/ocupados).
  useEffect(() => {
    const ini = desdeInputLocal(inicio)
    const finD = desdeInputLocal(fin)
    if (!ini || !finD || finD <= ini) {
      setOcupados(new Set())
      return
    }
    let vigente = true
    fetchOcupados(ini, finD)
      .then(({ ocupados: ids }) => {
        if (!vigente) return
        const nuevos = new Set(ids)
        setOcupados(nuevos)
        setRecursoIds((anteriores) => {
          const quitados = anteriores.filter((id) => nuevos.has(id))
          if (quitados.length === 0) return anteriores
          const nombres = quitados
            .map((id) => recursos.find((r) => r.id === id)?.nombre ?? `#${id}`)
            .join(", ")
          toast.warning(`Se quitaron activos ocupados en ese horario: ${nombres}.`)
          return anteriores.filter((id) => !nuevos.has(id))
        })
      })
      .catch(() => {})
    return () => {
      vigente = false
    }
  }, [inicio, fin, recursos])

  // Al pasar a "Grupo", la fila 1 arranca con el código uninorte del cliente
  // elegido -- mismo criterio que cambiarModalidad en ReservaDetalle.tsx.
  function cambiarModalidad(valor: Modalidad) {
    setModalidad(valor)
    if (valor === "grupo" && integrantes.length === 0) {
      const codigo =
        cliente?.tipo === "existente"
          ? (cliente.usuario.codigo_uninorte ?? "")
          : cliente?.tipo === "nuevo"
            ? (cliente.datos.codigo_uninorte ?? "")
            : ""
      setIntegrantes([codigo, ""])
    }
  }

  async function guardar(evt: FormEvent) {
    evt.preventDefault()

    if (!cliente) {
      toast.error("Indica para quién es la reserva.")
      return
    }
    if (cliente.tipo === "nuevo") {
      if (cliente.datos.nombre.trim().split(/\s+/).filter(Boolean).length < 2) {
        toast.error("Escribe nombre y apellido del usuario nuevo.")
        return
      }
      if (!cliente.datos.telefono.trim()) {
        toast.error("Escribe el teléfono del usuario nuevo.")
        return
      }
    }

    if (recursoIds.length === 0) {
      toast.error("Elige al menos un activo.")
      return
    }

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

    if (tipoUso === "asignatura" && !/^\d{4}$/.test(nrc)) {
      toast.error("El NRC debe tener exactamente 4 dígitos.")
      return
    }

    let codigosGrupo: string[] | undefined
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
      codigosGrupo = limpios
    }

    if (saleBarranquilla === "") {
      toast.error("Indica si los activos salen de Barranquilla.")
      return
    }

    const datos: NuevaReserva = {
      // Un equipo puede estar marcado desde "Espacios" y desde "Activos": se
      // reserva una sola vez.
      recurso_ids: [...new Set(recursoIds)],
      inicio: nuevoInicio.toISOString(),
      fin: nuevoFin.toISOString(),
      tipo_uso: tipoUso,
      nrc: tipoUso === "asignatura" ? nrc : undefined,
      modalidad,
      integrantes: codigosGrupo,
      sale_de_barranquilla: saleBarranquilla === "si",
      // Opcional: si marcó el checkbox pero no escribió nada, simplemente no
      // se manda (sin toast de error, a diferencia de sale_de_barranquilla).
      nota: dejarNota && nota.trim() ? nota.trim() : undefined,
      notificar,
      ...(cliente.tipo === "existente"
        ? { usuario_id: cliente.usuario.id }
        : {
            cliente_nuevo: {
              nombre: cliente.datos.nombre.trim(),
              telefono: cliente.datos.telefono.trim(),
              codigo_uninorte: cliente.datos.codigo_uninorte?.trim() || undefined,
              cedula: cliente.datos.cedula?.trim() || undefined,
            },
          }),
    }

    setGuardando(true)
    try {
      const resultado = await crearReserva(datos)
      toast.success(
        `Reserva N.º ${resultado.grupo_id} creada para ${resultado.usuario.nombre ?? resultado.usuario.telefono_wa}.`
      )
      if (resultado.advertencias.length > 0) {
        toast.warning(resultado.advertencias.join(" "))
      }
      if (notificar && resultado.notificado === false) {
        toast.warning("No se pudo enviar el WhatsApp: avísale al usuario por otro medio.")
      }
      onCreada(resultado)
      onClose()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setGuardando(false)
    }
  }

  return (
    <Dialog open onOpenChange={(abierto) => !abierto && !guardando && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Nueva reserva</DialogTitle>
          <DialogDescription>Mismos datos que pide el bot por WhatsApp.</DialogDescription>
        </DialogHeader>

        <form onSubmit={guardar} className="space-y-3">
          <SelectorCliente seleccion={cliente} onChange={setCliente} disabled={guardando} />

          <Separator />

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="nueva-reserva-uso">Uso</Label>
              <Select value={tipoUso} onValueChange={(v) => setTipoUso(v as TipoUso)}>
                <SelectTrigger id="nueva-reserva-uso" className="w-full" disabled={guardando}>
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
                <Label htmlFor="nueva-reserva-nrc">NRC</Label>
                <Input
                  id="nueva-reserva-nrc"
                  inputMode="numeric"
                  maxLength={4}
                  value={nrc}
                  onChange={(e) => setNrc(e.target.value)}
                  disabled={guardando}
                />
              </div>
            )}
          </div>

          <Separator />

          <div className="space-y-1.5">
            <Label htmlFor="nueva-reserva-modalidad">Grupo</Label>
            <Select value={modalidad} onValueChange={(v) => cambiarModalidad(v as Modalidad)}>
              <SelectTrigger id="nueva-reserva-modalidad" className="w-full" disabled={guardando}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="individual">Individual</SelectItem>
                <SelectItem value="grupo">Grupo</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {modalidad === "grupo" && (
            <IntegrantesEditor integrantes={integrantes} onChange={setIntegrantes} disabled={guardando} />
          )}

          <Separator />

          <div className="space-y-2">
            <SelectorActivos
              tipo="espacio"
              recursos={recursos}
              seleccionados={recursoIds}
              onChange={setRecursoIds}
              ocupados={ocupados}
              disabled={guardando}
            />
            <SelectorActivos
              tipo="equipo"
              recursos={recursos}
              seleccionados={recursoIds}
              onChange={setRecursoIds}
              ocupados={ocupados}
              disabled={guardando}
            />
          </div>

          <p className="text-sm text-foreground/80">
            <span className="font-semibold text-foreground">
              {recursoIds.length} seleccionado{recursoIds.length === 1 ? "" : "s"} en total.
            </span>{" "}
            Al marcar
            un espacio o un equipo se incluyen sus contenidos y accesorios; puedes quitarlos uno a uno.
          </p>

          <Separator />

          <div className="space-y-1.5">
            <Label htmlFor="nueva-reserva-sale-barranquilla">¿Los activos salen de Barranquilla?</Label>
            <Select
              value={saleBarranquilla}
              onValueChange={(v) => setSaleBarranquilla(v as "si" | "no")}
            >
              <SelectTrigger id="nueva-reserva-sale-barranquilla" className="w-full" disabled={guardando}>
                <SelectValue placeholder="Elige una opción" />
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

          <Separator />

          <div className="space-y-1.5">
            <div className="flex items-center gap-2">
              <Checkbox
                id="nueva-reserva-dejar-nota"
                checked={dejarNota}
                onCheckedChange={(v) => {
                  const marcado = v === true
                  setDejarNota(marcado)
                  if (!marcado) setNota("")
                }}
                disabled={guardando}
              />
              <Label htmlFor="nueva-reserva-dejar-nota" className="cursor-pointer">
                Dejar una nota
              </Label>
            </div>
            {dejarNota && (
              <>
                <Textarea
                  id="nueva-reserva-nota"
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

          <Separator />

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="nueva-reserva-inicio">Inicio</Label>
              <Input
                id="nueva-reserva-inicio"
                type="datetime-local"
                value={inicio}
                onChange={(e) => setInicio(e.target.value)}
                disabled={guardando}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="nueva-reserva-fin">Fin</Label>
              <Input
                id="nueva-reserva-fin"
                type="datetime-local"
                value={fin}
                onChange={(e) => setFin(e.target.value)}
                disabled={guardando}
              />
            </div>
          </div>

          {inicioYaPaso && <p className="text-xs text-warning-text">Ojo: el inicio ya pasó.</p>}
          <AvisosFechasReserva minimaHoy={minimaHoy} incumplePlazo={incumplePlazo} avisosBloqueo={avisosBloqueo} />
          <p className="text-xs text-muted-foreground">
            Horarios en hora local de la LACS (America/Bogota).
          </p>

          <Separator />

          <div className="flex items-center gap-2">
            <Checkbox
              id="nueva-reserva-notificar"
              checked={notificar}
              onCheckedChange={(v) => setNotificar(v === true)}
              disabled={guardando}
            />
            <Label htmlFor="nueva-reserva-notificar" className="cursor-pointer">
              Avisar al usuario por WhatsApp
            </Label>
          </div>
          <p className="text-xs text-muted-foreground">
            Se le envía la confirmación con el número de reserva.
          </p>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={guardando}>
              Cancelar
            </Button>
            <Button type="submit" disabled={guardando}>
              {guardando ? "Creando…" : "Crear reserva"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
