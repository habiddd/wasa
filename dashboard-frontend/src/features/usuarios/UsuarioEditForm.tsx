import { useState, type FormEvent } from "react"

import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import type { CambiosUsuario, Rol, Usuario } from "@/lib/api"

const ROLES: Rol[] = ["estudiante", "staff", "admin"]

export function UsuarioEditForm({
  usuario,
  onCancel,
  onSave,
}: {
  usuario: Usuario
  onCancel: () => void
  onSave: (cambios: CambiosUsuario) => Promise<void>
}) {
  const [nombre, setNombre] = useState(usuario.nombre ?? "")
  const [telefono, setTelefono] = useState(usuario.telefono_wa)
  const [codigo, setCodigo] = useState(usuario.codigo_uninorte ?? "")
  const [cedula, setCedula] = useState(usuario.cedula ?? "")
  const [rol, setRol] = useState<Rol>(usuario.rol)
  const [activo, setActivo] = useState(usuario.activo)
  const [error, setError] = useState<string | null>(null)
  const [guardando, setGuardando] = useState(false)

  async function handleSubmit(ev: FormEvent) {
    ev.preventDefault()
    setError(null)
    setGuardando(true)
    try {
      await onSave({
        nombre: nombre || null,
        telefono_wa: telefono,
        codigo_uninorte: codigo || null,
        cedula: cedula || null,
        rol,
        activo,
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setGuardando(false)
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-3 border-t border-dashed pt-3"
    >
      <div className="flex flex-col gap-1">
        <Label>Nombre</Label>
        <Input value={nombre} onChange={(e) => setNombre(e.target.value)} />
      </div>
      <div className="flex flex-col gap-1">
        <Label>Teléfono</Label>
        <Input value={telefono} onChange={(e) => setTelefono(e.target.value)} required />
      </div>
      <div className="flex flex-col gap-1">
        <Label>Código Uninorte</Label>
        <Input value={codigo} onChange={(e) => setCodigo(e.target.value)} />
      </div>
      <div className="flex flex-col gap-1">
        <Label>Cédula</Label>
        <Input value={cedula} onChange={(e) => setCedula(e.target.value)} />
      </div>
      <div className="flex flex-col gap-1">
        <Label>Rol</Label>
        <Select value={rol} onValueChange={(v) => setRol(v as Rol)}>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {ROLES.map((r) => (
              <SelectItem key={r} value={r}>
                {r}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="flex items-center gap-2 self-end pb-2">
        <Checkbox checked={activo} onCheckedChange={(v) => setActivo(v === true)} id={`activo-${usuario.id}`} />
        <Label htmlFor={`activo-${usuario.id}`}>Activo</Label>
      </div>
      {error && <p className="col-span-full text-sm text-destructive">{error}</p>}
      <div className="col-span-full flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={onCancel} disabled={guardando}>
          Cancelar
        </Button>
        <Button type="submit" disabled={guardando}>
          Guardar
        </Button>
      </div>
    </form>
  )
}
