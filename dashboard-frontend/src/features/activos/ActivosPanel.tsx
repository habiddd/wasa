import { useEffect, useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

import { ActivoCard } from "./ActivoCard"
import { ActivoNuevoForm } from "./ActivoNuevoForm"
import { useActivosContext } from "./activos-context"
import {
  descendientesDe,
  etiquetaGrupo,
  GRUPO_TODOS,
  grupoDe,
  gruposDisponibles,
} from "./tree-utils"

export function ActivosPanel() {
  const { activos, error, reload, crear, subtabActiva, setSubtabActiva } =
    useActivosContext()
  const [mostrarNuevo, setMostrarNuevo] = useState(false)

  useEffect(() => {
    reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const equipos = activos.filter((r) => r.tipo === "equipo")
  const grupos = gruposDisponibles(equipos)
  const claves = [GRUPO_TODOS, ...grupos]
  const subtabEfectiva = subtabActiva !== GRUPO_TODOS && !grupos.includes(subtabActiva) ? GRUPO_TODOS : subtabActiva

  let raiz = equipos.filter((r) => r.padre_id === null)
  let listaParaHijos = equipos

  if (subtabEfectiva !== GRUPO_TODOS) {
    const directos = equipos.filter((r) => grupoDe(r) === subtabEfectiva)
    const idsDirectos = new Set(directos.map((r) => r.id))
    const visiblesMap = new Map(directos.map((r) => [r.id, r]))
    directos.forEach((r) => {
      descendientesDe(r.id, equipos).forEach((d) => visiblesMap.set(d.id, d))
    })
    listaParaHijos = Array.from(visiblesMap.values())
    raiz = directos.filter((r) => r.padre_id === null || !idsDirectos.has(r.padre_id))
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Equipos y relaciones
        </h2>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={() => setMostrarNuevo((v) => !v)}>
            + Agregar activo
          </Button>
          <Button size="sm" variant="outline" onClick={() => reload()}>
            Refrescar
          </Button>
        </div>
      </div>
      <p className="-mt-2 text-xs text-muted-foreground">
        Los datos no se refrescan solos en esta pestaña para no perder ediciones en curso — usa
        "Refrescar".
      </p>

      {mostrarNuevo && (
        <ActivoNuevoForm
          tipo="equipo"
          equipos={equipos}
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

      <div className="flex flex-wrap gap-1.5">
        {claves.map((clave) => {
          const total =
            clave === GRUPO_TODOS
              ? equipos.length
              : equipos.filter((r) => grupoDe(r) === clave).length
          const etiqueta = clave === GRUPO_TODOS ? "Todos" : etiquetaGrupo(clave)
          return (
            <button
              key={clave}
              type="button"
              onClick={() => setSubtabActiva(clave)}
              className={cn(
                "rounded-full border px-2.5 py-1 text-xs",
                clave === subtabEfectiva
                  ? "border-primary bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:border-primary"
              )}
            >
              {etiqueta} <span className="opacity-70">{total}</span>
            </button>
          )
        })}
      </div>

      {error && !activos.length && (
        <p className="text-sm text-destructive">No se pudieron cargar los activos.</p>
      )}
      {!error && equipos.length === 0 && <p className="text-sm text-muted-foreground">No hay equipos registrados.</p>}
      {equipos.length > 0 && raiz.length === 0 && subtabEfectiva !== GRUPO_TODOS && (
        <p className="text-sm text-muted-foreground">No hay activos en esta categoría.</p>
      )}

      <div>
        {raiz.map((r) => (
          <ActivoCard key={r.id} recurso={r} lista={listaParaHijos} nivel={0} />
        ))}
      </div>
    </div>
  )
}
