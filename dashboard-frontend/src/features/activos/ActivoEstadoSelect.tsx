import { cn } from "@/lib/utils"
import type { EstadoRecurso } from "@/lib/api"

import { ESTADOS_ACTIVO } from "./tree-utils"

const VARIANT_CLASSES: Record<string, string> = {
  success: "bg-success/10 text-success",
  warning: "bg-warning/10 text-warning",
  destructive: "bg-destructive/10 text-destructive",
}

export function ActivoEstadoSelect({
  estado,
  disabled,
  onChange,
}: {
  estado: EstadoRecurso
  disabled?: boolean
  onChange: (nuevo: EstadoRecurso) => void
}) {
  const variantClass = VARIANT_CLASSES[ESTADOS_ACTIVO[estado].variant]

  return (
    <select
      className={cn(
        "h-5 rounded-full border-none px-2 text-xs font-medium outline-none",
        variantClass,
        disabled && "opacity-60"
      )}
      value={estado}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value as EstadoRecurso)}
    >
      {(Object.keys(ESTADOS_ACTIVO) as EstadoRecurso[]).map((valor) => (
        <option key={valor} value={valor}>
          {ESTADOS_ACTIVO[valor].texto}
        </option>
      ))}
    </select>
  )
}
