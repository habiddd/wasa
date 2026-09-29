import { useMemo, useState } from "react"
import { Pencil, X } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
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
import { fechaCivilLarga, hoy } from "@/features/calendario/calendario-utils"
import { eliminarExcepcion, type DiaExcepcion, type OrigenExcepcion } from "@/lib/api"

import { detalleDia, ESTILO_ORIGEN, useExcepciones } from "./excepciones-utils"

type FiltroMostrar = "todas" | "bloqueadas" | "manuales" | "festivos" | "dia_semana" | "desbloqueadas"

const FILTROS: { valor: FiltroMostrar; etiqueta: string }[] = [
  { valor: "todas", etiqueta: "Todas" },
  { valor: "bloqueadas", etiqueta: "Bloqueadas" },
  { valor: "manuales", etiqueta: "Bloqueos manuales" },
  { valor: "festivos", etiqueta: "Festivos" },
  { valor: "dia_semana", etiqueta: "Día de la semana" },
  { valor: "desbloqueadas", etiqueta: "Desbloqueadas" },
]

const MESES = [
  "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
  "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]

function cumpleFiltro(dia: DiaExcepcion, filtro: FiltroMostrar): boolean {
  if (filtro === "todas") return true
  if (filtro === "bloqueadas") return dia.bloqueada
  if (filtro === "desbloqueadas") return dia.origen === "desbloqueada"
  if (filtro === "manuales") return dia.origen === "manual"
  const origenes: Record<"festivos" | "dia_semana", OrigenExcepcion> = {
    festivos: "festivo",
    dia_semana: "dia_semana",
  }
  return dia.origen === origenes[filtro as "festivos" | "dia_semana"]
}

function OrigenBadge({ dia }: { dia: DiaExcepcion }) {
  if (!dia.origen) {
    return <Badge variant="outline">Festivo (no bloqueado)</Badge>
  }
  const estilo = ESTILO_ORIGEN[dia.origen]
  return (
    <Badge variant="outline" className={estilo.badge}>
      {estilo.etiqueta}
    </Badge>
  )
}

export function VistaListaExcepciones({
  version,
  onCambio,
  onSeleccionarFecha,
}: {
  version: number
  onCambio: () => void
  onSeleccionarFecha: (fecha: string) => void
}) {
  const anioActual = hoy().anio
  const [anio, setAnio] = useState(anioActual)
  const [mes, setMes] = useState<number | "todo">("todo")
  const [filtro, setFiltro] = useState<FiltroMostrar>("todas")
  const [quitando, setQuitando] = useState<string | null>(null)

  const desde = `${anio}-01-01`
  const hasta = `${anio}-12-31`
  const { lista, reglas, cargando, error } = useExcepciones(desde, hasta, version)

  const filtrados = useMemo(
    () =>
      lista.filter((dia) => {
        if (mes !== "todo" && Number(dia.fecha.slice(5, 7)) !== mes) return false
        return cumpleFiltro(dia, filtro)
      }),
    [lista, mes, filtro]
  )

  async function quitarExcepcion(fecha: string) {
    setQuitando(fecha)
    try {
      await eliminarExcepcion(fecha)
      toast.success("Excepción quitada: el día vuelve a seguir las reglas.")
      onCambio()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setQuitando(null)
    }
  }

  return (
    <div className="@container space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={String(anio)} onValueChange={(v) => setAnio(Number(v))}>
          <SelectTrigger size="sm" className="w-28">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {[anioActual - 1, anioActual, anioActual + 1, anioActual + 2].map((a) => (
              <SelectItem key={a} value={String(a)}>
                {a}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={String(mes)} onValueChange={(v) => setMes(v === "todo" ? "todo" : Number(v))}>
          <SelectTrigger size="sm" className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="todo">Todo el año</SelectItem>
            {MESES.map((nombre, i) => (
              <SelectItem key={i} value={String(i + 1)}>
                {nombre}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={filtro} onValueChange={(v) => setFiltro(v as FiltroMostrar)}>
          <SelectTrigger size="sm" className="w-48">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {FILTROS.map(({ valor, etiqueta }) => (
              <SelectItem key={valor} value={valor}>
                {etiqueta}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <span className="ml-auto text-xs text-muted-foreground">
          {filtrados.length} fecha{filtrados.length === 1 ? "" : "s"}
        </span>
      </div>

      {error && <p className="text-sm text-destructive-text">No se pudo cargar: {error}</p>}

      {cargando && !lista.length ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : filtrados.length === 0 ? (
        <p className="rounded-md border p-4 text-sm text-muted-foreground">
          No hay fechas que coincidan con el filtro.
        </p>
      ) : (
        <>
          {/* Pantallas angostas: tarjetas. Desde @2xl: tabla (ver "Tablas
              responsivas" en docs/ARQUITECTURA.md). */}
          <ul className="divide-y rounded-md border @2xl:hidden">
            {filtrados.map((dia) => (
              <li key={dia.fecha}>
                <button
                  type="button"
                  onClick={() => onSeleccionarFecha(dia.fecha)}
                  className="w-full space-y-1 px-3 py-2.5 text-left text-sm outline-none transition-colors hover:bg-muted/50"
                >
                  <span className="flex items-start justify-between gap-2">
                    <span>{fechaCivilLarga(dia.fecha)}</span>
                    <Badge variant={dia.bloqueada ? "destructive" : "success"}>
                      {dia.bloqueada ? "Bloqueada" : "Habilitada"}
                    </Badge>
                  </span>
                  <span className="block">
                    <OrigenBadge dia={dia} />
                  </span>
                  <span className="block text-xs text-muted-foreground">
                    {detalleDia(dia, reglas)}
                  </span>
                </button>
              </li>
            ))}
          </ul>

          <div className="hidden rounded-md border @2xl:block">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="whitespace-nowrap">Fecha</TableHead>
                  <TableHead>Estado</TableHead>
                  <TableHead>Origen</TableHead>
                  <TableHead className="w-full">Detalle / motivo</TableHead>
                  <TableHead>Acciones</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtrados.map((dia) => (
                  <TableRow key={dia.fecha} className="@6xl:text-base @6xl:py-3">
                    <TableCell className="whitespace-nowrap">{fechaCivilLarga(dia.fecha)}</TableCell>
                    <TableCell>
                      <Badge variant={dia.bloqueada ? "destructive" : "success"}>
                        {dia.bloqueada ? "Bloqueada" : "Habilitada"}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <OrigenBadge dia={dia} />
                    </TableCell>
                    <TableCell className="whitespace-normal">{detalleDia(dia, reglas)}</TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1">
                        <Button
                          size="xs"
                          variant="outline"
                          onClick={() => onSeleccionarFecha(dia.fecha)}
                        >
                          <Pencil />
                          Editar
                        </Button>
                        {dia.excepcion && (
                          <Button
                            size="xs"
                            variant="outline"
                            className="text-destructive-text hover:text-destructive-text"
                            disabled={quitando === dia.fecha}
                            onClick={() => quitarExcepcion(dia.fecha)}
                          >
                            <X />
                            Quitar excepción
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </>
      )}
    </div>
  )
}
