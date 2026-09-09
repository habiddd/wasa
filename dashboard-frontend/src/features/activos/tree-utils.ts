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

export function contenidosEn(espacioId: number, lista: Recurso[]) {
  return lista.filter((r) => r.espacio_id === espacioId)
}

export function descendientesEspacioDe(id: number, lista: Recurso[]): Recurso[] {
  const directos = contenidosEn(id, lista)
  let todos = [...directos]
  directos.forEach((h) => {
    todos = todos.concat(descendientesEspacioDe(h.id, lista))
  })
  return todos
}

export function espaciosDisponibles(lista: Recurso[]) {
  return lista.filter((r) => r.tipo === "espacio")
}

export function nombreEspacio(espacioId: number | null, lista: Recurso[]) {
  if (espacioId === null) return null
  return lista.find((r) => r.id === espacioId)?.nombre ?? null
}

export function hijosSincronizados(recurso: Recurso, lista: Recurso[]) {
  return descendientesDe(recurso.id, lista).filter(
    (h) => h.espacio_id === recurso.espacio_id
  )
}

export const GRUPO_TODOS = "__todos__"
export const GRUPO_SIN_CATEGORIA = "__sin_categoria__"
export const GRUPO_SIN_SUBCATEGORIA = "__sin_subcategoria__"

export function grupoDe(r: Recurso) {
  return r.categoria || GRUPO_SIN_CATEGORIA
}

export function subgrupoDe(r: Recurso) {
  return r.subcategoria || GRUPO_SIN_SUBCATEGORIA
}

export function etiquetaGrupo(clave: string) {
  if (clave === GRUPO_SIN_CATEGORIA) return "Sin categoría"
  if (clave === GRUPO_SIN_SUBCATEGORIA) return "Sin subcategoría"
  return clave.charAt(0).toUpperCase() + clave.slice(1)
}

export function gruposDisponibles(lista: Recurso[]) {
  const claves = Array.from(new Set(lista.map(grupoDe)))
  claves.sort((a, b) => etiquetaGrupo(a).localeCompare(etiquetaGrupo(b), "es"))
  return claves
}

export function subgruposDisponibles(lista: Recurso[]) {
  const claves = Array.from(new Set(lista.map(subgrupoDe)))
  claves.sort((a, b) => etiquetaGrupo(a).localeCompare(etiquetaGrupo(b), "es"))
  return claves
}

// Valores distintos no nulos de categoria/subcategoria entre los activos --
// alimentan el Select de los formularios de alta/edición (ver
// CampoCategoria), para que se pueda elegir lo que ya se usa y así evitar
// categorías/subcategorías duplicadas por un typo o una tilde de diferencia.
// El Select siempre deja además la puerta abierta a escribir un valor nuevo.
export function valoresCategoria(lista: Recurso[]) {
  return Array.from(new Set(lista.map((r) => r.categoria).filter((v): v is string => !!v))).sort(
    (a, b) => a.localeCompare(b, "es")
  )
}

export function valoresSubcategoria(lista: Recurso[]) {
  return Array.from(
    new Set(lista.map((r) => r.subcategoria).filter((v): v is string => !!v))
  ).sort((a, b) => a.localeCompare(b, "es"))
}

export const ESTADOS_ACTIVO: Record<
  EstadoRecurso,
  { texto: string; variant: "success" | "warning" | "destructive" }
> = {
  disponible: { texto: "Disponible", variant: "success" },
  en_mantenimiento: { texto: "En mantenimiento", variant: "warning" },
  no_disponible: { texto: "No disponible", variant: "destructive" },
}
