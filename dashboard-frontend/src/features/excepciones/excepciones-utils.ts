import { useEffect, useRef, useState } from "react"

import {
  fetchExcepciones,
  type DiaExcepcion,
  type OrigenExcepcion,
  type ReglasExcepcion,
} from "@/lib/api"

// Un estilo por origen: la celda del calendario y el badge de la lista
// comparten la misma paleta, así el color significa lo mismo en las dos
// vistas. `festivo`/`manual` usan los tonos de advertencia/error del
// dashboard; `desbloqueada` es la excepción "positiva" (abre un día que por
// regla estaría cerrado), con borde punteado en vez de relleno.
export const ESTILO_ORIGEN: Record<
  OrigenExcepcion,
  { etiqueta: string; celda: string; badge: string }
> = {
  dia_semana: {
    etiqueta: "Día no hábil (regla)",
    celda: "bg-muted text-muted-foreground",
    badge: "bg-muted text-muted-foreground",
  },
  festivo: {
    etiqueta: "Festivo",
    celda: "border-warning/40 bg-warning/10 text-warning-text",
    badge: "border-warning/40 bg-warning/10 text-warning-text",
  },
  manual: {
    etiqueta: "Bloqueo manual",
    celda: "border-destructive/40 bg-destructive/10 text-destructive-text",
    badge: "border-destructive/40 bg-destructive/10 text-destructive-text",
  },
  desbloqueada: {
    etiqueta: "Desbloqueada",
    celda: "border-dashed border-success bg-success/5 text-success-text",
    badge: "border-dashed border-success bg-success/5 text-success-text",
  },
}

// Festivo informativo con `festivos_co` desactivado: no está bloqueado, pero
// se sigue marcando (tenue) para que se note que es un festivo. No es un
// origen real (`DiaExcepcion.origen` viene null en ese caso).
export const ESTILO_FESTIVO_INFORMATIVO = "bg-muted/40 text-muted-foreground"

export const NOMBRES_DIA_LARGO = [
  "Lunes",
  "Martes",
  "Miércoles",
  "Jueves",
  "Viernes",
  "Sábado",
  "Domingo",
]

/** Línea de detalle de un día para el título/tooltip de una celda o fila. */
export function detalleDia(dia: DiaExcepcion, reglas: ReglasExcepcion | null): string {
  if (dia.excepcion?.motivo) return `Motivo: ${dia.excepcion.motivo}`
  if (dia.origen === "manual") return "Bloqueo manual sin motivo registrado."
  if (dia.origen === "desbloqueada") {
    if (dia.festivo) return `Desbloqueada (festivo: ${dia.festivo})`
    return "Desbloqueada (regla del día de la semana)"
  }
  if (dia.festivo && !dia.bloqueada_por_regla) {
    return `Festivo (bloqueo de festivos desactivado): ${dia.festivo}`
  }
  if (dia.festivo) return `Festivo: ${dia.festivo}`
  if (dia.origen === "dia_semana") return `Regla: ${NOMBRES_DIA_LARGO[dia.dia_semana]}`
  return reglas ? "Día normal." : ""
}

/** Descarta respuestas viejas, igual que `peticionRef` en CalendarioPanel. */
export function useExcepciones(desde: string, hasta: string, version: number) {
  const [dias, setDias] = useState<Map<string, DiaExcepcion>>(new Map())
  const [reglas, setReglas] = useState<ReglasExcepcion | null>(null)
  const [cargando, setCargando] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const peticionRef = useRef(0)

  useEffect(() => {
    const peticion = ++peticionRef.current
    setCargando(true)
    fetchExcepciones(desde, hasta)
      .then((data) => {
        if (peticion !== peticionRef.current) return
        setDias(new Map(data.dias.map((d) => [d.fecha, d])))
        setReglas(data.reglas)
        setError(null)
      })
      .catch((err) => {
        if (peticion !== peticionRef.current) return
        setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (peticion === peticionRef.current) setCargando(false)
      })
  }, [desde, hasta, version])

  const lista = [...dias.values()].sort((a, b) => a.fecha.localeCompare(b.fecha))

  return { dias, lista, reglas, cargando, error }
}
