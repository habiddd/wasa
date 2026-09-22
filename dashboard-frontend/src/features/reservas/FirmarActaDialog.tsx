import { useEffect, useRef, useState } from "react"
import { Loader2Icon } from "lucide-react"
import { toast } from "sonner"

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
import { etiquetaEstado } from "@/features/calendario/calendario-utils"
import { registrarActa, type AnclaFirma, type MomentoActa, type Sesion } from "@/lib/api"

import { estadoResultante, estamparFirmas, formatearFechaActa } from "./actas-pdf"
import { FirmaCanvas, type FirmaCanvasHandle } from "./FirmaCanvas"
import { recortarCuadro } from "./recorte-acta"

type Paso = 1 | 2 | 3

export function FirmarActaDialog({
  abierto,
  onOpenChange,
  grupoId,
  momento,
  anclas,
  pdfBase,
  usuarioNombre,
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
  /** Fin de la reserva -- para anunciar en el paso 3 si una devolución llega
   * a tiempo (completada) o tarde (devuelta_tarde). Ver actas-pdf.estadoResultante. */
  fin: Date
  onRegistrada: (reservas: Sesion[]) => void
}) {
  const [paso, setPaso] = useState<Paso>(1)
  const [firmadoPor, setFirmadoPor] = useState("")
  const [cedulaAdmin, setCedulaAdmin] = useState("")
  const [usuarioVacio, setUsuarioVacio] = useState(true)
  const [adminVacio, setAdminVacio] = useState(true)
  const [generando, setGenerando] = useState(false)
  const [guardando, setGuardando] = useState(false)
  const [pdfFirmado, setPdfFirmado] = useState<Uint8Array | null>(null)
  const [url, setUrl] = useState<string | null>(null)

  // Fecha y hora que va a quedar impresa en los dos cuadros de este momento
  // -- se congela al ABRIR el diálogo, no al componer el PDF (irAPaso3),
  // porque el usuario pidió que sea "la fecha cuando se crea la petición de
  // hacer la firma", no la del rato después en que se termina de firmar.
  // Reiniciada cada vez que `abierto` pasa a true (ver el efecto de abajo) --
  // el componente puede seguir montado entre una firma cancelada y la
  // siguiente apertura dentro de la misma sesión de BotonActaPdf.
  const [fechaPeticion, setFechaPeticion] = useState(() => new Date())
  useEffect(() => {
    if (abierto) setFechaPeticion(new Date())
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
  // los tres pasos están siempre montados, solo se ocultan con `hidden`),
  // así que "Atrás" no pierde lo ya firmado dentro de una misma apertura.
  function resetearYCerrar() {
    setPaso(1)
    setFirmadoPor("")
    setCedulaAdmin("")
    setUsuarioVacio(true)
    setAdminVacio(true)
    setPdfFirmado(null)
    canvasUsuarioRef.current?.limpiar()
    canvasAdminRef.current?.limpiar()
    onOpenChange(false)
  }

  async function irAPaso2() {
    if (canvasUsuarioRef.current?.estaVacio()) {
      toast.error("Falta la firma del usuario.")
      return
    }
    setPaso(2)
  }

  async function irAPaso3() {
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
        cedulaAdmin
      )
      setPdfFirmado(bytes)
      setPaso(3)
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
      })
      toast.success("Acta registrada.")
      onRegistrada(reservas)
      resetearYCerrar()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setGuardando(false)
    }
  }

  const resultado = estadoResultante(momento, fin)
  const tituloDialogo = momento === "entrega" ? "Acta de entrega" : "Acta de devolución"

  return (
    <Dialog open={abierto} onOpenChange={(v) => !v && resetearYCerrar()}>
      <DialogContent className="flex max-h-[90vh] flex-col sm:max-w-lg">
        {/* Ancho normal (no el sm:max-w-xl que se necesitó cuando el lienzo
            cubría el cuadro completo del acta): la franja de firma es mucho
            más apaisada, el lienzo mapea 1:1 con `aspect-ratio` y queda bajo
            incluso a este ancho. El `overflow-y-auto` de abajo sigue ahí
            como red de seguridad si una pantalla chica lo necesita. */}
        <DialogHeader>
          <DialogTitle>{tituloDialogo}</DialogTitle>
          <DialogDescription>
            Reserva N.º {grupoId} · {usuarioNombre} · Paso {paso} de 3
          </DialogDescription>
        </DialogHeader>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto">
          {/* Los tres pasos quedan siempre montados (solo se ocultan con
              `hidden`): así "Atrás" no borra los trazos ya dibujados, que
              viven dentro del estado interno de cada FirmaCanvas. */}
          <div className={paso === 1 ? "space-y-2" : "hidden"}>
            <p className="text-sm font-medium">1. Firma del usuario</p>
            <p className="text-sm text-muted-foreground">
              {usuarioNombre}, titular de la reserva N.º {grupoId}.
            </p>
            {/* El lienzo queda siempre montado debajo (ver el comentario de
                arriba sobre los tres pasos) -- el overlay de carga solo lo
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

          <div className={paso === 2 ? "space-y-2" : "hidden"}>
            <p className="text-sm font-medium">2. Firma del administrador (LACS)</p>
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

          <div className={paso === 3 ? "space-y-2" : "hidden"}>
            <p className="text-sm font-medium">3. Confirmar</p>
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
          </div>
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={resetearYCerrar} disabled={guardando}>
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
            <Button type="button" onClick={irAPaso2} disabled={usuarioVacio}>
              Siguiente
            </Button>
          )}
          {paso === 2 && (
            <Button type="button" onClick={irAPaso3} disabled={adminVacio || !firmadoPor.trim() || generando}>
              {generando ? "Componiendo…" : "Siguiente"}
            </Button>
          )}
          {paso === 3 && (
            <Button type="button" onClick={confirmar} disabled={!pdfFirmado || guardando}>
              {guardando ? "Guardando…" : "Guardar acta"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
