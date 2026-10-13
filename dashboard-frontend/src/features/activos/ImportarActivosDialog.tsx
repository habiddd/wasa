import { useState } from "react"
import { toast } from "sonner"
import { Download, Upload } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { importarActivos, type ErrorImportacion, URL_PLANTILLA_ACTIVOS } from "@/lib/api"

export function ImportarActivosDialog({
  onClose,
  onImportado,
}: {
  onClose: () => void
  onImportado: () => void
}) {
  const [archivo, setArchivo] = useState<File | null>(null)
  const [importando, setImportando] = useState(false)
  const [errores, setErrores] = useState<ErrorImportacion[]>([])

  async function importar() {
    if (!archivo) return
    setImportando(true)
    try {
      const resultado = await importarActivos(archivo)
      if (resultado.errores.length) {
        setErrores(resultado.errores)
      } else {
        toast.success(`Se importaron ${resultado.creados} activo${resultado.creados === 1 ? "" : "s"}.`)
        onImportado()
        onClose()
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setImportando(false)
    }
  }

  return (
    <Dialog open onOpenChange={(abierto) => !abierto && !importando && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Importar activos</DialogTitle>
          <DialogDescription>
            Solo agrega activos nuevos: no modifica ni borra los que ya existen. Si alguna fila
            tiene un error, no se importa ninguna.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 sm:grid-cols-2">
          {/* Descarga la plantilla */}
          <div className="rounded-md border p-3 space-y-2">
            <p className="text-sm font-medium">1. Descarga la plantilla</p>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" size="sm" asChild>
                <a href={URL_PLANTILLA_ACTIVOS.xlsx} download>
                  <Download />
                  Excel (.xlsx)
                </a>
              </Button>
              <Button variant="outline" size="sm" asChild>
                <a href={URL_PLANTILLA_ACTIVOS.csv} download>
                  <Download />
                  CSV (.csv)
                </a>
              </Button>
            </div>
            <ul className="list-disc space-y-1 pl-4 text-xs text-muted-foreground">
              <li>Una fila por activo o lugar. tipo: equipo o espacio.</li>
              <li>Hijo: escribe en placa_padre la placa de su padre (ya creado o en el mismo archivo).</li>
              <li>Lugar: escribe el nombre del lugar donde está (ya creado o en el mismo archivo).</li>
              <li>La plantilla de Excel trae las instrucciones de cada columna y la lista de lugares.</li>
            </ul>
          </div>

          {/* Sube el archivo */}
          <div className="rounded-md border p-3 space-y-2">
            <p className="text-sm font-medium">2. Sube el archivo lleno</p>
            <Input
              type="file"
              accept=".xlsx,.csv"
              onChange={(e) => {
                setArchivo(e.target.files?.[0] ?? null)
                setErrores([])
              }}
              disabled={importando}
            />
            <Button
              size="sm"
              onClick={importar}
              disabled={!archivo || importando}
              className="w-full"
            >
              <Upload />
              {importando ? "Importando…" : "Importar"}
            </Button>
          </div>
        </div>

        {/* Errores */}
        {errores.length > 0 && (
          <div className="rounded-md border border-destructive/40 p-3">
            <p className="text-sm font-medium text-destructive-text">
              No se importó ningún activo. Corrige estas filas y vuelve a subir el archivo:
            </p>
            <ul className="mt-2 max-h-56 space-y-0.5 overflow-y-auto text-xs">
              {errores.map((e, i) => (
                <li key={`${e.fila}-${i}`}>
                  <span className="font-medium tabular-nums">Fila {e.fila}:</span> {e.mensaje}
                </li>
              ))}
            </ul>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={importando}>
            Cerrar
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
