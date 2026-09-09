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
import { Textarea } from "@/components/ui/textarea"
import type { CambiosActivo, EstadoRecurso, Recurso, TipoRecurso } from "@/lib/api"

import { descendientesDe, ESTADOS_ACTIVO } from "./tree-utils"

export function ActivoEditForm({
  recurso,
  lista,
  onCancel,
  onSave,
}: {
  recurso: Recurso
  lista: Recurso[]
  onCancel: () => void
  onSave: (cambios: CambiosActivo) => Promise<void>
}) {
  const [nombre, setNombre] = useState(recurso.nombre)
  const [tipo, setTipo] = useState<TipoRecurso>(recurso.tipo)
  const [categoria, setCategoria] = useState(recurso.categoria ?? "")
  const [placa, setPlaca] = useState(recurso.placa ?? "")
  const [marca, setMarca] = useState(recurso.marca ?? "")
  const [modelo, setModelo] = useState(recurso.modelo ?? "")
  const [identificador, setIdentificador] = useState(recurso.identificador ?? "")
  const [ubicacion, setUbicacion] = useState(recurso.ubicacion ?? "")
  const [descripcion, setDescripcion] = useState(recurso.descripcion ?? "")
  const [padreId, setPadreId] = useState<string>(
    recurso.padre_id != null ? String(recurso.padre_id) : ""
  )
  const [requiereSeguro, setRequiereSeguro] = useState(recurso.requiere_seguro)
  const [estado, setEstado] = useState<EstadoRecurso>(recurso.estado)
  const [error, setError] = useState<string | null>(null)
  const [guardando, setGuardando] = useState(false)

  const excluidos = new Set([
    recurso.id,
    ...descendientesDe(recurso.id, lista).map((r) => r.id),
  ])
  const opcionesPadre = lista.filter((r) => !excluidos.has(r.id))

  async function handleSubmit(ev: FormEvent) {
    ev.preventDefault()
    setError(null)
    setGuardando(true)
    try {
      await onSave({
        nombre,
        tipo,
        categoria: categoria || null,
        placa: placa ? placa.toUpperCase() : null,
        marca: marca || null,
        modelo: modelo || null,
        identificador: identificador || null,
        ubicacion: ubicacion || null,
        descripcion: descripcion || null,
        padre_id: padreId ? Number(padreId) : null,
        requiere_seguro: requiereSeguro,
        estado,
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
      <div className="col-span-full flex flex-col gap-1">
        <Label>Nombre</Label>
        <Input value={nombre} onChange={(e) => setNombre(e.target.value)} required />
      </div>
      <div className="flex flex-col gap-1">
        <Label>Tipo</Label>
        <Select value={tipo} onValueChange={(v) => setTipo(v as TipoRecurso)}>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="equipo">Equipo</SelectItem>
            <SelectItem value="espacio">Espacio</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="flex flex-col gap-1">
        <Label>Categoría</Label>
        <Input value={categoria} onChange={(e) => setCategoria(e.target.value)} />
      </div>
      <div className="flex flex-col gap-1">
        <Label>Placa</Label>
        <Input
          value={placa}
          onChange={(e) => setPlaca(e.target.value)}
          placeholder="Padre: 8 dígitos · Hijo: 7-10 caracteres"
        />
      </div>
      <div className="flex flex-col gap-1">
        <Label>Marca</Label>
        <Input value={marca} onChange={(e) => setMarca(e.target.value)} />
      </div>
      <div className="flex flex-col gap-1">
        <Label>Modelo</Label>
        <Input value={modelo} onChange={(e) => setModelo(e.target.value)} />
      </div>
      <div className="flex flex-col gap-1">
        <Label>Identificador / serie</Label>
        <Input value={identificador} onChange={(e) => setIdentificador(e.target.value)} />
      </div>
      <div className="flex flex-col gap-1">
        <Label>Ubicación</Label>
        <Input value={ubicacion} onChange={(e) => setUbicacion(e.target.value)} />
      </div>
      <div className="col-span-full flex flex-col gap-1">
        <Label>Descripción</Label>
        <Textarea rows={2} value={descripcion} onChange={(e) => setDescripcion(e.target.value)} />
      </div>
      <div className="flex flex-col gap-1">
        <Label>Padre (activo del que depende)</Label>
        <Select value={padreId || "__ninguno__"} onValueChange={(v) => setPadreId(v === "__ninguno__" ? "" : v)}>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__ninguno__">— Ninguno —</SelectItem>
            {opcionesPadre.map((r) => (
              <SelectItem key={r.id} value={String(r.id)}>
                {r.nombre}
                {r.placa ? ` (${r.placa})` : ""}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="flex items-center gap-2 self-end pb-2">
        <Checkbox
          id={`seguro-${recurso.id}`}
          checked={requiereSeguro}
          onCheckedChange={(v) => setRequiereSeguro(v === true)}
        />
        <Label htmlFor={`seguro-${recurso.id}`}>Requiere seguro</Label>
      </div>
      <div className="flex flex-col gap-1">
        <Label>Estado</Label>
        <Select value={estado} onValueChange={(v) => setEstado(v as EstadoRecurso)}>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(Object.keys(ESTADOS_ACTIVO) as EstadoRecurso[]).map((valor) => (
              <SelectItem key={valor} value={valor}>
                {ESTADOS_ACTIVO[valor].texto}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
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
