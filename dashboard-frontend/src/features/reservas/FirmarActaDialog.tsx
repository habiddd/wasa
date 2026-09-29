import { useEffect, useRef, useState } from "react"
import { Loader2Icon } from "lucide-react"
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
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import { etiquetaEstado, NOTA_MAX_CARACTERES } from "@/features/calendario/calendario-utils"
import { registrarActa, type AnclaFirma, type MomentoActa, type Sesion } from "@/lib/api"

import { estadoResultante, estamparFirmas, formatearFechaActa } from "./actas-pdf"
import { FirmaCanvas, type FirmaCanvasHandle } from "./FirmaCanvas"
import { recortarCuadro } from "./recorte-acta"

type Paso = 1 | 2 | 3 | 4

// Quien firma por la LACS suele ser el mismo monitor todo el turno: se
// recuerda en este navegador tras cada acta registrada para no reescribirlo.
const MONITOR_STORAGE_KEY = "dashboard-lacs-monitor-acta"

function monitorGuardado(): { nombre: string; cedula: string } {
  try {
    const guardado = JSON.parse(localStorage.getItem(MONITOR_STORAGE_KEY) ?? "null")
    if (guardado && typeof guardado.nombre === "string" && typeof guardado.cedula === "string") {
      return guardado
    }
  } catch {
    // localStorage no disponible o valor corrupto
  }
  return { nombre: "", cedula: "" }
}

function guardarMonitor(nombre: string, cedula: string) {
  try {
    localStorage.setItem(MONITOR_STORAGE_KEY, JSON.stringify({ nombre, cedula }))
  } catch {
    // localStorage no disponible
  }
}

export function FirmarActaDialog({
  abierto,
  onOpenChange,
  grupoId,
  momento,
  anclas,
  pdfBase,
  usuarioNombre,
  equipos,
  fin,
  onRegistrada,
}: {
  abierto: boolean
  onOpenChange: (abierto: boolean) => void
  grupoId: number
  momento: MomentoActa
  anclas: Record<string, AnclaFirma>
  /** Bytes del acta vigente (la que se muestra en el preview de BotonActaPdf) -- es la base sobre la que se estampan las firmas. */
  pdfBase: ArrayBuffer
  usuarioNombre: string
  /** Activos de esta reserva, en el mismo orden (índice 1-based) que la
   * columna "N.º" de la tabla del PDF (ver acta_pdf.generar_acta_con_anclas)
   * -- de acá sale la lista de checkboxes del paso "Equipos". */
  equipos: { id: number; nombre: string }[]
  /** Fin de la reserva -- para anunciar en el paso 4 si una devolución llega
   * a tiempo (completada) o tarde (devuelta_tarde). Ver actas-pdf.estadoResultante. */
  fin: Date
  onRegistrada: (reservas: Sesion[]) => void
}) {
  const [paso, setPaso] = useState<Paso>(1)
  const [firmadoPor, setFirmadoPor] = useState(() => monitorGuardado().nombre)
  const [cedulaAdmin, setCedulaAdmin] = useState(() => monitorGuardado().cedula)
  const [confirmandoDescarte, setConfirmandoDescarte] = useState(false)
  const [dejarNota, setDejarNota] = useState(false)
  const [nota, setNota] = useState("")
  // Equipos marcados como entregados/devueltos -- todos por defecto (un solo
  // chulito para todo es el caso común), el administrador desmarca los que
  // no apliquen. Guarda ids de línea (`Reserva.id`, `equipos[].id`).
  const [marcados, setMarcados] = useState<Set<number>>(() => new Set(equipos.map((e) => e.id)))
  const [usuarioVacio, setUsuarioVacio] = useState(true)
  const [adminVacio, setAdminVacio] = useState(true)
  const [generando, setGenerando] = useState(false)
  const [guardando, setGuardando] = useState(false)
  const [pdfFirmado, setPdfFirmado] = useState<Uint8Array | null>(null)
  const [url, setUrl] = useState<string | null>(null)

  // Fecha y hora que va a quedar impresa en los dos cuadros de este momento
  // -- se congela al ABRIR el diálogo, no al componer el PDF (irAPaso4),
  // porque el usuario pidió que sea "la fecha cuando se crea la petición de
  // hacer la firma", no la del rato después en que se termina de firmar.
  // Reiniciada cada vez que `abierto` pasa a true (ver el efecto de abajo) --
  // el componente puede seguir montado entre una firma cancelada y la
  // siguiente apertura dentro de la misma sesión de BotonActaPdf.
  const [fechaPeticion, setFechaPeticion] = useState(() => new Date())
  useEffect(() => {
    if (abierto) {
      setFechaPeticion(new Date())
      setMarcados(new Set(equipos.map((e) => e.id)))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `equipos` es un array derivado de `reservas` en BotonActaPdf, se relee tal cual en cada apertura sin necesitar que cambie la referencia para disparar el efecto.
  }, [abierto])

  // Fondo de cada lienzo: la franja de firma real del acta, recortada del
  // PDF base con pdf.js (ver recorte-acta.ts) -- decisión del usuario para
  // que la persona vea la raya de "Firma" real y firme encima, no a ciegas.
  // `cargandoFondo` cubre el hueco entre abrir el diálogo y tener los dos
  // recortes listos.
  const [fondoUsuario, setFondoUsuario] = useState<HTMLCanvasElement | null>(null)
  const [fondoAdmin, setFondoAdmin] = useState<HTMLCanvasElement | null>(null)
  const [cargandoFondo, setCargandoFondo] = useState(true)

  const canvasUsuarioRef = useRef<FirmaCanvasHandle>(null)
  const canvasAdminRef = useRef<FirmaCanvasHandle>(null)

  // Zona de firma de cada firmante para este momento (ver
  // acta_pdf.generar_acta_con_anclas) -- las dos celdas de una misma fila NO
  // miden lo mismo (la del usuario lleva una línea extra de "Titular: …"),
  // así que cada lienzo usa su propia proporción, no una compartida.
  const anclaUsuario = anclas[`${momento}_usuario`]
  const anclaAdmin = anclas[`${momento}_admin`]
  const proporcionUsuario = anclaUsuario ? anclaUsuario.alto / anclaUsuario.ancho : 1
  const proporcionAdmin = anclaAdmin ? anclaAdmin.alto / anclaAdmin.ancho : 1

  // Se recorta cada vez que el diálogo se abre (no en cada render mientras
  // está montado-pero-oculto): renderizar con pdf.js no es gratis y el PDF
  // base no cambia mientras el diálogo permanece abierto.
  useEffect(() => {
    if (!abierto || !anclaUsuario || !anclaAdmin) return
    let cancelado = false
    setCargandoFondo(true)
    Promise.all([recortarCuadro(pdfBase, anclaUsuario), recortarCuadro(pdfBase, anclaAdmin)])
      .then(([usuario, admin]) => {
        if (cancelado) return
        setFondoUsuario(usuario)
        setFondoAdmin(admin)
      })
      .catch((err) => {
        if (!cancelado) toast.error(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelado) setCargandoFondo(false)
      })
    return () => {
      cancelado = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- anclaUsuario/anclaAdmin son objetos derivados de `anclas`, estables mientras no cambie la prop.
  }, [abierto, pdfBase])

  // Mismo patrón que BotonActaPdf: el object URL se ata al blob compuesto,
  // no al clic, y se revoca solo cuando llega otro o el diálogo se cierra.
  useEffect(() => {
    if (!pdfFirmado) {
      setUrl(null)
      return
    }
    const blob = new Blob([pdfFirmado as BlobPart], { type: "application/pdf" })
    const nueva = URL.createObjectURL(blob)
    setUrl(nueva)
    return () => URL.revokeObjectURL(nueva)
  }, [pdfFirmado])

  // Al cerrar, todo vuelve al paso 1 la próxima vez que se abra -- pero los
  // lienzos NO se desmontan mientras el diálogo está abierto (ver el JSX:
  // los cuatro pasos están siempre montados, solo se ocultan con `hidden`),
  // así que "Atrás" no pierde lo ya firmado dentro de una misma apertura.
  function resetearYCerrar() {
    setPaso(1)
    setFirmadoPor(monitorGuardado().nombre)
    setCedulaAdmin(monitorGuardado().cedula)
    setConfirmandoDescarte(false)
    setDejarNota(false)
    setNota("")
    setMarcados(new Set(equipos.map((e) => e.id)))
    setUsuarioVacio(true)
    setAdminVacio(true)
    setPdfFirmado(null)
    canvasUsuarioRef.current?.limpiar()
    canvasAdminRef.current?.limpiar()
    onOpenChange(false)
  }

  // Escape, clic afuera y "Cancelar" pasan por acá: con el estudiante
  // presente, un toque de más no puede borrar firmas ya hechas sin preguntar.
  function intentarCerrar() {
    if (guardando) return
    if (!usuarioVacio || !adminVacio) setConfirmandoDescarte(true)
    else resetearYCerrar()
  }

  // Paso 1 (Equipos + nota) -> 2: en la entrega, cualquier combinación menos
  // ninguno; en la devolución también ninguno (no devolvió nada).
  function irAPaso2() {
    setPaso(2)
  }

  async function irAPaso3() {
    if (canvasUsuarioRef.current?.estaVacio()) {
      toast.error("Falta la firma del usuario.")
      return
    }
    setPaso(3)
  }

  async function irAPaso4() {
    if (!firmadoPor.trim()) {
      toast.error("Falta el nombre de quien firma por la LACS.")
      return
    }
    const pngUsuario = canvasUsuarioRef.current?.exportarPng()
    const pngAdmin = canvasAdminRef.current?.exportarPng()
    if (!pngUsuario || !pngAdmin) {
      toast.error("Faltan una o las dos firmas.")
      return
    }
    setGenerando(true)
    try {
      const bytes = await estamparFirmas(
        pdfBase,
        anclas,
        momento,
        { usuario: pngUsuario, admin: pngAdmin },
        fechaPeticion,
        cedulaAdmin,
        dejarNota ? nota : undefined,
        {
          marcados: [...marcados],
          sinMarcar: equipos.filter((e) => !marcados.has(e.id)).map((e) => e.id),
        }
      )
      setPdfFirmado(bytes)
      setPaso(4)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setGenerando(false)
    }
  }

  async function confirmar() {
    if (!pdfFirmado) return
    setGuardando(true)
    try {
      const { reservas } = await registrarActa(grupoId, {
        archivo: new Blob([pdfFirmado as BlobPart], { type: "application/pdf" }),
        momento,
        origen: "firmada",
        anclas,
        firmadoPor: firmadoPor.trim(),
        nota: dejarNota ? nota.trim() : undefined,
        equipos: [...marcados],
      })
      toast.success("Acta registrada.")
      guardarMonitor(firmadoPor.trim(), cedulaAdmin.trim())
      onRegistrada(reservas)
      resetearYCerrar()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setGuardando(false)
    }
  }

  const resultado = estadoResultante(momento, fin)
  const noDevueltos = momento === "devolucion" ? equipos.filter((e) => !marcados.has(e.id)) : []
  const tituloDialogo = momento === "entrega" ? "Acta de entrega" : "Acta de devolución"

  return (
    <>
    <Dialog open={abierto} onOpenChange={(v) => !v && intentarCerrar()}>
      <DialogContent className="flex max-h-[90vh] flex-col sm:max-w-lg">
        {/* Ancho normal (no el sm:max-w-xl que se necesitó cuando el lienzo
            cubría el cuadro completo del acta): la franja de firma es mucho
            más apaisada, el lienzo mapea 1:1 con `aspect-ratio` y queda bajo
            incluso a este ancho. El `overflow-y-auto` de abajo sigue ahí
            como red de seguridad si una pantalla chica lo necesita. */}
        <DialogHeader>
          <DialogTitle>{tituloDialogo}</DialogTitle>
          <DialogDescription>
            Reserva N.º {grupoId} · {usuarioNombre} · Paso {paso} de 4
          </DialogDescription>
        </DialogHeader>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto">
          {/* Los cuatro pasos quedan siempre montados (solo se ocultan con
              `hidden`): así "Atrás" no borra los trazos ya dibujados, que
              viven dentro del estado interno de cada FirmaCanvas. */}
          <div className={paso === 1 ? "space-y-2" : "hidden"}>
            <p className="text-sm font-medium">1. Equipos y nota</p>
            <div className="space-y-1.5">
              <div className="flex items-center gap-2">
                <Checkbox
                  id="firma-equipos-todos"
                  checked={
                    marcados.size === 0
                      ? false
                      : marcados.size === equipos.length
                        ? true
                        : "indeterminate"
                  }
                  onCheckedChange={(v) =>
                    setMarcados(v === true ? new Set(equipos.map((e) => e.id)) : new Set())
                  }
                />
                <Label htmlFor="firma-equipos-todos" className="cursor-pointer font-medium">
                  Todos los equipos {momento === "entrega" ? "entregados" : "devueltos"}
                </Label>
              </div>
              <ul className="space-y-1 pl-6">
                {equipos.map((e) => (
                  <li key={e.id} className="flex items-center gap-2">
                    <Checkbox
                      id={`firma-equipo-${e.id}`}
                      checked={marcados.has(e.id)}
                      onCheckedChange={(v) =>
                        setMarcados((prev) => {
                          const siguiente = new Set(prev)
                          if (v === true) siguiente.add(e.id)
                          else siguiente.delete(e.id)
                          return siguiente
                        })
                      }
                    />
                    <Label htmlFor={`firma-equipo-${e.id}`} className="cursor-pointer font-normal">
                      {e.nombre}
                    </Label>
                  </li>
                ))}
              </ul>
              {equipos.length > 0 && !anclas[`${momento}_linea_${equipos[0].id}`] && (
                <p className="text-xs text-muted-foreground">
                  Esta acta es anterior a las marcas de equipos: no se imprimen.
                </p>
              )}
            </div>

            <Separator className="my-3" />

            <div className="space-y-1.5">
              <div className="flex items-center gap-2">
                <Checkbox
                  id="firma-dejar-nota"
                  checked={dejarNota}
                  onCheckedChange={(v) => {
                    const marcado = v === true
                    setDejarNota(marcado)
                    if (!marcado) setNota("")
                  }}
                />
                <Label htmlFor="firma-dejar-nota" className="cursor-pointer font-semibold">
                  Agregar nota al acta
                </Label>
              </div>
              {dejarNota && (
                <>
                  <Textarea
                    id="firma-nota"
                    value={nota}
                    onChange={(e) => setNota(e.target.value)}
                    maxLength={NOTA_MAX_CARACTERES}
                    placeholder="Observaciones de esta entrega/devolución…"
                    className="resize-none"
                  />
                  <p className="text-xs text-muted-foreground">
                    {nota.length}/{NOTA_MAX_CARACTERES} caracteres
                  </p>
                  {!anclas[`${momento}_nota`] && (
                    <p className="text-xs text-muted-foreground">
                      Esta acta es anterior a las notas: se guarda en el historial pero no se imprime.
                    </p>
                  )}
                </>
              )}
            </div>
          </div>

          <div className={paso === 2 ? "space-y-2" : "hidden"}>
            <p className="text-sm font-medium">2. Firma del usuario</p>
            <p className="text-sm text-muted-foreground">
              {usuarioNombre}, titular de la reserva N.º {grupoId}.
            </p>
            {/* El lienzo queda siempre montado debajo (ver el comentario de
                arriba sobre los cuatro pasos) -- el overlay de carga solo lo
                tapa mientras pdf.js todavía está recortando el fondo, no lo
                reemplaza. */}
            <div className="relative">
              <FirmaCanvas
                ref={canvasUsuarioRef}
                onTrazosCambian={setUsuarioVacio}
                fondo={fondoUsuario}
                proporcion={proporcionUsuario}
              />
              {cargandoFondo && (
                <div className="absolute inset-0 flex items-center justify-center gap-2 rounded-md bg-muted/90 text-sm text-muted-foreground">
                  <Loader2Icon className="size-4 animate-spin" />
                  Cargando la raya de firma…
                </div>
              )}
            </div>
          </div>

          <div className={paso === 3 ? "space-y-2" : "hidden"}>
            <p className="text-sm font-medium">3. Firma del administrador (LACS)</p>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="firma-firmado-por">Nombre de quien entrega/recibe por la LACS</Label>
                <Input
                  id="firma-firmado-por"
                  value={firmadoPor}
                  onChange={(e) => setFirmadoPor(e.target.value)}
                  placeholder="Nombre completo"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="firma-cedula-admin">Cédula de quien firma por la LACS (opcional)</Label>
                <Input
                  id="firma-cedula-admin"
                  value={cedulaAdmin}
                  onChange={(e) => setCedulaAdmin(e.target.value)}
                  placeholder="N.º de cédula"
                  inputMode="numeric"
                />
              </div>
            </div>

            <div className="relative">
              <FirmaCanvas
                ref={canvasAdminRef}
                onTrazosCambian={setAdminVacio}
                fondo={fondoAdmin}
                proporcion={proporcionAdmin}
              />
              {cargandoFondo && (
                <div className="absolute inset-0 flex items-center justify-center gap-2 rounded-md bg-muted/90 text-sm text-muted-foreground">
                  <Loader2Icon className="size-4 animate-spin" />
                  Cargando la raya de firma…
                </div>
              )}
            </div>
          </div>

          <div className={paso === 4 ? "space-y-2" : "hidden"}>
            <p className="text-sm font-medium">4. Confirmar</p>
            <div className="h-80 overflow-hidden rounded-md border bg-muted">
              {url ? (
                <iframe
                  // Sin `sandbox`, por el mismo motivo que el preview de
                  // BotonActaPdf: el atributo bloquea el visor de PDF del
                  // navegador y el recuadro sale vacío, sin error en consola.
                  src={`${url}#toolbar=0&navpanes=0&view=FitH`}
                  title="Vista previa del acta firmada"
                  className="size-full"
                />
              ) : (
                <p className="flex size-full items-center justify-center text-sm text-muted-foreground">
                  Componiendo el PDF…
                </p>
              )}
            </div>
            <p className="rounded-md border border-dashed border-warning bg-warning/5 px-3 py-2 text-sm">
              Al guardar, la reserva N.º {grupoId} pasa a <strong>{etiquetaEstado(resultado)}</strong>.
              <br />
              {/* Para que quien firma vea exactamente qué fecha y hora van a
                  quedar impresas -- es la fecha CONGELADA al abrir el
                  diálogo (fechaPeticion), no la del reloj en este instante. */}
              {/* Sin punto final: el formato es-CO ya termina en "p. m." y
                  se leía "3:41 p. m..". */}
              Fecha y hora que quedan impresas: <strong>{formatearFechaActa(fechaPeticion)}</strong>
            </p>
            {noDevueltos.length > 0 && (
              <p className="rounded-md border border-destructive bg-destructive/5 px-3 py-2 text-sm text-destructive-text">
                Sin marcar, quedan registrados como <strong>no devueltos</strong>:{" "}
                {noDevueltos.map((e) => e.nombre).join(", ")}. Esos activos pasan a "No devuelto" y no se
                podrán reservar hasta cambiarlos en Activos.
              </p>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={intentarCerrar} disabled={guardando}>
            Cancelar
          </Button>
          {paso > 1 && (
            <Button
              type="button"
              variant="outline"
              onClick={() => setPaso((p) => (p - 1) as Paso)}
              disabled={generando || guardando}
            >
              Atrás
            </Button>
          )}
          {paso === 1 && (
            <Button type="button" onClick={irAPaso2} disabled={marcados.size === 0 && momento === "entrega"}>
              Siguiente
            </Button>
          )}
          {paso === 2 && (
            <Button type="button" onClick={irAPaso3} disabled={usuarioVacio}>
              Siguiente
            </Button>
          )}
          {paso === 3 && (
            <Button type="button" onClick={irAPaso4} disabled={adminVacio || !firmadoPor.trim() || generando}>
              {generando ? "Componiendo…" : "Siguiente"}
            </Button>
          )}
          {paso === 4 && (
            <Button type="button" onClick={confirmar} disabled={!pdfFirmado || guardando}>
              {guardando ? "Guardando…" : "Guardar acta"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <Dialog open={confirmandoDescarte} onOpenChange={setConfirmandoDescarte}>
      <DialogContent showCloseButton={false} className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>¿Descartar las firmas?</DialogTitle>
          <DialogDescription>
            Se borran las firmas y lo que llevas del acta. La reserva no cambia de estado.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => setConfirmandoDescarte(false)}>
            Seguir firmando
          </Button>
          <Button type="button" variant="destructive" onClick={resetearYCerrar}>
            Descartar firmas
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
    </>
  )
}
