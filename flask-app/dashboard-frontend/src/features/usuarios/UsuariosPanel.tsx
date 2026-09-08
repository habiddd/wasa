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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { usePolling } from "@/hooks/use-polling"
import { actualizarUsuario, fetchUsuarios, type CambiosUsuario } from "@/lib/api"

import { SesionesTab } from "./SesionesTab"
import { UsuarioEditForm } from "./UsuarioEditForm"

function ListaUsuariosTab() {
  const { data, error, reload } = usePolling(fetchUsuarios, 30000)
  const [editandoId, setEditandoId] = useState<number | null>(null)

  async function handleSave(id: number, cambios: CambiosUsuario) {
    await actualizarUsuario(id, cambios)
    setEditandoId(null)
    await reload()
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Usuarios registrados
        </h2>
        <Button size="sm" variant="outline" onClick={() => reload()}>
          Refrescar
        </Button>
      </div>

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Nombre</TableHead>
              <TableHead>Teléfono</TableHead>
              <TableHead>Código Uninorte</TableHead>
              <TableHead>Cédula</TableHead>
              <TableHead>Rol</TableHead>
              <TableHead>Activo</TableHead>
              <TableHead>Registrado</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {error && !data && (
              <TableRow>
                <TableCell colSpan={8}>No se pudieron cargar los usuarios.</TableCell>
              </TableRow>
            )}
            {data && data.length === 0 && (
              <TableRow>
                <TableCell colSpan={8}>No hay usuarios registrados.</TableCell>
              </TableRow>
            )}
            {data?.map((u) => (
              <>
                <TableRow key={u.id}>
                  <TableCell>{u.nombre || "-"}</TableCell>
                  <TableCell>{u.telefono_wa}</TableCell>
                  <TableCell>{u.codigo_uninorte || "-"}</TableCell>
                  <TableCell>{u.cedula || "-"}</TableCell>
                  <TableCell>{u.rol}</TableCell>
                  <TableCell>{u.activo ? "sí" : "no"}</TableCell>
                  <TableCell>
                    {u.creado_en ? new Date(u.creado_en).toLocaleString() : "-"}
                  </TableCell>
                  <TableCell>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setEditandoId(editandoId === u.id ? null : u.id)}
                    >
                      Editar
                    </Button>
                  </TableCell>
                </TableRow>
                {editandoId === u.id && (
                  <TableRow key={`${u.id}-form`}>
                    <TableCell colSpan={8}>
                      <UsuarioEditForm
                        usuario={u}
                        onCancel={() => setEditandoId(null)}
                        onSave={(cambios) => handleSave(u.id, cambios)}
                      />
                    </TableCell>
                  </TableRow>
                )}
              </>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}

export function UsuariosPanel() {
  return (
    <Tabs defaultValue="usuarios" className="gap-4">
      <TabsList>
        <TabsTrigger value="usuarios">Usuarios</TabsTrigger>
        <TabsTrigger value="sesiones">Sesiones</TabsTrigger>
      </TabsList>
      <TabsContent value="usuarios">
        <ListaUsuariosTab />
      </TabsContent>
      <TabsContent value="sesiones">
        <SesionesTab />
      </TabsContent>
    </Tabs>
  )
}
