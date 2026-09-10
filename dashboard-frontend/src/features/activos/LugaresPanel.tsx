import { useEffect, useState } from "react"
import { ChevronDown, ChevronRight } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import type { Recurso } from "@/lib/api"

import { ActivoCard } from "./ActivoCard"
import { ActivoNuevoForm } from "./ActivoNuevoForm"
import { useActivosContext } from "./activos-context"
import { contenidosEn, raicesEn } from "./tree-utils"

// El árbol por padre_id de arriba (ActivoCard) usa el estado compartido
// `expandidos` del contexto. Los "contenidos" son otra jerarquía
// (espacio_id) y no deben compartir ese estado -- si lo hicieran, expandir
// los contenidos de un lugar expandiría también sus hijos por padre_id (o
// viceversa). Por eso este componente lleva su propio useState.
function LugarConContenidos({
  lugar,
  lista,
  activos,
  nivel,
}: {
  lugar: Recurso
  lista: Recurso[]
  activos: Recurso[]
  nivel: number
}) {
  const [expandido, setExpandido] = useState(false)
  // El contador y el botón cuentan TODOS los contenidos (incluye hijos
  // anidados) porque al usuario le interesa cuántas cosas hay ahí, no
  // cuántas ramas. El renderizado, en cambio, solo recorre las raíces --
  // cada `ActivoCard` ya sabe encontrar y anidar sus propios hijos dentro
  // de `contenidos` (se le sigue pasando la lista completa como `lista`).
  const contenidos = contenidosEn(lugar.id, activos)
  const raices = raicesEn(lugar.id, activos)

  return (
    <div>
      <ActivoCard recurso={lugar} lista={lista} nivel={nivel} />
      {contenidos.length > 0 && (
        <div className="mb-2" style={{ marginLeft: `${nivel * 1.25 + 1}rem` }}>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-7 gap-1 px-2 text-xs text-muted-foreground"
            onClick={() => setExpandido((v) => !v)}
          >
            {expandido ? (
              <ChevronDown className="size-3.5" />
            ) : (
              <ChevronRight className="size-3.5" />
            )}
            {contenidos.length} activo{contenidos.length === 1 ? "" : "s"} aquí
          </Button>
          {expandido && (
            <div className="mt-1 space-y-1">
              {raices.map((c) => (
                <ActivoCard key={c.id} recurso={c} lista={contenidos} nivel={0} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export function LugaresPanel() {
  const { activos, error, reload, crear } = useActivosContext()
  const [mostrarNuevo, setMostrarNuevo] = useState(false)

  useEffect(() => {
    reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Un lugar no va dentro de otro ni tiene padre (CHECK
  // recursos_espacio_sin_padre_ni_lugar): todos son de primer nivel.
  const espacios = activos.filter((r) => r.tipo === "espacio")

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
          espacios={[]}
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
        {espacios.map((r) => (
          <LugarConContenidos key={r.id} lugar={r} lista={espacios} activos={activos} nivel={0} />
        ))}
      </div>
    </div>
  )
}
