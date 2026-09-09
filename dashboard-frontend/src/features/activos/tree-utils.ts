import type { EstadoRecurso, Recurso } from "@/lib/api"

export function hijosDe(padreId: number, lista: Recurso[]) {
  return lista.filter((r) => r.padre_id === padreId)
}

export function descendientesDe(id: number, lista: Recurso[]): Recurso[] {
  const directos = hijosDe(id, lista)
  let todos = [...directos]
  directos.forEach((h) => {
    todos = todos.concat(descendientesDe(h.id, lista))
  })
  return todos
}

export const GRUPO_TODOS = "__todos__"
export const GRUPO_SIN_CATEGORIA = "__sin_categoria__"

export function grupoDe(r: Recurso) {
  return r.categoria || GRUPO_SIN_CATEGORIA
}

export function etiquetaGrupo(clave: string) {
  if (clave === GRUPO_SIN_CATEGORIA) return "Sin categoría"
  return clave.charAt(0).toUpperCase() + clave.slice(1)
}

export function gruposDisponibles(lista: Recurso[]) {
  const claves = Array.from(new Set(lista.map(grupoDe)))
  claves.sort((a, b) => etiquetaGrupo(a).localeCompare(etiquetaGrupo(b), "es"))
  return claves
}

export const ESTADOS_ACTIVO: Record<
  EstadoRecurso,
  { texto: string; variant: "success" | "warning" | "destructive" }
> = {
  disponible: { texto: "Disponible", variant: "success" },
  en_mantenimiento: { texto: "En mantenimiento", variant: "warning" },
  no_disponible: { texto: "No disponible", variant: "destructive" },
}
