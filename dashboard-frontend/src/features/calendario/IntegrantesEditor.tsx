import { Plus, Trash2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

// Mismo rango que TAMANO_GRUPO_MIN/MAX en recursos_service.py y el CHECK
// reserva_grupos_tamano_rango de postgres/init/02-schema.sql.
export const TAMANO_GRUPO_MIN = 2
export const TAMANO_GRUPO_MAX = 10

/**
 * Lista editable de códigos Uninorte de un grupo (integrantes[0] es siempre
 * quien reserva). Extraído de ReservaDetalle.tsx para reusarlo también en
 * NuevaReservaDialog.
 */
export function IntegrantesEditor({
  integrantes,
  onChange,
  disabled,
}: {
  integrantes: string[]
  onChange: (integrantes: string[]) => void
  disabled?: boolean
}) {
  function actualizar(indice: number, valor: string) {
    onChange(integrantes.map((codigo, i) => (i === indice ? valor : codigo)))
  }

  function quitar(indice: number) {
    onChange(integrantes.filter((_, i) => i !== indice))
  }

  function agregar() {
    onChange([...integrantes, ""])
  }

  return (
    <fieldset className="space-y-1.5">
      <legend className="text-sm font-medium">Integrantes ({integrantes.length})</legend>
      <div className="space-y-2">
        {integrantes.map((codigo, i) => (
          <div key={i} className="flex items-center gap-2">
            <Input
              aria-label={i === 0 ? "ID uninorte de quien reserva" : `ID uninorte del integrante ${i + 1}`}
              placeholder={i === 0 ? "ID de quien reserva" : `ID del integrante ${i + 1}`}
              value={codigo}
              onChange={(e) => actualizar(i, e.target.value)}
              disabled={disabled}
            />
            <Button
              type="button"
              size="icon-sm"
              variant="ghost"
              aria-label={`Quitar integrante ${i + 1}`}
              title={`Quitar integrante ${i + 1}`}
              onClick={() => quitar(i)}
              disabled={disabled || integrantes.length <= TAMANO_GRUPO_MIN}
            >
              <Trash2 />
            </Button>
          </div>
        ))}
      </div>
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={agregar}
        disabled={disabled || integrantes.length >= TAMANO_GRUPO_MAX}
      >
        <Plus />
        Agregar integrante
      </Button>
    </fieldset>
  )
}
