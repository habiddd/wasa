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

import { useActivosContext } from "./activos-context"
import { CampoCategoria } from "./CampoCategoria"
import {
  descendientesDe,
  espaciosDisponibles,
  ESTADOS_ACTIVO,
  hijosSincronizados,
  valoresCategoria,
  valoresSubcategoria,
} from "./tree-utils"

export function ActivoEditForm({
  recurso,
  lista,
  onCancel,
  onSave,
}: {
  recurso: Recurso
  lista: Recurso[]
  onCancel: () => void
  onSave: (cambios: CambiosActivo & { propagar_espacio?: boolean }) => Promise<void>
}) {
  // El selector de Padre usa `lista` (viene filtrada por el panel que llama
  // a este form -- ej. solo equipos de una categoría). El de Lugar/espacio
  // en cambio necesita TODOS los activos (useActivosContext().activos, no
  // `lista`): en ActivosPanel `lista` solo trae equipos, así que un
  // selector de espacios basado en `lista` quedaría siempre vacío.
  const { activos } = useActivosContext()

  const [nombre, setNombre] = useState(recurso.nombre)
  const [tipo, setTipo] = useState<TipoRecurso>(recurso.tipo)
  const [categoria, setCategoria] = useState(recurso.categoria ?? "")
  const [subcategoria, setSubcategoria] = useState(recurso.subcategoria ?? "")
  const [placa, setPlaca] = useState(recurso.placa ?? "")
  const [marca, setMarca] = useState(recurso.marca ?? "")
  const [modelo, setModelo] = useState(recurso.modelo ?? "")
  const [identificador, setIdentificador] = useState(recurso.identificador ?? "")
  const [ubicacion, setUbicacion] = useState(recurso.ubicacion ?? "")
  const [descripcion, setDescripcion] = useState(recurso.descripcion ?? "")
  const [padreId, setPadreId] = useState<string>(
    recurso.padre_id != null ? String(recurso.padre_id) : ""
  )
  const [espacioId, setEspacioId] = useState<string>(
    recurso.espacio_id != null ? String(recurso.espacio_id) : ""
  )
  // Mientras el usuario no toque el selector de Lugar a mano, cambiar el
  // Padre pre-rellena el Lugar con el del nuevo padre (el "va con el padre
  // por defecto" también aplica al elegir el padre desde este form).
  const [lugarTocado, setLugarTocado] = useState(false)
  const [propagarEspacio, setPropagarEspacio] = useState(true)
  const [requiereConfirmacion, setRequiereConfirmacion] = useState(recurso.requiere_confirmacion)
  const [estado, setEstado] = useState<EstadoRecurso>(recurso.estado)
  const [error, setError] = useState<string | null>(null)
  const [guardando, setGuardando] = useState(false)

  const excluidos = new Set([
    recurso.id,
    ...descendientesDe(recurso.id, lista).map((r) => r.id),
  ])
  const opcionesPadre = lista.filter((r) => !excluidos.has(r.id))

  // Contra TODOS los activos, no solo `lista`, por el mismo motivo de
  // arriba. Sin el propio recurso: si era un espacio y en este form se lo
  // pasa a equipo, no puede quedar dentro de sí mismo.
  const opcionesEspacio = espaciosDisponibles(activos).filter((r) => r.id !== recurso.id)

  // Valores de Categoría/Subcategoría ya usados -- contra TODOS los
  // activos (contexto), no contra `lista` (que viene filtrada), para que
  // el Select ofrezca lo que ya existe en el catálogo completo y no solo
  // dentro del filtro actual.
  const categoriasConocidas = valoresCategoria(activos)
  const subcategoriasConocidas = valoresSubcategoria(activos)

  // Un espacio no tiene padre ni lugar (no se anidan lugares): el form
  // oculta esos dos selectores y los manda en null.
  const esEspacio = tipo === "espacio"
  const espacioIdNumerico = esEspacio || !espacioId ? null : Number(espacioId)
  const hijosAMover = hijosSincronizados(recurso, activos)
  const mostrarPropagacion =
    !esEspacio && hijosAMover.length > 0 && espacioIdNumerico !== recurso.espacio_id

  function handlePadreChange(valor: string) {
    const nuevoPadreId = valor === "__ninguno__" ? "" : valor
    setPadreId(nuevoPadreId)
    if (!lugarTocado) {
      const padre = activos.find((r) => String(r.id) === nuevoPadreId)
      setEspacioId(padre?.espacio_id != null ? String(padre.espacio_id) : "")
    }
  }

  function handleEspacioChange(valor: string) {
    setLugarTocado(true)
    setEspacioId(valor === "__ninguno__" ? "" : valor)
  }

  async function handleSubmit(ev: FormEvent) {
    ev.preventDefault()
    setError(null)
    setGuardando(true)
    try {
      await onSave({
        nombre,
        tipo,
        categoria: categoria || null,
        subcategoria: subcategoria || null,
        placa: placa ? placa.toUpperCase() : null,
        marca: marca || null,
        modelo: modelo || null,
        identificador: identificador || null,
        ubicacion: ubicacion || null,
        descripcion: descripcion || null,
        padre_id: esEspacio || !padreId ? null : Number(padreId),
        espacio_id: espacioIdNumerico,
        requiere_confirmacion: requiereConfirmacion,
        estado,
        ...(mostrarPropagacion ? { propagar_espacio: propagarEspacio } : {}),
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
        <CampoCategoria
          id={`categoria-${recurso.id}`}
          valor={categoria}
          opciones={categoriasConocidas}
          onChange={setCategoria}
          placeholder="Agrupación gruesa, ej. grabación audiovisual"
          textoNueva="+ Nueva categoría…"
        />
      </div>
      <div className="flex flex-col gap-1">
        <Label>Subcategoría</Label>
        <CampoCategoria
          id={`subcategoria-${recurso.id}`}
          valor={subcategoria}
          opciones={subcategoriasConocidas}
          onChange={setSubcategoria}
          placeholder="Tipo de equipo, ej. cámara"
          textoNueva="+ Nueva subcategoría…"
          textoSinValor="— Sin subcategoría —"
        />
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
      {!esEspacio && (
        <>
          <div className="flex flex-col gap-1">
            <Label>Padre (activo del que depende)</Label>
            <Select value={padreId || "__ninguno__"} onValueChange={handlePadreChange}>
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
          <div className="flex flex-col gap-1">
            <Label>Lugar / espacio</Label>
            <Select value={espacioId || "__ninguno__"} onValueChange={handleEspacioChange}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__ninguno__">— Sin lugar —</SelectItem>
                {opcionesEspacio.map((r) => (
                  <SelectItem key={r.id} value={String(r.id)}>
                    {r.nombre}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </>
      )}
      {mostrarPropagacion && (
        <div className="col-span-full flex items-center gap-2">
          <Checkbox
            id={`propagar-espacio-${recurso.id}`}
            checked={propagarEspacio}
            onCheckedChange={(v) => setPropagarEspacio(v === true)}
          />
          <Label htmlFor={`propagar-espacio-${recurso.id}`}>
            Mover también los {hijosAMover.length} hijo{hijosAMover.length === 1 ? "" : "s"} que
            está{hijosAMover.length === 1 ? "" : "n"} en el mismo lugar
          </Label>
        </div>
      )}
      <div className="flex items-center gap-2 self-end pb-2">
        <Checkbox
          id={`confirmacion-${recurso.id}`}
          checked={requiereConfirmacion}
          onCheckedChange={(v) => setRequiereConfirmacion(v === true)}
        />
        <Label htmlFor={`confirmacion-${recurso.id}`}>Requiere confirmación</Label>
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
