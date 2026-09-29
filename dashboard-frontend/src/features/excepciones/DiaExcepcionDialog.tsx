import { useEffect, useState } from "react"
import { Pencil } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { diaDeSemana, fechaCivilLarga } from "@/features/calendario/calendario-utils"
import {
  eliminarExcepcion,
  fetchExcepciones,
  guardarExcepcion,
  type DiaExcepcion,
  type ReglasExcepcion,
  type TipoExcepcion,
} from "@/lib/api"

import { detalleDia, ESTILO_ORIGEN, NOMBRES_DIA_LARGO } from "./excepciones-utils"

// Estado del día cuando no aparece en la respuesta (no tiene regla, festivo
// ni excepción): un día normal, sin restricción.
function diaNormal(fecha: string, diaSemana: number): DiaExcepcion {
  return {
    fecha,
    dia_semana: diaSemana,
    bloqueada: false,
    origen: null,
    festivo: null,
    bloqueada_por_regla: false,
    excepcion: null,
  }
}

type Accion = "bloqueo" | "desbloqueo" | "seguir_reglas"

export function DiaExcepcionDialog({
  fecha,
  onClose,
  onCambio,
}: {
  fecha: string
  onClose: () => void
  onCambio: () => void
}) {
  const [dia, setDia] = useState<DiaExcepcion | null>(null)
  const [reglas, setReglas] = useState<ReglasExcepcion | null>(null)
  const [cargando, setCargando] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editando, setEditando] = useState(false)
  const [accion, setAccion] = useState<Accion>("bloqueo")
  const [motivo, setMotivo] = useState("")
  const [guardando, setGuardando] = useState(false)

  useEffect(() => {
    let vigente = true
    setCargando(true)
    fetchExcepciones(fecha, fecha)
      .then((data) => {
        if (!vigente) return
        const encontrado = data.dias[0] ?? null
        setDia(encontrado)
        setReglas(data.reglas)
        setAccion(encontrado?.excepcion?.tipo ?? "bloqueo")
        setMotivo(encontrado?.excepcion?.motivo ?? "")
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
  }, [fecha])

  async function guardar() {
    setGuardando(true)
    try {
      let mensaje: string
      let nuevo: DiaExcepcion
      if (accion === "seguir_reglas") {
        nuevo = await eliminarExcepcion(fecha)
        mensaje = "Excepción quitada."
      } else {
        const resultado = await guardarExcepcion({
          desde: fecha,
          tipo: accion as TipoExcepcion,
          motivo: motivo.trim() || null,
        })
        nuevo = resultado.dias[0]
        mensaje = accion === "bloqueo" ? "Día bloqueado." : "Día desbloqueado."
      }
      setDia(nuevo)
      toast.success(mensaje)
      onCambio()
      setEditando(false)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setGuardando(false)
    }
  }

  const [anioIso, mesIso, diaIso] = fecha.split("-").map(Number)
  const diaMostrado =
    dia ?? (reglas ? diaNormal(fecha, diaDeSemana({ anio: anioIso, mes: mesIso, dia: diaIso })) : null)

  return (
    <Dialog open onOpenChange={(abierto) => !abierto && !guardando && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{fechaCivilLarga(fecha)}</DialogTitle>
          <DialogDescription>{editando && "Editando la excepción de este día."}</DialogDescription>
        </DialogHeader>

        {cargando && !diaMostrado ? (
          <Skeleton className="h-32 w-full" />
        ) : error && !diaMostrado ? (
          <p className="text-sm text-destructive-text">No se pudo cargar: {error}</p>
        ) : !diaMostrado ? null : editando ? (
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="excepcion-accion">Acción</Label>
              <Select value={accion} onValueChange={(v) => setAccion(v as Accion)}>
                <SelectTrigger id="excepcion-accion" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="bloqueo">Bloquear este día</SelectItem>
                  <SelectItem value="desbloqueo">Desbloquear este día</SelectItem>
                  {diaMostrado.excepcion && (
                    <SelectItem value="seguir_reglas">Seguir las reglas (quitar excepción)</SelectItem>
                  )}
                </SelectContent>
              </Select>
            </div>
            {accion !== "seguir_reglas" && (
              <div className="space-y-1.5">
                <Label htmlFor="excepcion-motivo">Motivo (opcional, máx. 200)</Label>
                <Textarea
                  id="excepcion-motivo"
                  value={motivo}
                  maxLength={200}
                  onChange={(e) => setMotivo(e.target.value)}
                  disabled={guardando}
                />
              </div>
            )}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setEditando(false)} disabled={guardando}>
                Cancelar
              </Button>
              <Button type="button" onClick={guardar} disabled={guardando}>
                {guardando ? "Guardando…" : "Guardar"}
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2">
              <Badge variant={diaMostrado.bloqueada ? "destructive" : "success"}>
                {diaMostrado.bloqueada ? "Bloqueada" : "Habilitada"}
              </Badge>
              {diaMostrado.origen && (
                <Badge variant="outline" className={ESTILO_ORIGEN[diaMostrado.origen].badge}>
                  {ESTILO_ORIGEN[diaMostrado.origen].etiqueta}
                </Badge>
              )}
            </div>
            <p className="text-sm text-muted-foreground">
              {detalleDia(diaMostrado, reglas) || `Regla: ${NOMBRES_DIA_LARGO[diaMostrado.dia_semana]} normal.`}
            </p>
            <DialogFooter>
              <Button type="button" size="sm" variant="outline" onClick={() => setEditando(true)}>
                <Pencil />
                Editar
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
