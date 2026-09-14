import { useMemo, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { EstadoReservaBadge } from "@/features/calendario/EstadoBadge"
import { usePolling } from "@/hooks/use-polling"
import { fetchSesiones, type Sesion } from "@/lib/api"
import { formatearFechaHoraCPA } from "@/lib/fechas"

type Orden = "reciente" | "usuario"

function fecha(valor: string | null) {
  return formatearFechaHoraCPA(valor)
}

function usoDe(s: Sesion) {
  return s.tipo_uso === "asignatura" ? `NRC ${s.nrc}` : "Particular"
}

/** Pantallas angostas: una tarjeta por sesión, sin columnas. */
function ListaSesiones({ sesiones }: { sesiones: Sesion[] }) {
  return (
    <ul className="divide-y rounded-md border">
      {sesiones.map((s) => (
        <li key={s.id} className="space-y-1 px-3 py-2.5 text-sm">
          <span className="flex items-start justify-between gap-2">
            <span className="font-medium">{s.usuario_nombre || s.usuario_telefono}</span>
            <EstadoReservaBadge estado={s.estado} />
          </span>
          <span className="block text-xs text-muted-foreground">
            {s.usuario_rol} · {s.recurso_nombre || "-"} · {usoDe(s)}
          </span>
          <span className="block text-xs text-muted-foreground">
            {fecha(s.inicio)} – {fecha(s.fin)}
          </span>
        </li>
      ))}
    </ul>
  )
}

/**
 * Desde ~670 px: tabla. Recurso y Uso tienen columna propia solo si hay
 * espacio; si no, van en una línea bajo el usuario. Creada es lo primero
 * que sobra.
 */
function TablaSesiones({ sesiones }: { sesiones: Sesion[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Usuario</TableHead>
          <TableHead className="hidden @3xl:table-cell">Recurso</TableHead>
          <TableHead className="hidden @4xl:table-cell">Uso</TableHead>
          <TableHead>Horario</TableHead>
          <TableHead>Estado</TableHead>
          <TableHead className="hidden @5xl:table-cell">Creada</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {sesiones.map((s) => (
          <TableRow key={s.id}>
            <TableCell className="whitespace-normal">
              <p>{s.usuario_nombre || s.usuario_telefono}</p>
              <p className="text-xs text-muted-foreground">{s.usuario_rol}</p>
              <p className="mt-0.5 text-xs text-muted-foreground @3xl:hidden">
                {s.recurso_nombre || "-"} · {usoDe(s)}
              </p>
            </TableCell>
            <TableCell className="hidden @3xl:table-cell">{s.recurso_nombre || "-"}</TableCell>
            <TableCell className="hidden @4xl:table-cell">{usoDe(s)}</TableCell>
            <TableCell>
              <p>{fecha(s.inicio)}</p>
              <p className="text-xs text-muted-foreground">{fecha(s.fin)}</p>
            </TableCell>
            <TableCell>
              <EstadoReservaBadge estado={s.estado} />
            </TableCell>
            <TableCell className="hidden text-muted-foreground @5xl:table-cell">
              {fecha(s.creado_en)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

/** Alterna entre lista y tabla según el ancho disponible, como ReservasPanel. */
function VistaSesiones({ sesiones }: { sesiones: Sesion[] }) {
  if (sesiones.length === 0) {
    return (
      <p className="rounded-md border p-4 text-sm text-muted-foreground">
        No hay sesiones para este filtro.
      </p>
    )
  }
  return (
    <div className="@container">
      <div className="@2xl:hidden">
        <ListaSesiones sesiones={sesiones} />
      </div>
      <div className="hidden rounded-md border @2xl:block">
        <TablaSesiones sesiones={sesiones} />
      </div>
    </div>
  )
}

function SesionesPorUsuario({ sesiones }: { sesiones: Sesion[] }) {
  const grupos = useMemo(() => {
    const porUsuario = new Map<number, { nombre: string; rol: string; sesiones: Sesion[] }>()
    for (const s of sesiones) {
      const etiqueta = s.usuario_nombre || s.usuario_telefono
      const grupo = porUsuario.get(s.usuario_id)
      if (grupo) {
        grupo.sesiones.push(s)
      } else {
        porUsuario.set(s.usuario_id, { nombre: etiqueta, rol: s.usuario_rol, sesiones: [s] })
      }
    }
    return [...porUsuario.values()].sort((a, b) => a.nombre.localeCompare(b.nombre))
  }, [sesiones])

  if (grupos.length === 0) {
    return <p className="text-sm text-muted-foreground">No hay sesiones para este filtro.</p>
  }

  return (
    <div className="space-y-3">
      {grupos.map((grupo) => (
        <Card key={grupo.nombre}>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              {grupo.nombre}
              <Badge variant="outline">{grupo.rol}</Badge>
              <span className="text-xs font-normal text-muted-foreground">
                {grupo.sesiones.length} sesión{grupo.sesiones.length === 1 ? "" : "es"}
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <VistaSesiones sesiones={grupo.sesiones} />
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

export function SesionesTab() {
  const { data, error, loading, reload } = usePolling(fetchSesiones, 30000)
  const [orden, setOrden] = useState<Orden>("reciente")
  const [rol, setRol] = useState("todos")

  const roles = useMemo(() => {
    const vistos = new Set<string>()
    for (const s of data ?? []) vistos.add(s.usuario_rol)
    return [...vistos].sort()
  }, [data])

  const sesionesFiltradas = useMemo(() => {
    const base = data ?? []
    return rol === "todos" ? base : base.filter((s) => s.usuario_rol === rol)
  }, [data, rol])

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Sesiones de uso
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          <Select value={orden} onValueChange={(v) => setOrden(v as Orden)}>
            <SelectTrigger size="sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="reciente">Más reciente</SelectItem>
              <SelectItem value="usuario">Por usuario</SelectItem>
            </SelectContent>
          </Select>
          <Select value={rol} onValueChange={setRol}>
            <SelectTrigger size="sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="todos">Todas las categorías</SelectItem>
              {roles.map((r) => (
                <SelectItem key={r} value={r}>
                  {r}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button size="sm" variant="outline" onClick={() => reload()}>
            Refrescar
          </Button>
        </div>
      </div>

      {loading && !data && (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      )}

      {error && !data && (
        <p className="text-sm text-destructive">No se pudieron cargar las sesiones.</p>
      )}

      {data &&
        (orden === "usuario" ? (
          <SesionesPorUsuario sesiones={sesionesFiltradas} />
        ) : (
          <VistaSesiones sesiones={sesionesFiltradas} />
        ))}
    </div>
  )
}
