import { useState } from "react"

import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { usePolling } from "@/hooks/use-polling"
import { actualizarUsuario, eliminarUsuario, fetchUsuarios, type CambiosUsuario, type Usuario } from "@/lib/api"
import { formatearFechaHoraCPA } from "@/lib/fechas"

import { UsuarioEditForm } from "./UsuarioEditForm"

type PropsVista = {
  usuarios: Usuario[]
  editandoId: number | null
  onToggleEditar: (id: number) => void
  onGuardar: (id: number, cambios: CambiosUsuario) => Promise<void>
  onEliminar: (id: number) => Promise<void>
}

function codigoYCedula(u: Usuario) {
  const partes = [u.codigo_uninorte && `Cód. ${u.codigo_uninorte}`, u.cedula && `C.C. ${u.cedula}`].filter(
    Boolean
  )
  return partes.length ? partes.join(" · ") : null
}

/** Pantallas angostas: una tarjeta por usuario, sin columnas. */
function ListaUsuarios({ usuarios, editandoId, onToggleEditar, onGuardar, onEliminar }: PropsVista) {
  return (
    <ul className="divide-y rounded-md border">
      {usuarios.map((u) => (
        <li key={u.id} className="space-y-1.5 px-3 py-2.5 text-sm">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="font-medium">{u.nombre || "-"}</p>
              <p className="text-xs text-muted-foreground">{u.telefono_wa}</p>
            </div>
            <Button size="sm" variant="outline" onClick={() => onToggleEditar(u.id)}>
              Editar
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            {u.rol} · {u.activo ? "activo" : "inactivo"} · {formatearFechaHoraCPA(u.creado_en)}
          </p>
          {codigoYCedula(u) && (
            <p className="text-xs text-muted-foreground">{codigoYCedula(u)}</p>
          )}
          {editandoId === u.id && (
            <UsuarioEditForm
              usuario={u}
              onCancel={() => onToggleEditar(u.id)}
              onSave={(cambios) => onGuardar(u.id, cambios)}
              onDelete={() => onEliminar(u.id)}
            />
          )}
        </li>
      ))}
    </ul>
  )
}

/**
 * Desde ~670 px: tabla. Código Uninorte y Cédula tienen columna propia solo
 * si hay espacio; si no, van en una línea bajo el usuario. Registrado es lo
 * primero que sobra. Nombre absorbe el espacio libre (con whitespace-normal)
 * en vez de que el navegador lo reparta entre columnas cortas.
 *
 * En pantallas muy anchas (@6xl) sube el tamaño de letra y el padding: hay
 * espacio de sobra para leer más cómodo, no solo para que la tabla se vea
 * ancha.
 */
function TablaUsuarios({ usuarios, editandoId, onToggleEditar, onGuardar, onEliminar }: PropsVista) {
  return (
    <div className="rounded-md border">
      <Table className="@6xl:text-base">
        <TableHeader>
          <TableRow>
            <TableHead className="w-full @6xl:py-3">Nombre</TableHead>
            <TableHead className="@6xl:py-3">Teléfono</TableHead>
            <TableHead className="hidden @4xl:table-cell @6xl:py-3">Código Uninorte</TableHead>
            <TableHead className="hidden @4xl:table-cell @6xl:py-3">Cédula</TableHead>
            <TableHead className="@6xl:py-3">Rol</TableHead>
            <TableHead className="@6xl:py-3">Activo</TableHead>
            <TableHead className="hidden @5xl:table-cell @6xl:py-3">Registrado</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {usuarios.map((u) => (
            <>
              <TableRow key={u.id}>
                <TableCell className="whitespace-normal @6xl:py-3">
                  {u.nombre || "-"}
                  {codigoYCedula(u) && (
                    <p className="mt-0.5 text-xs text-muted-foreground @4xl:hidden">
                      {codigoYCedula(u)}
                    </p>
                  )}
                </TableCell>
                <TableCell className="@6xl:py-3">{u.telefono_wa}</TableCell>
                <TableCell className="hidden @4xl:table-cell @6xl:py-3">
                  {u.codigo_uninorte || "-"}
                </TableCell>
                <TableCell className="hidden @4xl:table-cell @6xl:py-3">
                  {u.cedula || "-"}
                </TableCell>
                <TableCell className="@6xl:py-3">{u.rol}</TableCell>
                <TableCell className="@6xl:py-3">{u.activo ? "sí" : "no"}</TableCell>
                <TableCell className="hidden text-muted-foreground @5xl:table-cell @6xl:py-3">
                  {formatearFechaHoraCPA(u.creado_en)}
                </TableCell>
                <TableCell className="@6xl:py-3">
                  <Button size="sm" variant="outline" onClick={() => onToggleEditar(u.id)}>
                    Editar
                  </Button>
                </TableCell>
              </TableRow>
              {editandoId === u.id && (
                <TableRow key={`${u.id}-form`}>
                  <TableCell colSpan={8}>
                    <UsuarioEditForm
                      usuario={u}
                      onCancel={() => onToggleEditar(u.id)}
                      onSave={(cambios) => onGuardar(u.id, cambios)}
                      onDelete={() => onEliminar(u.id)}
                    />
                  </TableCell>
                </TableRow>
              )}
            </>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

export function ListaUsuariosPanel() {
  const { data, error, reload } = usePolling(fetchUsuarios, 30000)
  const [editandoId, setEditandoId] = useState<number | null>(null)

  function toggleEditar(id: number) {
    setEditandoId((actual) => (actual === id ? null : id))
  }

  async function handleSave(id: number, cambios: CambiosUsuario) {
    await actualizarUsuario(id, cambios)
    setEditandoId(null)
    await reload()
  }

  async function handleDelete(id: number) {
    await eliminarUsuario(id)
    setEditandoId(null)
    await reload()
  }

  return (
    <div className="@container space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Usuarios registrados
        </h2>
        <Button size="sm" variant="outline" onClick={() => reload()}>
          Refrescar
        </Button>
      </div>

      {error && !data && (
        <p className="text-sm text-destructive">No se pudieron cargar los usuarios.</p>
      )}

      {data && data.length === 0 && (
        <p className="rounded-md border p-4 text-sm text-muted-foreground">
          No hay usuarios registrados.
        </p>
      )}

      {data && data.length > 0 && (
        <>
          <div className="@2xl:hidden">
            <ListaUsuarios
              usuarios={data}
              editandoId={editandoId}
              onToggleEditar={toggleEditar}
              onGuardar={handleSave}
              onEliminar={handleDelete}
            />
          </div>
          <div className="hidden @2xl:block">
            <TablaUsuarios
              usuarios={data}
              editandoId={editandoId}
              onToggleEditar={toggleEditar}
              onGuardar={handleSave}
              onEliminar={handleDelete}
            />
          </div>
        </>
      )}
    </div>
  )
}
