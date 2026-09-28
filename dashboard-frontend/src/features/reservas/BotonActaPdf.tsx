import { useEffect, useRef, useState, type ChangeEvent, type MouseEvent } from "react"
import { ChevronDown, Download, FileSignature, FileText, Loader2Icon, Upload } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { etiquetaEstado } from "@/features/calendario/calendario-utils"
import {
  descargarActaHistorica,
  descargarActaPdf,
  obtenerEstadoActa,
  registrarActa,
  type EstadoActa,
  type MomentoActa,
  type Sesion,
} from "@/lib/api"

import { estadoResultante } from "./actas-pdf"
import { FirmarActaDialog } from "./FirmarActaDialog"

/**
 * Espejo barato de `actas_service.momento_siguiente`, SOLO para la etiqueta
 * del botón cuando todavía está cerrado (evita pedirle al backend el estado
 * de cada fila de un listado grande solo para poner un texto). La verdad que
 * decide la acción real la manda `/acta/estado` al abrir el modal
 * (`estadoActa.momento_siguiente`, ver más abajo) -- si algo no cuadra entre
 * las dos, gana siempre la del backend.
 */
function momentoSiguienteAprox(reservas: Sesion[]): MomentoActa | null {
  if (reservas.some((r) => r.estado === "confirmada")) return "entrega"
  if (reservas.some((r) => r.estado === "entregada")) return "devolucion"
  return null
}

function etiquetaBoton(momento: MomentoActa | null) {
  if (momento === "entrega") return "Firmar entrega"
  if (momento === "devolucion") return "Firmar devolución"
  return "Ver acta"
}

const FORMATO_HISTORIAL = new Intl.DateTimeFormat("es-CO", {
  dateStyle: "short",
  timeStyle: "short",
})

function descargarBlob(blob: Blob, nombreArchivo: string) {
  const objectUrl = URL.createObjectURL(blob)
  const enlace = document.createElement("a")
  enlace.href = objectUrl
  enlace.download = nombreArchivo
  document.body.appendChild(enlace)
  enlace.click()
  enlace.remove()
  URL.revokeObjectURL(objectUrl)
}

/**
 * Botón "Acta": abre un modal con la vista previa del PDF vigente, el
 * historial de versiones y, si a la reserva le falta un acta, las dos formas
 * de registrarla (firmar en pantalla o subir un PDF ya firmado). Vive aparte
 * de los paneles porque lo usan varios sitios -- la tabla y las tarjetas de
 * ReservasPanel, y el pie de ReservaDetalle (que es el mismo modal en
 * Calendario y en Reservas).
 *
 * `variante` es solo presentación: en las filas es un icono suelto, y en el
 * pie del detalle un botón con texto que además cambia según lo que falte
 * firmar ("Firmar entrega" / "Firmar devolución" / "Ver acta").
 */
export function BotonActaPdf({
  grupoId,
  reservas,
  usuarioNombre,
  fin,
  variante = "icono",
  onRegistrada,
}: {
  grupoId: number | null
  /** Líneas de la reserva (evento.reservas): de acá sale la etiqueta
   * aproximada del botón cerrado, ver momentoSiguienteAprox. */
  reservas: Sesion[]
  usuarioNombre: string
  fin: Date
  variante?: "icono" | "boton"
  /** Refresca el listado del panel tras registrar un acta (firmada o
   * subida): el estado de la reserva cambió y conviene no esperar al
   * próximo sondeo periódico para que se note en pantalla. */
  onRegistrada?: () => void
}) {
  const [abierto, setAbierto] = useState(false)
  const [pdf, setPdf] = useState<Blob | null>(null)
  const [url, setUrl] = useState<string | null>(null)
  // El PDF base también viaja como ArrayBuffer (además de Blob) porque
  // FirmarActaDialog/actas-pdf.ts (pdf-lib) necesitan bytes, no un Blob --
  // la conversión es asíncrona así que vive en su propio estado derivado.
  const [pdfBytes, setPdfBytes] = useState<ArrayBuffer | null>(null)
  const [estadoActa, setEstadoActa] = useState<EstadoActa | null>(null)
  const [cargando, setCargando] = useState(false)
  const [firmando, setFirmando] = useState(false)
  const [historialAbierto, setHistorialAbierto] = useState(false)
  const [archivoPendiente, setArchivoPendiente] = useState<File | null>(null)
  const [subiendo, setSubiendo] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Mismo patrón que antes: el object URL se ata al blob, no al clic, y se
  // revoca solo cuando llega otro o el modal se cierra.
  useEffect(() => {
    if (!pdf) {
      setUrl(null)
      return
    }
    const nueva = URL.createObjectURL(pdf)
    setUrl(nueva)
    return () => URL.revokeObjectURL(nueva)
  }, [pdf])

  useEffect(() => {
    if (!pdf) {
      setPdfBytes(null)
      return
    }
    let cancelado = false
    pdf.arrayBuffer().then((bytes) => {
      if (!cancelado) setPdfBytes(bytes)
    })
    return () => {
      cancelado = true
    }
  }, [pdf])

  if (grupoId === null) return null
  const id = grupoId

  async function cargarActa() {
    setCargando(true)
    try {
      const [pdfNuevo, estadoNuevo] = await Promise.all([
        descargarActaPdf(id),
        obtenerEstadoActa(id),
      ])
      setPdf(pdfNuevo)
      setEstadoActa(estadoNuevo)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
      setAbierto(false)
    } finally {
      setCargando(false)
    }
  }

  function abrir(e: MouseEvent<HTMLButtonElement>) {
    e.stopPropagation()
    setAbierto(true)
    cargarActa()
  }

  // Al cerrar se suelta todo: la próxima apertura pide el acta de nuevo, que
  // es lo correcto si mientras tanto cambió la reserva.
  function cambiarApertura(siguiente: boolean) {
    setAbierto(siguiente)
    if (!siguiente) {
      setPdf(null)
      setEstadoActa(null)
      setHistorialAbierto(false)
    }
  }

  function descargar() {
    if (!pdf) return
    descargarBlob(pdf, `reserva-${id}.pdf`)
  }

  async function descargarHistorial(actaId: number) {
    try {
      descargarBlob(await descargarActaHistorica(id, actaId), `reserva-${id}-acta-${actaId}.pdf`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  function onArchivoElegido(e: ChangeEvent<HTMLInputElement>) {
    const archivo = e.target.files?.[0]
    e.target.value = "" // permite elegir el mismo archivo otra vez si hace falta reintentar
    if (archivo) setArchivoPendiente(archivo)
  }

  async function confirmarSubida() {
    if (!archivoPendiente || !estadoActa?.momento_siguiente) return
    setSubiendo(true)
    try {
      await registrarActa(id, {
        archivo: archivoPendiente,
        momento: estadoActa.momento_siguiente,
        origen: "subida",
      })
      toast.success("Acta registrada.")
      setArchivoPendiente(null)
      onRegistrada?.()
      await cargarActa()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setSubiendo(false)
    }
  }

  async function firmaRegistrada() {
    onRegistrada?.()
    await cargarActa()
  }

  const etiqueta = variante === "boton" ? etiquetaBoton(momentoSiguienteAprox(reservas)) : undefined

  return (
    // Los eventos de un portal de React burbujean por el árbol de React, no
    // por el DOM: sin este envoltorio, un clic dentro del modal llegaría a la
    // fila y abriría también el detalle de la reserva.
    <span
      className="contents"
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") e.stopPropagation()
      }}
    >
      <Button
        type="button"
        variant={variante === "icono" ? "ghost" : "outline"}
        size={variante === "icono" ? "icon-sm" : "sm"}
        aria-label={variante === "icono" ? "Ver acta de entrega y devolución" : undefined}
        title="Ver acta de entrega y devolución"
        disabled={cargando}
        onClick={abrir}
      >
        <FileText />
        {variante === "boton" && etiqueta}
      </Button>

      <Dialog open={abierto} onOpenChange={cambiarApertura}>
        <DialogContent className="flex h-[85vh] flex-col sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>Acta de entrega y devolución</DialogTitle>
            <DialogDescription>Reserva N.º {id}</DialogDescription>
          </DialogHeader>

          <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto">
            <div className="min-h-64 flex-1 overflow-hidden rounded-md border bg-muted">
              {url ? (
                <iframe
                  // `toolbar=0` esconde la barra del visor propio del
                  // navegador, que duplicaría el botón de descarga de abajo.
                  //
                  // SIN `sandbox`: se probó con `sandbox="allow-same-origin"`
                  // y el visor de PDF del navegador queda bloqueado -- el
                  // recuadro sale vacío con el icono de "contenido bloqueado"
                  // y ni siquiera lanza un error en consola. El atributo
                  // impide cargar el plugin del visor, no solo los scripts
                  // del documento. No hace falta: el blob es del mismo
                  // origen, el visor de PDF ya corre aislado en su propio
                  // proceso, y el dashboard entero está detrás del login.
                  src={`${url}#toolbar=0&navpanes=0&view=FitH`}
                  title={`Acta de la reserva N.º ${id}`}
                  className="size-full"
                />
              ) : (
                <p className="flex size-full items-center justify-center gap-2 text-sm text-muted-foreground">
                  <Loader2Icon className="size-4 animate-spin" />
                  Cargando el acta…
                </p>
              )}
            </div>

            {estadoActa?.momento_siguiente && (
              <div className="flex flex-wrap items-center gap-2 rounded-md border border-dashed border-warning bg-warning/5 px-3 py-2 text-sm">
                <p className="flex-1">
                  Falta el acta de{" "}
                  {estadoActa.momento_siguiente === "entrega" ? "entrega" : "devolución"}.
                </p>
                <Button
                  type="button"
                  size="sm"
                  disabled={!pdfBytes}
                  onClick={() => setFirmando(true)}
                >
                  <FileSignature />
                  Firmar en pantalla
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => fileInputRef.current?.click()}
                >
                  <Upload />
                  Subir PDF firmado
                </Button>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="application/pdf"
                  className="hidden"
                  onChange={onArchivoElegido}
                />
              </div>
            )}

            {estadoActa && estadoActa.historial.length > 0 && (
              <Collapsible open={historialAbierto} onOpenChange={setHistorialAbierto}>
                <CollapsibleTrigger asChild>
                  <Button type="button" variant="ghost" size="sm">
                    <ChevronDown
                      className={`size-4 shrink-0 transition-transform ${historialAbierto ? "rotate-180" : ""}`}
                    />
                    Historial ({estadoActa.historial.length})
                  </Button>
                </CollapsibleTrigger>
                <CollapsibleContent>
                  <ul className="divide-y rounded-md border text-sm">
                    {estadoActa.historial.map((acta) => (
                      <li
                        key={acta.id}
                        className="flex flex-wrap items-center justify-between gap-2 px-3 py-2"
                      >
                        <span className="space-y-0.5">
                          <span className="flex flex-wrap items-center gap-1.5">
                            <Badge variant="outline">
                              {acta.momento === "entrega" ? "Entrega" : "Devolución"}
                            </Badge>
                            <Badge variant="outline">
                              {acta.origen === "firmada" ? "Firmada en pantalla" : "Subida"}
                            </Badge>
                          </span>
                          <span className="block text-xs text-muted-foreground">
                            {acta.creado_en ? FORMATO_HISTORIAL.format(new Date(acta.creado_en)) : "-"}
                            {acta.firmado_por && ` · ${acta.firmado_por}`}
                          </span>
                          {acta.nota && <span className="block text-xs italic text-muted-foreground">{acta.nota}</span>}
                        </span>
                        <Button
                          type="button"
                          size="icon-sm"
                          variant="ghost"
                          aria-label="Descargar esta versión"
                          title="Descargar esta versión"
                          onClick={() => descargarHistorial(acta.id)}
                        >
                          <Download />
                        </Button>
                      </li>
                    ))}
                  </ul>
                </CollapsibleContent>
              </Collapsible>
            )}
          </div>

          <DialogFooter>
            <Button type="button" size="sm" variant="outline" onClick={() => cambiarApertura(false)}>
              Cerrar
            </Button>
            <Button type="button" size="sm" disabled={!url} onClick={descargar}>
              <Download />
              Descargar PDF
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {estadoActa?.momento_siguiente && pdfBytes && (
        <FirmarActaDialog
          abierto={firmando}
          onOpenChange={setFirmando}
          grupoId={id}
          momento={estadoActa.momento_siguiente}
          anclas={estadoActa.anclas}
          pdfBase={pdfBytes}
          usuarioNombre={usuarioNombre}
          fin={fin}
          onRegistrada={firmaRegistrada}
        />
      )}

      <Dialog
        open={archivoPendiente !== null}
        onOpenChange={(v) => {
          if (!v && !subiendo) setArchivoPendiente(null)
        }}
      >
        <DialogContent showCloseButton={false} className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>¿Registrar este PDF como acta?</DialogTitle>
            <DialogDescription>
              {estadoActa?.momento_siguiente &&
                `La reserva N.º ${id} pasará a ${etiquetaEstado(
                  estadoResultante(estadoActa.momento_siguiente, fin)
                )}.`}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setArchivoPendiente(null)}
              disabled={subiendo}
            >
              Cancelar
            </Button>
            <Button type="button" onClick={confirmarSubida} disabled={subiendo}>
              {subiendo ? "Guardando…" : "Registrar acta"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </span>
  )
}
