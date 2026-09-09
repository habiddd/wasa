import { useState, type FormEvent } from "react"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
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

import { CampoCategoria } from "./CampoCategoria"
import { ESTADOS_ACTIVO, valoresCategoria, valoresSubcategoria } from "./tree-utils"

export function ActivoNuevoForm({
  tipo,
  equipos,
  espacios,
  onCancel,
  onCreate,
}: {
  tipo: TipoRecurso
  equipos: Recurso[]
  // Opciones del selector "Lugar / espacio": un equipo va en una sala, pero
  // una sala también puede ir dentro de otra (edificio > piso > sala), así
  // que este selector aplica a los dos tipos.
  espacios: Recurso[]
  onCancel: () => void
  onCreate: (cambios: CambiosActivo) => Promise<void>
}) {
  const [nombre, setNombre] = useState("")
  const [categoria, setCategoria] = useState("")
  const [subcategoria, setSubcategoria] = useState("")
  const [placa, setPlaca] = useState("")
  const [marca, setMarca] = useState("")
  const [modelo, setModelo] = useState("")
  const [identificador, setIdentificador] = useState("")
  const [ubicacion, setUbicacion] = useState("")
  const [descripcion, setDescripcion] = useState("")
  const [padreId, setPadreId] = useState<string>("")
  const [espacioId, setEspacioId] = useState<string>("")
  // Mientras el usuario no haya tocado el selector de Lugar a mano, elegir
  // un padre pre-rellena el lugar con el del padre (el hijo "va con el
  // padre por defecto"). Una vez que el usuario elige un lugar explícito,
  // ya no se lo pisa al cambiar de padre.
  const [lugarTocado, setLugarTocado] = useState(false)
  const [requiereSeguro, setRequiereSeguro] = useState(false)
  const [estado, setEstado] = useState<EstadoRecurso>("disponible")
  const [error, setError] = useState<string | null>(null)
  const [guardando, setGuardando] = useState(false)

  // `equipos` acá ya viene sin filtrar por categoría/subcategoría (ver
  // ActivosPanel), así que sirve tal cual para alimentar el Select con
  // todo lo que ya se usa en el catálogo.
  const categoriasConocidas = valoresCategoria(equipos)
  const subcategoriasConocidas = valoresSubcategoria(equipos)

  function handlePadreChange(valor: string) {
    const nuevoPadreId = valor === "__ninguno__" ? "" : valor
    setPadreId(nuevoPadreId)
    if (!lugarTocado) {
      const padre = equipos.find((r) => String(r.id) === nuevoPadreId)
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
    // La Categoría es obligatoria para equipos. Como el Select puede
    // quedar sin nada elegido (no hay opción vacía, a propósito), la
    // validación del "required" que antes traía el Input hay que hacerla
    // a mano acá.
    if (tipo === "equipo" && !categoria.trim()) {
      setError("La categoría es obligatoria")
      return
    }
    setGuardando(true)
    try {
      const cambios: CambiosActivo = {
        tipo,
        nombre,
        ubicacion: ubicacion || null,
        descripcion: descripcion || null,
        estado,
        espacio_id: espacioId ? Number(espacioId) : null,
      }
      if (tipo === "equipo") {
        cambios.categoria = categoria || null
        cambios.subcategoria = subcategoria || null
        cambios.placa = placa ? placa.toUpperCase() : null
        cambios.marca = marca || null
        cambios.modelo = modelo || null
        cambios.identificador = identificador || null
        cambios.padre_id = padreId ? Number(padreId) : null
        cambios.requiere_seguro = requiereSeguro
      }
      await onCreate(cambios)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setGuardando(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">
          {tipo === "equipo" ? "Nuevo activo" : "Nuevo lugar"}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <form
          onSubmit={handleSubmit}
          className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-3"
        >
          <div className="col-span-full flex flex-col gap-1">
            <Label>Nombre</Label>
            <Input value={nombre} onChange={(e) => setNombre(e.target.value)} required />
          </div>

          {tipo === "equipo" && (
            <>
              <div className="flex flex-col gap-1">
                <Label>Categoría</Label>
                <CampoCategoria
                  id="nuevo-categoria"
                  valor={categoria}
                  opciones={categoriasConocidas}
                  onChange={setCategoria}
                  placeholder="Agrupación gruesa, ej. grabación audiovisual"
                  textoNueva="+ Nueva categoría…"
                  required
                />
              </div>
              <div className="flex flex-col gap-1">
                <Label>Subcategoría</Label>
                <CampoCategoria
                  id="nuevo-subcategoria"
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
                  required
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
            </>
          )}

          <div className="flex flex-col gap-1">
            <Label>Ubicación</Label>
            <Input value={ubicacion} onChange={(e) => setUbicacion(e.target.value)} />
          </div>

          <div className="flex flex-col gap-1">
            <Label>Lugar / espacio</Label>
            <Select value={espacioId || "__ninguno__"} onValueChange={handleEspacioChange}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__ninguno__">— Sin lugar —</SelectItem>
                {espacios.map((r) => (
                  <SelectItem key={r.id} value={String(r.id)}>
                    {r.nombre}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="col-span-full flex flex-col gap-1">
            <Label>Descripción</Label>
            <Textarea rows={2} value={descripcion} onChange={(e) => setDescripcion(e.target.value)} />
          </div>

          {tipo === "equipo" && (
            <div className="col-span-full flex flex-col gap-1">
              <Label>¿Es hijo de otro activo?</Label>
              <Select value={padreId || "__ninguno__"} onValueChange={handlePadreChange}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__ninguno__">
                    No, es un activo independiente (padre)
                  </SelectItem>
                  {equipos.map((r) => (
                    <SelectItem key={r.id} value={String(r.id)}>
                      {r.nombre}
                      {r.placa ? ` (${r.placa})` : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          {tipo === "equipo" && (
            <div className="flex items-center gap-2 self-end pb-2">
              <Checkbox
                id="nuevo-requiere-seguro"
                checked={requiereSeguro}
                onCheckedChange={(v) => setRequiereSeguro(v === true)}
              />
              <Label htmlFor="nuevo-requiere-seguro">Requiere seguro</Label>
            </div>
          )}

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
              {tipo === "equipo" ? "Crear activo" : "Crear lugar"}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}
