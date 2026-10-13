import { useEffect, useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

import { ActivoCard } from "./ActivoCard"
import { ActivoNuevoForm } from "./ActivoNuevoForm"
import { ImportarActivosDialog } from "./ImportarActivosDialog"
import { useActivosContext } from "./activos-context"
import {
  descendientesDe,
  etiquetaGrupo,
  GRUPO_TODOS,
  grupoDe,
  gruposDisponibles,
  subgrupoDe,
  subgruposDisponibles,
} from "./tree-utils"

export function ActivosPanel() {
  const {
    activos,
    error,
    reload,
    crear,
    subtabActiva,
    setSubtabActiva,
    subsubtabActiva,
    setSubsubtabActiva,
  } = useActivosContext()
  const [mostrarNuevo, setMostrarNuevo] = useState(false)
  const [importando, setImportando] = useState(false)

  useEffect(() => {
    reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const equipos = activos.filter((r) => r.tipo === "equipo")
  // El selector "Lugar / espacio" del alta necesita TODOS los espacios, no
  // solo `equipos` (que aquí ya viene filtrado por tipo='equipo').
  const espacios = activos.filter((r) => r.tipo === "espacio")
  const grupos = gruposDisponibles(equipos)
  const claves = [GRUPO_TODOS, ...grupos]
  const subtabEfectiva = subtabActiva !== GRUPO_TODOS && !grupos.includes(subtabActiva) ? GRUPO_TODOS : subtabActiva

  // Fila 2 (subcategorías): solo entre los equipos de la categoría ya
  // seleccionada -- si la categoría está en "Todos", entre todos los
  // equipos. Si la subcategoría activa no existe dentro de ese subconjunto
  // (p. ej. porque se acaba de cambiar de categoría), cae a "Todos" -- mismo
  // criterio que `subtabEfectiva` arriba.
  const equiposDeCategoria =
    subtabEfectiva === GRUPO_TODOS ? equipos : equipos.filter((r) => grupoDe(r) === subtabEfectiva)
  const subgrupos = subgruposDisponibles(equiposDeCategoria)
  const subclaves = [GRUPO_TODOS, ...subgrupos]
  const subsubtabEfectiva =
    subsubtabActiva !== GRUPO_TODOS && !subgrupos.includes(subsubtabActiva) ? GRUPO_TODOS : subsubtabActiva

  const matchFiltro = (r: (typeof equipos)[number]) =>
    (subtabEfectiva === GRUPO_TODOS || grupoDe(r) === subtabEfectiva) &&
    (subsubtabEfectiva === GRUPO_TODOS || subgrupoDe(r) === subsubtabEfectiva)

  let raiz = equipos.filter((r) => r.padre_id === null)
  let listaParaHijos = equipos

  if (subtabEfectiva !== GRUPO_TODOS || subsubtabEfectiva !== GRUPO_TODOS) {
    const directos = equipos.filter(matchFiltro)
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
        <h1 className="text-xl font-semibold tracking-tight">
          Equipos y relaciones
        </h1>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={() => setMostrarNuevo((v) => !v)}>
            + Agregar activo
          </Button>
          <Button size="sm" variant="outline" onClick={() => setImportando(true)}>
            Importar
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
          espacios={espacios}
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

      {importando && <ImportarActivosDialog onClose={() => setImportando(false)} onImportado={() => reload()} />}

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
              aria-pressed={clave === subtabEfectiva}
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

      {subclaves.length > 1 && (
        <div className="ml-4 flex flex-wrap gap-1 border-l pl-3">
          {subclaves.map((clave) => {
            const total =
              clave === GRUPO_TODOS
                ? equiposDeCategoria.length
                : equiposDeCategoria.filter((r) => subgrupoDe(r) === clave).length
            const etiqueta = clave === GRUPO_TODOS ? "Todas" : etiquetaGrupo(clave)
            return (
              <button
                key={clave}
                type="button"
                onClick={() => setSubsubtabActiva(clave)}
                aria-pressed={clave === subsubtabEfectiva}
                className={cn(
                  "rounded-md border px-2 py-0.5 text-xs",
                  clave === subsubtabEfectiva
                    ? "border-primary/70 bg-primary/10 text-primary"
                    : "text-muted-foreground hover:border-primary/70"
                )}
              >
                {etiqueta} <span className="opacity-70">{total}</span>
              </button>
            )
          })}
        </div>
      )}

      {error && !activos.length && (
        <p className="text-sm text-destructive-text">No se pudieron cargar los activos.</p>
      )}
      {!error && equipos.length === 0 && <p className="text-sm text-muted-foreground">No hay equipos registrados.</p>}
      {equipos.length > 0 &&
        raiz.length === 0 &&
        (subtabEfectiva !== GRUPO_TODOS || subsubtabEfectiva !== GRUPO_TODOS) && (
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
