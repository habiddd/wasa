import { useState } from "react"
import { toast } from "sonner"
import { Download, FileUp, Upload } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { importarActivos, type ErrorImportacion, URL_PLANTILLA_ACTIVOS } from "@/lib/api"
import { cn } from "@/lib/utils"

type Formato = keyof typeof URL_PLANTILLA_ACTIVOS

const FORMATOS: Record<Formato, { nombre: string; resumen: string; accept: string; notas: string[] }> = {
  xlsm: {
    nombre: "Excel (.xlsm)",
    resumen: "Con formulario y botones, o escribiendo directo en la tabla. Usa macros.",
    // .xlsx también: alguien puede guardar la plantilla sin macros.
    accept: ".xlsm,.xlsx",
    notas: [
      "Llena el formulario de la primera hoja y un botón pasa los datos a la hoja Activos, o escribe directo en esa hoja.",
      "Desde la primera hoja también agregas categorías y subcategorías nuevas a los desplegables.",
      "Usa macros: al abrirlo, pulsa «Habilitar contenido».",
    ],
  },
  csv: {
    nombre: "CSV (.csv)",
    resumen: "Una fila por activo, sin macros. Se abre en Excel o en cualquier editor.",
    accept: ".csv",
    notas: ["Una fila por activo o lugar. tipo: equipo o espacio."],
  },
}

export function ImportarActivosDialog({
  onClose,
  onImportado,
}: {
  onClose: () => void
  onImportado: () => void
}) {
  // Primero se elige el formato; la plantilla y la subida salen después.
  const [formato, setFormato] = useState<Formato | null>(null)
  const [archivo, setArchivo] = useState<File | null>(null)
  const [importando, setImportando] = useState(false)
  const [errores, setErrores] = useState<ErrorImportacion[]>([])
  const [arrastrando, setArrastrando] = useState(false)

  function elegir(nuevo: Formato | null) {
    setFormato(nuevo)
    setArchivo(null)
    setErrores([])
  }

  // Llega del selector o de soltar un archivo. El `accept` del input no
  // filtra lo que se suelta, así que la extensión se revisa acá.
  function tomar(nuevo: File | undefined) {
    if (!nuevo || !formato) return
    const extensiones = FORMATOS[formato].accept.split(",")
    if (!extensiones.some((ext) => nuevo.name.toLowerCase().endsWith(ext))) {
      toast.error(`Ese archivo no sirve aquí: debe ser ${extensiones.join(" o ")}.`)
      return
    }
    setArchivo(nuevo)
    setErrores([])
  }

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

        {formato === null ? (
          <div className="grid gap-3 sm:grid-cols-2">
            {(Object.keys(FORMATOS) as Formato[]).map((f) => (
              <Button
                key={f}
                variant="outline"
                className="h-auto flex-col items-start gap-1 whitespace-normal p-3 text-left"
                onClick={() => elegir(f)}
              >
                <span className="font-medium">{FORMATOS[f].nombre}</span>
                <span className="text-xs font-normal text-muted-foreground">{FORMATOS[f].resumen}</span>
              </Button>
            ))}
          </div>
        ) : (

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2 rounded-md border p-3">
                <p className="text-sm font-medium">1. Descarga la plantilla</p>
                <Button variant="outline" size="sm" asChild>
                  <a href={URL_PLANTILLA_ACTIVOS[formato]} download>
                    <Download />
                    {FORMATOS[formato].nombre}
                  </a>
                </Button>
                <ul className="list-disc space-y-1 pl-4 text-xs text-muted-foreground">
                  {FORMATOS[formato].notas.map((nota) => (
                    <li key={nota}>{nota}</li>
                  ))}
                  <li>
                    Hijo: escribe en placa_padre la placa de su padre. Lugar: el nombre del lugar.
                    Ambos pueden existir ya o venir en el mismo archivo.
                  </li>
                </ul>
              </div>

              <div className="flex flex-col gap-2 rounded-md border p-3">
                <p className="text-sm font-medium">2. Sube el archivo lleno</p>
                {/* El label ocupa todo el alto libre: clic abre el selector y
                    también se puede soltar el archivo encima. */}
                <label
                  onDragOver={(e) => {
                    e.preventDefault()
                    setArrastrando(true)
                  }}
                  onDragLeave={() => setArrastrando(false)}
                  onDrop={(e) => {
                    e.preventDefault()
                    setArrastrando(false)
                    if (!importando) tomar(e.dataTransfer.files[0])
                  }}
                  className={cn(
                    "flex min-h-36 flex-1 cursor-pointer flex-col items-center justify-center gap-1 rounded-md border border-dashed p-4 text-center text-sm transition-colors focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50 hover:bg-muted/50",
                    arrastrando && "border-ring bg-muted",
                    importando && "pointer-events-none opacity-50"
                  )}
                >
                  <input
                    type="file"
                    className="sr-only"
                    accept={FORMATOS[formato].accept}
                    disabled={importando}
                    onChange={(e) => {
                      tomar(e.target.files?.[0])
                      // Permite volver a elegir el mismo archivo tras corregirlo.
                      e.target.value = ""
                    }}
                  />
                  <FileUp className="size-6 text-muted-foreground" aria-hidden />
                  {archivo ? (
                    <>
                      <span className="font-medium break-all">{archivo.name}</span>
                      <span className="text-xs text-muted-foreground">
                        Arrastra otro o haz clic para cambiarlo
                      </span>
                    </>
                  ) : (
                    <>
                      <span className="font-medium">Arrastra el archivo aquí</span>
                      <span className="text-xs text-muted-foreground">
                        o haz clic para buscarlo ({FORMATOS[formato].accept.replaceAll(",", ", ")})
                      </span>
                    </>
                  )}
                </label>
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

        )}

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
          {formato !== null && (
            <Button variant="ghost" onClick={() => elegir(null)} disabled={importando}>
              Cambiar formato
            </Button>
          )}
          <Button variant="outline" onClick={onClose} disabled={importando}>
            Cerrar
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
