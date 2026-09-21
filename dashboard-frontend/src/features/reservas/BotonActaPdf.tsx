import { useState, type MouseEvent } from "react"
import { FileText } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { descargarActaPdf } from "@/lib/api"

/**
 * Botón "Descargar acta" de una fila de reserva. Vive aparte de
 * ReservasPanel porque tanto la tabla como la vista de tarjetas lo necesitan
 * y las dos filas ya son clickeables (abren el modal de detalle): el
 * `stopPropagation` de acá es lo que evita que el clic en el botón también
 * dispare la apertura del modal.
 */
export function BotonActaPdf({ grupoId }: { grupoId: number | null }) {
  const [descargando, setDescargando] = useState(false)

  if (grupoId === null) return null
  // Capturado en un const aparte: así el closure de abajo conserva el tipo
  // ya angosto a `number` sin depender de que TS reanalice `grupoId` (prop,
  // no una const) dentro de la función anidada.
  const id = grupoId

  async function descargar(e: MouseEvent<HTMLButtonElement>) {
    e.stopPropagation()
    setDescargando(true)
    try {
      const blob = await descargarActaPdf(id)
      const url = URL.createObjectURL(blob)
      const enlace = document.createElement("a")
      enlace.href = url
      enlace.download = `reserva-${id}.pdf`
      document.body.appendChild(enlace)
      enlace.click()
      enlace.remove()
      URL.revokeObjectURL(url)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setDescargando(false)
    }
  }

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-sm"
      aria-label="Descargar acta de entrega y devolución"
      title="Descargar acta de entrega y devolución"
      disabled={descargando}
      onClick={descargar}
      onKeyDown={(e) => {
        // La fila entera reacciona a Enter/espacio para abrir el modal (ver
        // TablaReservas): frena esa propagación igual que en el clic.
        if (e.key === "Enter" || e.key === " ") e.stopPropagation()
      }}
    >
      <FileText />
    </Button>
  )
}
