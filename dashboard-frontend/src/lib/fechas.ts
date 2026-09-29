// Helper compartido para mostrar fechas/horas del backend (siempre en UTC,
// ver `Reserva`/`AuditoriaBot`/etc en models.py) en hora de la LACS -- igual
// que hace el bot (recursos_service.ZONA_HORARIA_LACS) y el calendario
// (calendario-utils.ZONA_LACS). Reexporta esa misma constante en vez de
// duplicarla para que ambos lados del dashboard usen una sola zona horaria.
import { normalizarAmPm, ZONA_LACS } from "@/features/calendario/calendario-utils"

export { ZONA_LACS }

// Mismo formato que la tabla de Reservas ("28 sept 2026"): un solo estilo de
// fecha en todo el dashboard, no "28/09/2026" en unos lados y "28 sept" en otros.
const FORMATO_FECHA_HORA = new Intl.DateTimeFormat("es-CO", {
  timeZone: ZONA_LACS,
  day: "numeric",
  month: "short",
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

/** "10 sept 2026, 2:30 p.m." en hora de la LACS, sea cual sea la del navegador. */
export function formatearFechaHoraLACS(valor: string | null | undefined): string {
  if (!valor) return "-"
  const fecha = new Date(valor)
  if (Number.isNaN(fecha.getTime())) return "-"
  return normalizarAmPm(FORMATO_FECHA_HORA.format(fecha))
}

/** "2:30:00 p.m." en hora de la LACS -- para marcas de tiempo sin fecha (ej. "última actualización"). */
export function formatearHoraLACS(valor: string | null | undefined): string {
  if (!valor) return "-"
  const fecha = new Date(valor)
  if (Number.isNaN(fecha.getTime())) return "-"
  return normalizarAmPm(FORMATO_HORA.format(fecha))
}
