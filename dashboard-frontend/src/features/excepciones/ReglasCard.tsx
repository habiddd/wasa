import { useEffect, useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { fetchReglasExcepcion, guardarReglasExcepcion, type ReglasExcepcion } from "@/lib/api"

import { NOMBRES_DIA_LARGO } from "./excepciones-utils"

export function ReglasCard({ version, onCambio }: { version: number; onCambio: () => void }) {
  const [reglas, setReglas] = useState<ReglasExcepcion | null>(null)
  const [diasSemana, setDiasSemana] = useState<number[]>([])
  const [festivosCo, setFestivosCo] = useState(true)
  const [cargando, setCargando] = useState(true)
  const [guardando, setGuardando] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let vigente = true
    setCargando(true)
    fetchReglasExcepcion()
      .then((datos) => {
        if (!vigente) return
        setReglas(datos)
        setDiasSemana(datos.dias_semana)
        setFestivosCo(datos.festivos_co)
        setError(null)
      })
      .catch((err) => {
        if (vigente) setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (vigente) setCargando(false)
      })
    return () => {
      vigente = false
    }
  }, [version])

  function alternarDia(dia: number, marcado: boolean) {
    setDiasSemana((actual) =>
      marcado ? [...actual, dia].sort((a, b) => a - b) : actual.filter((d) => d !== dia)
    )
  }

  const hayCambios =
    !!reglas &&
    (JSON.stringify([...diasSemana].sort()) !== JSON.stringify([...reglas.dias_semana].sort()) ||
      festivosCo !== reglas.festivos_co)
  const los7Bloqueados = diasSemana.length === 7

  async function guardar() {
    setGuardando(true)
    try {
      const nuevas = await guardarReglasExcepcion({ dias_semana: diasSemana, festivos_co: festivosCo })
      setReglas(nuevas)
      toast.success("Reglas actualizadas.")
      onCambio()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setGuardando(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Reglas generales</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {cargando && !reglas ? (
          <Skeleton className="h-24 w-full" />
        ) : error && !reglas ? (
          <p className="text-sm text-destructive-text">No se pudieron cargar las reglas: {error}</p>
        ) : (
          <>
            <div className="flex flex-wrap gap-x-4 gap-y-2">
              {NOMBRES_DIA_LARGO.map((nombre, i) => (
                <div key={i} className="flex items-center gap-2">
                  <Checkbox
                    id={`regla-dia-${i}`}
                    checked={diasSemana.includes(i)}
                    onCheckedChange={(v) => alternarDia(i, v === true)}
                    disabled={guardando}
                  />
                  <Label htmlFor={`regla-dia-${i}`} className="cursor-pointer">
                    {nombre}
                  </Label>
                </div>
              ))}
            </div>
            <div className="flex items-center gap-2">
              <Checkbox
                id="regla-festivos"
                checked={festivosCo}
                onCheckedChange={(v) => setFestivosCo(v === true)}
                disabled={guardando}
              />
              <Label htmlFor="regla-festivos" className="cursor-pointer">
                Bloquear festivos de Colombia
              </Label>
            </div>
            <p className="text-xs text-muted-foreground">
              Las excepciones de un día concreto mandan sobre estas reglas.
            </p>
            {los7Bloqueados && (
              <p className="text-xs text-destructive-text">
                No se pueden bloquear los 7 días de la semana.
              </p>
            )}
            <Button size="sm" onClick={guardar} disabled={!hayCambios || los7Bloqueados || guardando}>
              {guardando ? "Guardando…" : "Guardar reglas"}
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  )
}
