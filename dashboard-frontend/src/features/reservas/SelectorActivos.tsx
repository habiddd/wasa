import { useState, type ReactNode } from "react"

import { ChevronDown } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Input } from "@/components/ui/input"
import { IconoTipo } from "@/features/calendario/IconoTipo"
import type { Recurso } from "@/lib/api"

function normalizar(texto: string) {
  return texto
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
}

/** Un activo "raíz": ni accesorio de otro equipo (padre_id) ni contenido de
 * un espacio (espacio_id). Un espacio nunca tiene ninguno de los dos (CHECK
 * recursos_espacio_sin_padre_ni_lugar, postgres/init/02-schema.sql). */
function esRaiz(recurso: Recurso) {
  return recurso.padre_id === null && recurso.espacio_id === null
}

function disponible(recurso: Recurso) {
  return recurso.estado === "disponible"
}

/**
 * Una lista del formulario "Nueva reserva" (espacios o equipos). Mismo
 * criterio del bot: al marcar un activo se incluyen por defecto sus
 * accesorios (padre_id) y, si es un espacio, sus contenidos (espacio_id);
 * cualquiera de ellos se puede quitar después uno a uno. Desmarcar un nodo
 * quita también lo que cuelga de él, como `_quitar_del_carrito`.
 */
export function SelectorActivos({
  tipo,
  recursos,
  seleccionados,
  onChange,
  ocupados,
  disabled,
}: {
  tipo: Recurso["tipo"]
  recursos: Recurso[]
  seleccionados: number[]
  onChange: (ids: number[]) => void
  ocupados: Set<number>
  disabled?: boolean
}) {
  const [filtro, setFiltro] = useState("")
  const [abierto, setAbierto] = useState(false)

  function hijosDe(recurso: Recurso): Recurso[] {
    // Los accesorios de un equipo del espacio también tienen espacio_id: se
    // muestran solo bajo su equipo, no dos veces.
    const porEspacio =
      recurso.tipo === "espacio"
        ? recursos.filter((r) => r.espacio_id === recurso.id && r.padre_id === null)
        : []
    const porPadre = recursos.filter((r) => r.padre_id === recurso.id)
    return [...porEspacio, ...porPadre]
  }

  function descendientesIds(recurso: Recurso): number[] {
    return hijosDe(recurso).flatMap((hijo) => [hijo.id, ...descendientesIds(hijo)])
  }

  function alternar(recurso: Recurso, marcar: boolean) {
    if (marcar) {
      // Solo se suman los descendientes que se pueden reservar.
      const agregar = [recurso, ...descendientesIds(recurso).map((id) => recursos.find((r) => r.id === id)!)]
        .filter((r) => disponible(r) && !ocupados.has(r.id))
        .map((r) => r.id)
      onChange([...new Set([...seleccionados, ...agregar])])
    } else {
      const quitar = new Set([recurso.id, ...descendientesIds(recurso)])
      onChange(seleccionados.filter((id) => !quitar.has(id)))
    }
  }

  const consulta = normalizar(filtro.trim())
  function coincideSubarbol(recurso: Recurso): boolean {
    if (!consulta) return true
    const texto = normalizar(`${recurso.nombre} ${recurso.placa ?? ""}`)
    if (texto.includes(consulta)) return true
    return hijosDe(recurso).some(coincideSubarbol)
  }

  function renderNodo(recurso: Recurso, nivel: number): ReactNode {
    const marcado = seleccionados.includes(recurso.id)
    const ocupado = ocupados.has(recurso.id)
    const noDisponible = !disponible(recurso)
    return (
      <div key={recurso.id}>
        <label
          htmlFor={`nueva-reserva-activo-${recurso.id}`}
          className="flex items-center gap-2 py-1 text-sm"
          style={{ paddingLeft: `${nivel * 1.25}rem` }}
        >
          <Checkbox
            id={`nueva-reserva-activo-${recurso.id}`}
            checked={marcado}
            disabled={disabled || ((ocupado || noDisponible) && !marcado)}
            onCheckedChange={(v) => alternar(recurso, v === true)}
          />
          <IconoTipo tipo={recurso.tipo} className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="min-w-0 flex-1 truncate">
            {recurso.nombre}
            {recurso.placa && <span className="text-muted-foreground"> · {recurso.placa}</span>}
            {tipo === "equipo" && nivel === 0 && recurso.espacio_id !== null && (
              <span className="text-muted-foreground">
                {" "}
                · en {recursos.find((r) => r.id === recurso.espacio_id)?.nombre ?? "un espacio"}
              </span>
            )}
          </span>
          {ocupado ? (
            <Badge variant="warning" className="shrink-0">
              Ocupado
            </Badge>
          ) : (
            noDisponible && (
              <Badge variant="outline" className="shrink-0">
                {recurso.estado}
              </Badge>
            )
          )}
        </label>
        {hijosDe(recurso).map((hijo) => renderNodo(hijo, nivel + 1))}
      </div>
    )
  }

  // Solo activos disponibles pueden ser raíz de la lista -- igual que el bot
  // (recursos_service.ESTADO_DISPONIBLE).
  // En "Activos" van todos los equipos, también los que están dentro de un
  // espacio (y por eso aparecen además en "Espacios"): la selección es una
  // sola lista de ids compartida, así que marcar en una marca en la otra.
  const todasLasRaices = recursos.filter(
    (r) => r.tipo === tipo && disponible(r) && (tipo === "equipo" ? r.padre_id === null : esRaiz(r))
  )
  const raices = todasLasRaices.filter(coincideSubarbol)
  const titulo = tipo === "espacio" ? "Espacios" : "Activos"
  const nombreLista = titulo.toLowerCase()

  return (
    <Collapsible open={abierto} onOpenChange={setAbierto} className="rounded-md border">
      <CollapsibleTrigger
        className="flex w-full items-center gap-2 px-3 py-2 text-sm font-medium"
        disabled={disabled}
      >
        <span className="flex-1 text-left">{titulo}</span>
        <ChevronDown
          className={`size-4 shrink-0 text-muted-foreground transition-transform ${abierto ? "rotate-180" : ""}`}
        />
      </CollapsibleTrigger>
      <CollapsibleContent className="space-y-1.5 border-t p-3">
        <Input
          placeholder={`Filtrar ${nombreLista}`}
          value={filtro}
          onChange={(e) => setFiltro(e.target.value)}
          disabled={disabled}
        />
        <div className="max-h-56 overflow-y-auto">
          {raices.length === 0 ? (
            <p className="py-2 text-center text-sm text-muted-foreground">No hay {nombreLista} que coincidan.</p>
          ) : (
            raices.map((r) => renderNodo(r, 0))
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}
