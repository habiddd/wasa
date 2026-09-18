import { useEffect, useMemo, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { fetchUsuarios, type ClienteNuevo, type Usuario } from "@/lib/api"

export type SeleccionCliente =
  | { tipo: "existente"; usuario: Usuario }
  | { tipo: "nuevo"; datos: ClienteNuevo }
  | null

const DATOS_NUEVO_VACIOS: ClienteNuevo = { nombre: "", telefono: "", codigo_uninorte: "", cedula: "" }

function normalizar(texto: string) {
  return texto
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
}

/**
 * "¿Para quién es la reserva?" del formulario de creación (NuevaReservaDialog):
 * un cliente ya registrado (por WhatsApp o creado antes desde el dashboard) o
 * uno nuevo, con los mismos datos que pide el bot al registrarse.
 */
export function SelectorCliente({
  seleccion,
  onChange,
  disabled,
}: {
  seleccion: SeleccionCliente
  onChange: (seleccion: SeleccionCliente) => void
  disabled?: boolean
}) {
  const [modo, setModo] = useState<"existente" | "nuevo">(seleccion?.tipo ?? "existente")
  const [usuarios, setUsuarios] = useState<Usuario[]>([])
  const [busqueda, setBusqueda] = useState("")

  // Incluye a medio registrar (y creados antes desde el dashboard sin ID ni
  // cédula): acá también deben poder elegirse, a diferencia del resto del
  // dashboard (ver GET /api/usuarios?incluir_incompletos=1).
  useEffect(() => {
    fetchUsuarios({ incluirIncompletos: true })
      .then(setUsuarios)
      .catch(() => {})
  }, [])

  const resultados = useMemo(() => {
    const q = normalizar(busqueda.trim())
    if (!q) return []
    return usuarios
      .filter((u) =>
        [u.nombre, u.telefono_wa, u.codigo_uninorte, u.cedula]
          .filter((campo): campo is string => !!campo)
          .some((campo) => normalizar(campo).includes(q))
      )
      .slice(0, 8)
  }, [usuarios, busqueda])

  function elegirModo(nuevo: "existente" | "nuevo") {
    setModo(nuevo)
    setBusqueda("")
    onChange(null)
  }

  function actualizarNuevo(campo: keyof ClienteNuevo, valor: string) {
    const actual = seleccion?.tipo === "nuevo" ? seleccion.datos : DATOS_NUEVO_VACIOS
    onChange({ tipo: "nuevo", datos: { ...actual, [campo]: valor } })
  }

  const clienteExistente = seleccion?.tipo === "existente" ? seleccion.usuario : null
  const datosNuevo = seleccion?.tipo === "nuevo" ? seleccion.datos : DATOS_NUEVO_VACIOS

  return (
    <div className="space-y-2">
      <Label>¿Para quién es la reserva?</Label>
      <div className="flex gap-2">
        <Button
          type="button"
          size="sm"
          variant={modo === "existente" ? "default" : "outline"}
          onClick={() => elegirModo("existente")}
          disabled={disabled}
        >
          Usuario existente
        </Button>
        <Button
          type="button"
          size="sm"
          variant={modo === "nuevo" ? "default" : "outline"}
          onClick={() => elegirModo("nuevo")}
          disabled={disabled}
        >
          Usuario nuevo
        </Button>
      </div>

      {modo === "existente" &&
        (clienteExistente ? (
          <div className="flex items-center justify-between gap-2 rounded-md border p-2 text-sm">
            <div className="min-w-0">
              <p className="truncate">{clienteExistente.nombre ?? "Sin nombre"}</p>
              <p className="truncate text-xs text-muted-foreground">
                {clienteExistente.telefono_wa}
                {clienteExistente.codigo_uninorte ? ` · ${clienteExistente.codigo_uninorte}` : ""}
              </p>
            </div>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => onChange(null)}
              disabled={disabled}
            >
              Cambiar
            </Button>
          </div>
        ) : (
          <div className="space-y-2">
            <Input
              placeholder="Buscar por nombre, teléfono, ID uninorte o cédula"
              value={busqueda}
              onChange={(e) => setBusqueda(e.target.value)}
              disabled={disabled}
            />
            {busqueda.trim() &&
              (resultados.length > 0 ? (
                <ul className="max-h-48 divide-y overflow-y-auto rounded-md border">
                  {resultados.map((u) => (
                    <li key={u.id}>
                      <button
                        type="button"
                        onClick={() => onChange({ tipo: "existente", usuario: u })}
                        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-muted/50"
                      >
                        <span className="min-w-0">
                          <span className="block truncate">{u.nombre ?? "Sin nombre"}</span>
                          <span className="block truncate text-xs text-muted-foreground">
                            {u.telefono_wa}
                            {u.codigo_uninorte ? ` · ${u.codigo_uninorte}` : ""}
                          </span>
                        </span>
                        {u.registro_completo === false && (
                          <Badge variant="outline" className="shrink-0">
                            Registro incompleto
                          </Badge>
                        )}
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-muted-foreground">
                  No hay coincidencias. Usa «Usuario nuevo».
                </p>
              ))}
          </div>
        ))}

      {modo === "nuevo" && (
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="nuevo-cliente-nombre">Nombre completo</Label>
            <Input
              id="nuevo-cliente-nombre"
              value={datosNuevo.nombre}
              onChange={(e) => actualizarNuevo("nombre", e.target.value)}
              disabled={disabled}
              required
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="nuevo-cliente-telefono">Teléfono (WhatsApp)</Label>
            <Input
              id="nuevo-cliente-telefono"
              value={datosNuevo.telefono}
              onChange={(e) => actualizarNuevo("telefono", e.target.value)}
              disabled={disabled}
              required
            />
            <p className="text-xs text-muted-foreground">Con o sin 57, ej. 3001234567</p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="nuevo-cliente-codigo">ID uninorte (opcional)</Label>
              <Input
                id="nuevo-cliente-codigo"
                value={datosNuevo.codigo_uninorte ?? ""}
                onChange={(e) => actualizarNuevo("codigo_uninorte", e.target.value)}
                disabled={disabled}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="nuevo-cliente-cedula">Cédula (opcional)</Label>
              <Input
                id="nuevo-cliente-cedula"
                value={datosNuevo.cedula ?? ""}
                onChange={(e) => actualizarNuevo("cedula", e.target.value)}
                disabled={disabled}
              />
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            Si dejas el ID o la cédula vacíos, el bot se los pedirá cuando el usuario escriba. Si
            el teléfono ya existe, la reserva se asigna a ese usuario.
          </p>
        </div>
      )}
    </div>
  )
}
