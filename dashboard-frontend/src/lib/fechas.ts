// Helper compartido para mostrar fechas/horas del backend (siempre en UTC,
// ver `Reserva`/`AuditoriaBot`/etc en models.py) en hora de la LACS -- igual
// que hace el bot (recursos_service.ZONA_HORARIA_LACS) y el calendario
// (calendario-utils.ZONA_LACS). Reexporta esa misma constante en vez de
// duplicarla para que ambos lados del dashboard usen una sola zona horaria.
import { ZONA_LACS } from "@/features/calendario/calendario-utils"

export { ZONA_LACS }

const FORMATO_FECHA_HORA = new Intl.DateTimeFormat("es-CO", {
  timeZone: ZONA_LACS,
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
  hour12: true,
})

const FORMATO_HORA = new Intl.DateTimeFormat("es-CO", {
  timeZone: ZONA_LACS,
  hour: "numeric",
  minute: "2-digit",
  second: "2-digit",
  hour12: true,
})

/** "10/09/2026, 2:30 p.m." en hora de la LACS, sea cual sea la del navegador. */
export function formatearFechaHoraLACS(valor: string | null | undefined): string {
  if (!valor) return "-"
  const fecha = new Date(valor)
  if (Number.isNaN(fecha.getTime())) return "-"
  return FORMATO_FECHA_HORA.format(fecha).replace("a. m.", "a.m.").replace("p. m.", "p.m.")
}

/** "2:30:00 p.m." en hora de la LACS -- para marcas de tiempo sin fecha (ej. "última actualización"). */
export function formatearHoraLACS(valor: string | null | undefined): string {
  if (!valor) return "-"
  const fecha = new Date(valor)
  if (Number.isNaN(fecha.getTime())) return "-"
  return FORMATO_HORA.format(fecha).replace("a. m.", "a.m.").replace("p. m.", "p.m.")
}
