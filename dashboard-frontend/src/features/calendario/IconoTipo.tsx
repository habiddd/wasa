import { Boxes, MapPin } from "lucide-react"

/**
 * Icono según el tipo de recurso. `null` (un evento que mezcla activos y
 * lugares) usa el de activos: en la práctica el lugar acompaña al equipo.
 */
export function IconoTipo({ tipo, className }: { tipo: string | null; className?: string }) {
  const Icono = tipo === "espacio" ? MapPin : Boxes
  return <Icono className={className} aria-hidden />
}
