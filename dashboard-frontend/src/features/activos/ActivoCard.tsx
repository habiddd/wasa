import { useState } from "react"
import { ChevronDown, ChevronRight } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import type { CambiosActivo, EstadoRecurso, OpcionesCambioActivo, Recurso } from "@/lib/api"

import { ActivoEditForm } from "./ActivoEditForm"
import { ActivoEstadoSelect } from "./ActivoEstadoSelect"
import { MotivoNoDevueltoDialog } from "./MotivoNoDevueltoDialog"
import { useActivosContext } from "./activos-context"
import { hijosDe, nombreEspacio } from "./tree-utils"

type Cambios = CambiosActivo & OpcionesCambioActivo
type Actualizado = Recurso & { hijos_actualizados?: number }

export function ActivoCard({
  recurso,
  lista,
  nivel,
}: {
  recurso: Recurso
  lista: Recurso[]
  nivel: number
}) {
  const { activos, expandidos, toggleExpandido, actualizar } = useActivosContext()
  const [editando, setEditando] = useState(false)
  const [cambiandoEstado, setCambiandoEstado] = useState(false)
  // Cambio en espera del motivo (ver actualizarConMotivo).
  const [pendiente, setPendiente] = useState<{
    cambios: Cambios
    resolve: (r: Actualizado) => void
    reject: (e: Error) => void
  } | null>(null)

  const hijos = hijosDe(recurso.id, lista)
  const tieneHijos = hijos.length > 0
  const expandido = expandidos.has(recurso.id)
  // El lugar se resuelve contra TODOS los activos (contexto), no contra
  // `lista`: `lista` puede venir filtrada (ej. solo equipos de una
  // categoría, o solo los contenidos de un lugar) y no siempre incluye el
  // espacio referenciado.
  const lugarNombre = nombreEspacio(recurso.espacio_id, activos)

  // Sacar un activo de "no_devuelto" (a cualquier estado) exige un motivo:
  // se pide en un diálogo y el cambio espera hasta que se guarde o cancele.
  function actualizarConMotivo(cambios: Cambios): Promise<Actualizado> {
    const saleDeNoDevuelto =
      recurso.estado === "no_devuelto" && cambios.estado !== undefined && cambios.estado !== "no_devuelto"
    if (!saleDeNoDevuelto) return actualizar(recurso.id, cambios)
    return new Promise((resolve, reject) => setPendiente({ cambios, resolve, reject }))
  }

  async function handleEstadoChange(nuevo: EstadoRecurso) {
    setCambiandoEstado(true)
    try {
      await actualizarConMotivo({ estado: nuevo })
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setCambiandoEstado(false)
    }
  }

  const meta = [
    recurso.placa ? `Placa: ${recurso.placa}` : null,
    recurso.marca || recurso.modelo
      ? [recurso.marca, recurso.modelo].filter(Boolean).join(" ")
      : null,
    recurso.ubicacion ? `Ubicación: ${recurso.ubicacion}` : null,
  ]
    .filter(Boolean)
    .join(" · ")

  return (
    <>
      <Card className="mb-2 gap-2 py-3" style={{ marginLeft: `${nivel * 1.25}rem` }}>
        <div className="flex flex-wrap items-center justify-between gap-3 px-4">
          <div className="flex flex-wrap items-center gap-2">
            {tieneHijos && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-7 gap-1 px-2 text-xs"
                onClick={() => toggleExpandido(recurso.id)}
              >
                {expandido ? (
                  <ChevronDown className="size-3.5" />
                ) : (
                  <ChevronRight className="size-3.5" />
                )}
                {hijos.length} hijo{hijos.length === 1 ? "" : "s"}
              </Button>
            )}
            <strong className="text-sm">{recurso.nombre}</strong>
            <Badge variant="secondary">{recurso.tipo === "equipo" ? "Equipo" : "Espacio"}</Badge>
            {recurso.categoria && <Badge variant="secondary">{recurso.categoria}</Badge>}
            {recurso.subcategoria && <Badge variant="outline">{recurso.subcategoria}</Badge>}
            {lugarNombre && <Badge variant="outline">Lugar: {lugarNombre}</Badge>}
            <ActivoEstadoSelect
              estado={recurso.estado}
              disabled={cambiandoEstado}
              onChange={handleEstadoChange}
            />
          </div>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => setEditando((v) => !v)}
          >
            Editar
          </Button>
        </div>
        {meta && <p className="px-4 text-xs text-muted-foreground">{meta}</p>}
        {editando && (
          <div className="px-4">
            <ActivoEditForm
              recurso={recurso}
              lista={lista}
              onCancel={() => setEditando(false)}
              onSave={async (cambios) => {
                const actualizado = await actualizarConMotivo(cambios)
                // Solo informativo -- reload() ya trajo los datos nuevos;
                // esto es para que el usuario sepa que también se movieron
                // hijos, no solo el activo que editó.
                if (actualizado.hijos_actualizados) {
                  toast.success(
                    `Se movió también ${actualizado.hijos_actualizados} hijo(s) al nuevo lugar.`
                  )
                }
                setEditando(false)
              }}
            />
          </div>
        )}
      </Card>
      {tieneHijos && expandido && (
        <div>
          {hijos.map((h) => (
            <ActivoCard key={h.id} recurso={h} lista={lista} nivel={nivel + 1} />
          ))}
        </div>
      )}
      {pendiente && pendiente.cambios.estado && (
        <MotivoNoDevueltoDialog
          nombre={recurso.nombre}
          estadoNuevo={pendiente.cambios.estado}
          onCancel={() => {
            pendiente.reject(new Error("Cambio de estado cancelado: falta el motivo."))
            setPendiente(null)
          }}
          onConfirm={async (motivo, nota) => {
            try {
              const r = await actualizar(recurso.id, {
                ...pendiente.cambios,
                motivo_estado: motivo,
                nota_estado: nota,
              })
              pendiente.resolve(r)
              setPendiente(null)
            } catch (err) {
              toast.error(err instanceof Error ? err.message : String(err))
              throw err
            }
          }}
        />
      )}
    </>
  )
}
