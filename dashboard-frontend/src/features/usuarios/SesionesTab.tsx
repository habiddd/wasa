import { useMemo, useState, type KeyboardEvent } from "react"

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
import { etiquetaRol, fetchSesiones, type Sesion } from "@/lib/api"
import { formatearFechaHoraLACS } from "@/lib/fechas"

import { ConversacionSesionDialog } from "./ConversacionSesionDialog"

type Orden = "reciente" | "usuario"

function fecha(valor: string | null) {
  return formatearFechaHoraLACS(valor)
}

function usoDe(s: Sesion) {
  return s.tipo_uso === "asignatura" ? `NRC ${s.nrc}` : "Particular"
}

// Props comunes para que una fila (<li> o <TableRow>) abra el modal de
// conversación con clic o teclado (Enter/Espacio), como cualquier otro
// control interactivo -- las filas no son <button> porque contienen varias
// celdas/columnas.
function propsFilaClicable(onAbrir: (s: Sesion) => void, s: Sesion) {
  return {
    onClick: () => onAbrir(s),
    role: "button" as const,
    tabIndex: 0,
    onKeyDown: (e: KeyboardEvent) => {
      if (e.key === "Enter") onAbrir(s)
      else if (e.key === " ") {
        e.preventDefault() // el espacio no debe hacer scroll de la página
        onAbrir(s)
      }
    },
    className:
      "cursor-pointer hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
  }
}

/** Pantallas angostas: una tarjeta por sesión, sin columnas. */
function ListaSesiones({ sesiones, onAbrir }: { sesiones: Sesion[]; onAbrir: (s: Sesion) => void }) {
  return (
    <ul className="divide-y rounded-md border">
      {sesiones.map((s) => {
        const { className: claseClicable, ...propsClic } = propsFilaClicable(onAbrir, s)
        return (
        <li
          key={s.id}
          {...propsClic}
          className={`space-y-1 px-3 py-2.5 text-sm ${claseClicable}`}
        >
          <span className="flex items-start justify-between gap-2">
            <span className="font-medium">{s.usuario_nombre || s.usuario_telefono}</span>
            <EstadoReservaBadge estado={s.estado} />
          </span>
          <span className="block text-xs text-muted-foreground">
            {etiquetaRol(s.usuario_rol)} · {s.recurso_nombre || "-"} · {usoDe(s)}
          </span>
          <span className="block text-xs text-muted-foreground">
            {fecha(s.inicio)} – {fecha(s.fin)}
          </span>
        </li>
        )
      })}
    </ul>
  )
}

/**
 * Desde ~670 px: tabla. Recurso y Uso tienen columna propia solo si hay
 * espacio; si no, van en una línea bajo el usuario. Creada es lo primero
 * que sobra. Recurso absorbe el espacio libre (con whitespace-normal) en
 * vez de que el navegador lo reparta entre columnas cortas.
 *
 * En pantallas muy anchas (@6xl) sube el tamaño de letra y el padding: hay
 * espacio de sobra para leer más cómodo, no solo para que la tabla se vea
 * ancha.
 */
function TablaSesiones({ sesiones, onAbrir }: { sesiones: Sesion[]; onAbrir: (s: Sesion) => void }) {
  return (
    <Table className="@6xl:text-base">
      <TableHeader>
        <TableRow>
          <TableHead className="@6xl:py-3">Usuario</TableHead>
          <TableHead className="hidden w-full @3xl:table-cell @6xl:py-3">Recurso</TableHead>
          <TableHead className="hidden @4xl:table-cell @6xl:py-3">Uso</TableHead>
          <TableHead className="@6xl:py-3">Horario</TableHead>
          <TableHead className="@6xl:py-3">Estado</TableHead>
          <TableHead className="hidden @5xl:table-cell @6xl:py-3">Creada</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {sesiones.map((s) => (
          <TableRow key={s.id} {...propsFilaClicable(onAbrir, s)}>
            <TableCell className="whitespace-normal @3xl:whitespace-nowrap @6xl:py-3">
              <p>{s.usuario_nombre || s.usuario_telefono}</p>
              <p className="text-xs text-muted-foreground">{etiquetaRol(s.usuario_rol)}</p>
              <p className="mt-0.5 text-xs text-muted-foreground @3xl:hidden">
                {s.recurso_nombre || "-"} · {usoDe(s)}
              </p>
            </TableCell>
            <TableCell className="hidden whitespace-normal @3xl:table-cell @6xl:py-3">
              {s.recurso_nombre || "-"}
            </TableCell>
            <TableCell className="hidden @4xl:table-cell @6xl:py-3">{usoDe(s)}</TableCell>
            <TableCell className="@6xl:py-3">
              <p>{fecha(s.inicio)}</p>
              <p className="text-xs text-muted-foreground">{fecha(s.fin)}</p>
            </TableCell>
            <TableCell className="@6xl:py-3">
              <EstadoReservaBadge estado={s.estado} />
            </TableCell>
            <TableCell className="hidden text-muted-foreground @5xl:table-cell @6xl:py-3">
              {fecha(s.creado_en)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

/** Alterna entre lista y tabla según el ancho disponible, como ReservasPanel. */
function VistaSesiones({
  sesiones,
  onAbrir,
}: {
  sesiones: Sesion[]
  onAbrir: (s: Sesion) => void
}) {
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
        <ListaSesiones sesiones={sesiones} onAbrir={onAbrir} />
      </div>
      <div className="hidden rounded-md border @2xl:block">
        <TablaSesiones sesiones={sesiones} onAbrir={onAbrir} />
      </div>
    </div>
  )
}

function SesionesPorUsuario({
  sesiones,
  onAbrir,
}: {
  sesiones: Sesion[]
  onAbrir: (s: Sesion) => void
}) {
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
              <Badge variant="outline">{etiquetaRol(grupo.rol)}</Badge>
              <span className="text-xs font-normal text-muted-foreground">
                {grupo.sesiones.length} sesión{grupo.sesiones.length === 1 ? "" : "es"}
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <VistaSesiones sesiones={grupo.sesiones} onAbrir={onAbrir} />
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
  // Sesión elegida para ver su conversación. Se guarda el objeto capturado
  // al hacer clic (no solo el id): el polling de 30 s de arriba recrea
  // `data` cada vez, pero el modal ya montado sigue usando esta copia fija
  // -- no se re-renderiza con datos nuevos ni vuelve a pedir el hilo por un
  // refresco del listado.
  const [abierta, setAbierta] = useState<Sesion | null>(null)

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
        <h1 className="text-xl font-semibold tracking-tight">
          Sesiones de uso
        </h1>
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
                  {etiquetaRol(r)}
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
        <p className="text-sm text-destructive-text">No se pudieron cargar las sesiones.</p>
      )}

      {data &&
        (orden === "usuario" ? (
          <SesionesPorUsuario sesiones={sesionesFiltradas} onAbrir={setAbierta} />
        ) : (
          <VistaSesiones sesiones={sesionesFiltradas} onAbrir={setAbierta} />
        ))}

      {/* Carga perezosa: este componente solo existe en el árbol mientras hay
          sesión elegida, así que su useEffect pide la conversación al montar
          (abrir) y no hay nada que limpiar hasta que se vuelva a abrir otra
          -- el listado de arriba nunca pide este endpoint. */}
      {abierta && <ConversacionSesionDialog sesion={abierta} onClose={() => setAbierta(null)} />}
    </div>
  )
}
