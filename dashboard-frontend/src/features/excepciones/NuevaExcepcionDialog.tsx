import { useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { claveDe, hoy } from "@/features/calendario/calendario-utils"
import { guardarExcepcion, type TipoExcepcion } from "@/lib/api"

export function NuevaExcepcionDialog({
  onClose,
  onCambio,
}: {
  onClose: () => void
  onCambio: () => void
}) {
  const hoyIso = claveDe(hoy())
  const [desde, setDesde] = useState(hoyIso)
  const [hasta, setHasta] = useState("")
  const [tipo, setTipo] = useState<TipoExcepcion>("bloqueo")
  const [motivo, setMotivo] = useState("")
  const [guardando, setGuardando] = useState(false)

  const rangoInvalido = !!hasta && hasta < desde

  async function guardar() {
    if (!desde || rangoInvalido) return
    setGuardando(true)
    try {
      const resultado = await guardarExcepcion({
        desde,
        hasta: hasta || undefined,
        tipo,
        motivo: motivo.trim() || null,
      })
      const n = resultado.dias.length
      const verbo = tipo === "bloqueo" ? "bloqueada" : "desbloqueada"
      toast.success(`${n} fecha${n === 1 ? "" : "s"} ${verbo}${n === 1 ? "" : "s"}.`)
      onCambio()
      onClose()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setGuardando(false)
    }
  }

  return (
    <Dialog open onOpenChange={(abierto) => !abierto && !guardando && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Añadir fecha a la lista negra</DialogTitle>
        </DialogHeader>

        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="nueva-excepcion-desde">Desde</Label>
              <Input
                id="nueva-excepcion-desde"
                type="date"
                value={desde}
                onChange={(e) => setDesde(e.target.value)}
                disabled={guardando}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="nueva-excepcion-hasta">Hasta (opcional)</Label>
              <Input
                id="nueva-excepcion-hasta"
                type="date"
                min={desde}
                value={hasta}
                onChange={(e) => setHasta(e.target.value)}
                disabled={guardando}
              />
            </div>
          </div>
          {rangoInvalido && (
            <p className="text-xs text-destructive">
              "Hasta" debe ser igual o después de "Desde".
            </p>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="nueva-excepcion-tipo">Tipo</Label>
            <Select value={tipo} onValueChange={(v) => setTipo(v as TipoExcepcion)}>
              <SelectTrigger id="nueva-excepcion-tipo" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="bloqueo">Bloquear por defecto</SelectItem>
                <SelectItem value="desbloqueo">Desbloquear</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="nueva-excepcion-motivo">Motivo (opcional, máx. 200)</Label>
            <Textarea
              id="nueva-excepcion-motivo"
              value={motivo}
              maxLength={200}
              onChange={(e) => setMotivo(e.target.value)}
              disabled={guardando}
            />
          </div>
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose} disabled={guardando}>
            Cancelar
          </Button>
          <Button type="button" onClick={guardar} disabled={guardando || !desde || rangoInvalido}>
            {guardando ? "Guardando…" : "Guardar"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
