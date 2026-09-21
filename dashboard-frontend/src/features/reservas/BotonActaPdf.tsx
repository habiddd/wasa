import { useEffect, useState, type MouseEvent } from "react"
import { Download, FileText, Loader2Icon } from "lucide-react"
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
import { descargarActaPdf } from "@/lib/api"

/**
 * Botón "Acta": abre un modal con la vista previa del PDF y, desde ahí, se
 * descarga. Vive aparte de ReservasPanel porque lo usan tres sitios -- la
 * tabla, las tarjetas y el pie del detalle de la reserva (ReservaDetalle, que
 * es el mismo modal en Calendario y en Reservas).
 *
 * `variante` es solo presentación: en las filas es un icono suelto, y en el
 * pie del detalle un botón con texto como los que ya están ahí.
 */
export function BotonActaPdf({
  grupoId,
  variante = "icono",
}: {
  grupoId: number | null
  variante?: "icono" | "boton"
}) {
  const [abierto, setAbierto] = useState(false)
  const [pdf, setPdf] = useState<Blob | null>(null)
  const [url, setUrl] = useState<string | null>(null)
  const [cargando, setCargando] = useState(false)

  // El object URL se ata al blob y no al clic: así se revoca solo cuando
  // llega otro, cuando se cierra el modal (`setPdf(null)`) y cuando la fila se
  // desmonta -- el panel recarga cada 30 s --, sin depender de que el cierre
  // ocurra después de que terminó la descarga.
  useEffect(() => {
    if (!pdf) {
      setUrl(null)
      return
    }
    const nueva = URL.createObjectURL(pdf)
    setUrl(nueva)
    return () => URL.revokeObjectURL(nueva)
  }, [pdf])

  if (grupoId === null) return null
  // Capturado en un const aparte: así el closure de abajo conserva el tipo
  // ya angosto a `number` sin depender de que TS reanalice `grupoId` (prop,
  // no una const) dentro de la función anidada.
  const id = grupoId

  async function abrir(e: MouseEvent<HTMLButtonElement>) {
    e.stopPropagation()
    setAbierto(true)
    setCargando(true)
    try {
      setPdf(await descargarActaPdf(id))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
      setAbierto(false)
    } finally {
      setCargando(false)
    }
  }

  // Al cerrar se suelta el blob: el acta se vuelve a pedir en la siguiente
  // apertura, que es lo correcto si mientras tanto cambió la reserva.
  function cambiarApertura(siguiente: boolean) {
    setAbierto(siguiente)
    if (!siguiente) setPdf(null)
  }

  function descargar() {
    if (!url) return
    const enlace = document.createElement("a")
    enlace.href = url
    enlace.download = `reserva-${id}.pdf`
    document.body.appendChild(enlace)
    enlace.click()
    enlace.remove()
  }

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
        {variante === "boton" && "Ver acta"}
      </Button>

      <Dialog open={abierto} onOpenChange={cambiarApertura}>
        <DialogContent className="flex h-[85vh] flex-col sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>Acta de entrega y devolución</DialogTitle>
            <DialogDescription>Reserva N.º {id}</DialogDescription>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-hidden rounded-md border bg-muted">
            {url ? (
              <iframe
                // `toolbar=0` esconde la barra del visor propio del navegador,
                // que duplicaría el botón de descarga de abajo. Firefox la
                // ignora, y tampoco pasa nada.
                src={`${url}#toolbar=0&navpanes=0&view=FitH`}
                title={`Acta de la reserva N.º ${id}`}
                className="size-full"
              />
            ) : (
              <p className="flex size-full items-center justify-center gap-2 text-sm text-muted-foreground">
                <Loader2Icon className="size-4 animate-spin" />
                Generando el acta…
              </p>
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
    </span>
  )
}
