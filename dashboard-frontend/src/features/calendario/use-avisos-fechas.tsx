import { useEffect, useState } from "react"

import {
  fetchExcepciones,
  fetchFechaMinimaSeguro,
  type DiaExcepcion,
  type FechaMinimaSeguro,
} from "@/lib/api"

import { claveDe, diaDe, DIAS_MINIMOS_SEGURO, fechaCivilLarga } from "./calendario-utils"

/**
 * Avisos "blandos" de fecha (plazo del seguro e inicio en un día de la lista
 * negra) para un formulario de reserva -- ni el bloqueo ni el guardado los
 * usa: son solo texto de advertencia, la LACS tiene la última palabra (ver
 * docs/ARQUITECTURA.md, secciones "Cláusula de seguro" y "Excepciones").
 * Extraído de ReservaDetalle.tsx para reusarlo también en la creación de
 * reservas desde el dashboard (NuevaReservaDialog).
 */
export function useAvisosFechasReserva(
  inicio: Date | null,
  fin: Date | null,
  saleBarranquilla: boolean
) {
  // Fecha mínima de HOY (no de creación) para el aviso de "¿Sale de
  // Barranquilla?" -- la calcula el backend (festivos de Colombia), nunca
  // este hook.
  const [minimaHoy, setMinimaHoy] = useState<FechaMinimaSeguro | null>(null)
  useEffect(() => {
    let vigente = true
    fetchFechaMinimaSeguro()
      .then((datos) => {
        if (vigente) setMinimaHoy(datos)
      })
      .catch(() => {})
    return () => {
      vigente = false
    }
  }, [])

  const incumplePlazo =
    saleBarranquilla && !!inicio && !!minimaHoy && claveDe(diaDe(inicio)) < minimaHoy.fecha_minima

  // Lista negra de fechas (pestaña "Excepciones"): mismo criterio que el bot
  // (solo importan el día de inicio y el de fin, ver
  // recursos_service._error_fecha_bloqueada).
  const [diasBloqueados, setDiasBloqueados] = useState<Map<string, DiaExcepcion>>(new Map())
  const claveInicio = inicio ? claveDe(diaDe(inicio)) : null
  const claveFin = fin ? claveDe(diaDe(fin)) : null
  useEffect(() => {
    if (!claveInicio || !claveFin) return
    const desde = claveInicio < claveFin ? claveInicio : claveFin
    const hasta = claveInicio < claveFin ? claveFin : claveInicio
    let vigente = true
    fetchExcepciones(desde, hasta)
      .then((data) => {
        if (vigente) setDiasBloqueados(new Map(data.dias.map((d) => [d.fecha, d])))
      })
      .catch(() => {})
    return () => {
      vigente = false
    }
  }, [claveInicio, claveFin])

  const avisosBloqueo = [
    claveInicio && { clave: claveInicio, cuando: "empieza" as const },
    claveFin && claveFin !== claveInicio && { clave: claveFin, cuando: "termina" as const },
  ]
    .filter((x): x is { clave: string; cuando: "empieza" | "termina" } => !!x)
    .map(({ clave, cuando }) => ({ dia: diasBloqueados.get(clave), cuando }))
    .filter((x): x is { dia: DiaExcepcion; cuando: "empieza" | "termina" } => !!x.dia?.bloqueada)

  return { minimaHoy, incumplePlazo, avisosBloqueo }
}

/** Párrafos de aviso (`text-warning`) para lo que devuelve el hook de arriba. */
export function AvisosFechasReserva({
  minimaHoy,
  incumplePlazo,
  avisosBloqueo,
}: ReturnType<typeof useAvisosFechasReserva>) {
  return (
    <>
      {incumplePlazo && minimaHoy && (
        <p className="text-xs text-warning">
          Ojo: el inicio queda antes de la fecha mínima del seguro (
          {fechaCivilLarga(minimaHoy.fecha_minima)}, {DIAS_MINIMOS_SEGURO} días hábiles desde
          hoy). Puedes guardar igual.
        </p>
      )}
      {avisosBloqueo.map(({ dia, cuando }) => (
        <p key={dia.fecha} className="text-xs text-warning">
          Ojo: el {fechaCivilLarga(dia.fecha)} (día en que {cuando} la reserva) está en la lista
          negra ({dia.excepcion?.motivo || dia.festivo || "no hábil"}). El bot no lo permitiría; el
          dashboard no lo bloquea.
        </p>
      ))}
    </>
  )
}
