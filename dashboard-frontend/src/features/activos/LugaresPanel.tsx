import { useEffect, useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"

import { ActivoCard } from "./ActivoCard"
import { ActivoNuevoForm } from "./ActivoNuevoForm"
import { useActivosContext } from "./activos-context"

export function LugaresPanel() {
  const { activos, error, reload, crear } = useActivosContext()
  const [mostrarNuevo, setMostrarNuevo] = useState(false)

  useEffect(() => {
    reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const espacios = activos.filter((r) => r.tipo === "espacio")
  const raiz = espacios.filter((r) => r.padre_id === null)

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Lugares
        </h2>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={() => setMostrarNuevo((v) => !v)}>
            + Agregar lugar
          </Button>
          <Button size="sm" variant="outline" onClick={() => reload()}>
            Refrescar
          </Button>
        </div>
      </div>
      <p className="-mt-2 text-xs text-muted-foreground">
        Espacios reservables (salas, estudios, etc.). Los datos no se refrescan solos en esta
        pestaña — usa "Refrescar".
      </p>

      {mostrarNuevo && (
        <ActivoNuevoForm
          tipo="espacio"
          equipos={[]}
          onCancel={() => setMostrarNuevo(false)}
          onCreate={async (cambios) => {
            try {
              await crear(cambios)
              setMostrarNuevo(false)
            } catch (err) {
              toast.error(err instanceof Error ? err.message : String(err))
              throw err
            }
          }}
        />
      )}

      {error && !activos.length && (
        <p className="text-sm text-destructive">No se pudieron cargar los lugares.</p>
      )}
      {!error && espacios.length === 0 && (
        <p className="text-sm text-muted-foreground">No hay lugares registrados.</p>
      )}

      <div>
        {raiz.map((r) => (
          <ActivoCard key={r.id} recurso={r} lista={espacios} nivel={0} />
        ))}
      </div>
    </div>
  )
}
