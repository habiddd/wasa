import { useState } from "react"

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
import { Textarea } from "@/components/ui/textarea"
import type { EstadoRecurso, MotivoSalidaNoDevuelto } from "@/lib/api"

import { ESTADOS_ACTIVO } from "./tree-utils"

const MOTIVOS: Record<MotivoSalidaNoDevuelto, string> = {
  devuelto_tarde: "Lo devolvieron tarde",
  encontrado: "Se encontró (estaba en la LACS)",
  otro: "Otro motivo",
}

// Al sacar un activo de "No devuelto" (a cualquier estado) se pide por qué:
// queda en recurso_estado_historial para las métricas (ver 02-schema.sql).
export function MotivoNoDevueltoDialog({
  nombre,
  estadoNuevo,
  onCancel,
  onConfirm,
}: {
  nombre: string
  estadoNuevo: EstadoRecurso
  onCancel: () => void
  onConfirm: (motivo: MotivoSalidaNoDevuelto, nota: string) => Promise<void>
}) {
  const [motivo, setMotivo] = useState<MotivoSalidaNoDevuelto | "">("")
  const [nota, setNota] = useState("")
  const [guardando, setGuardando] = useState(false)

  const falta = !motivo || (motivo === "otro" && !nota.trim())

  async function confirmar() {
    if (!motivo || falta) return
    setGuardando(true)
    try {
      await onConfirm(motivo, nota.trim())
    } catch {
      setGuardando(false)
    }
  }

  return (
    <Dialog open onOpenChange={(abierto) => !abierto && !guardando && onCancel()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>¿Por qué deja de estar no devuelto?</DialogTitle>
          <DialogDescription>
            {nombre} pasará a «{ESTADOS_ACTIVO[estadoNuevo].texto}». El motivo queda en el
            historial del activo.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="motivo-no-devuelto">Motivo</Label>
            <Select value={motivo} onValueChange={(v) => setMotivo(v as MotivoSalidaNoDevuelto)}>
              <SelectTrigger id="motivo-no-devuelto" className="w-full">
                <SelectValue placeholder="Elige un motivo" />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(MOTIVOS) as MotivoSalidaNoDevuelto[]).map((m) => (
                  <SelectItem key={m} value={m}>
                    {MOTIVOS[m]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="nota-no-devuelto">
              Nota {motivo === "otro" ? "(obligatoria" : "(opcional"}, máx. 300)
            </Label>
            <Textarea
              id="nota-no-devuelto"
              value={nota}
              maxLength={300}
              onChange={(e) => setNota(e.target.value)}
              disabled={guardando}
            />
          </div>
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={onCancel} disabled={guardando}>
            Cancelar
          </Button>
          <Button type="button" onClick={confirmar} disabled={guardando || falta}>
            {guardando ? "Guardando…" : "Guardar"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
