import { cn } from "@/lib/utils"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
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
    <Select
      value={estado}
      disabled={disabled}
      onValueChange={(v) => onChange(v as EstadoRecurso)}
    >
      {/* El trigger sigue siendo la píldora de color de siempre -- se
          neutraliza el tamaño/borde/padding por defecto de SelectTrigger
          para que el Select del theme no rompa el aspecto de la píldora. */}
      <SelectTrigger
        size="sm"
        className={cn(
          "h-5 w-fit gap-1 rounded-full border-none bg-transparent px-2 py-0 text-xs font-medium data-[size=sm]:h-5 data-[size=sm]:rounded-full [&_svg]:size-3",
          variantClass,
          disabled && "opacity-60"
        )}
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent align="end">
        {(Object.keys(ESTADOS_ACTIVO) as EstadoRecurso[]).map((valor) => (
          <SelectItem key={valor} value={valor}>
            {ESTADOS_ACTIVO[valor].texto}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
