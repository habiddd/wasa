import { useEffect } from "react"

import { Badge } from "@/components/ui/badge"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { usePolling } from "@/hooks/use-polling"
import { ESTADOS_ACTIVO } from "@/features/activos/tree-utils"
import { EstadoReservaBadge } from "@/features/calendario/EstadoBadge"
import {
  fetchActivos,
  fetchMetricasActivo,
  type EstadoReserva,
  type FiltroMetricas,
  type MetricasActivo as DatosActivo,
  type Recurso,
} from "@/lib/api"
import { formatearFechaHoraLACS } from "@/lib/fechas"
import {
  BarrasH,
  Cifra,
  Cifras,
  CuandoSeReserva,
  Datos,
  ETIQUETA_ESTADO,
  Persona,
  Puntualidad,
  filtroValido,
  respuestaDe,
  Seccion,
  Subtitulo,
  TopTabla,
  Vacio,
  duracion,
  legible,
  telefono,
} from "./MetricasPanel"
import { TimelineActivo } from "./TimelineActivo"

export type VistaActivo = "metricas" | "timeline"

const ETIQUETA_MOTIVO: Record<string, string> = {
  devuelto_tarde: "Devuelto tarde",
  encontrado: "Encontrado",
  otro: "Otro",
}

const textoEstado = (e: string | null) => (e ? (ESTADOS_ACTIVO[e as keyof typeof ESTADOS_ACTIVO]?.texto ?? legible(e, {})) : "Alta")

const veces = (n: number) => `${n} ${n === 1 ? "vez" : "veces"}`

function SelectorActivo({ activoId, onActivo }: { activoId: number | null; onActivo: (id: number) => void }) {
  const { data: activos } = usePolling(fetchActivos, 300000)
  const grupos: [string, Recurso[]][] = [
    ["Equipos", (activos ?? []).filter((a) => a.tipo === "equipo")],
    ["Espacios", (activos ?? []).filter((a) => a.tipo === "espacio")],
  ]
  return (
    <Select value={activoId == null ? "" : String(activoId)} onValueChange={(v) => onActivo(Number(v))}>
      <SelectTrigger className="w-full @xl:max-w-md" aria-label="Activo">
        <SelectValue placeholder="Elige un activo" />
      </SelectTrigger>
      <SelectContent>
        {grupos.map(([titulo, lista]) =>
          lista.length ? (
            <SelectGroup key={titulo}>
              <SelectLabel>{titulo}</SelectLabel>
              {lista.map((a) => (
                <SelectItem key={a.id} value={String(a.id)}>
                  {a.nombre}
                  {a.placa ? ` · ${a.placa}` : ""}
                </SelectItem>
              ))}
            </SelectGroup>
          ) : null,
        )}
      </SelectContent>
    </Select>
  )
}

function ReservaCercana({ reserva }: { reserva: DatosActivo["ultima_reserva"] }) {
  if (!reserva) return <span className="text-muted-foreground">Ninguna</span>
  return (
    <span className="flex flex-wrap items-center justify-end gap-x-2 gap-y-1">
      <span>{formatearFechaHoraLACS(reserva.inicio)}</span>
      <span className="text-muted-foreground">{reserva.nombre ?? telefono(reserva.telefono_wa)}</span>
      <EstadoReservaBadge estado={reserva.estado as EstadoReserva} />
    </span>
  )
}

export function MetricasActivo({
  activoId,
  onActivo,
  vista,
  onVista,
  filtro,
}: {
  activoId: number | null
  onActivo: (id: number) => void
  vista: VistaActivo
  onVista: (v: VistaActivo) => void
  filtro: FiltroMetricas
}) {
  const { data, error, reload } = usePolling(
    () => (activoId == null || vista !== "metricas" || !filtroValido(filtro) ? Promise.resolve(null) : fetchMetricasActivo(activoId, filtro)),
    60000,
  )

  // usePolling solo recarga por intervalo: al cambiar de activo o filtro se pide de nuevo.
  useEffect(() => {
    reload()
  }, [activoId, vista, filtro.periodo, filtro.desde, filtro.hasta, reload])

  // Una respuesta que aún es de otro activo o pedido no se pinta como si fuera la nueva.
  const m = vista === "metricas" && data && data.ficha.id === activoId && filtroValido(filtro) && respuestaDe(data, filtro) ? data : null

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-2 @xl:flex-row @xl:items-center @xl:justify-between">
        <SelectorActivo activoId={activoId} onActivo={onActivo} />
        {activoId != null && (
          <Tabs value={vista} onValueChange={(v) => onVista(v as VistaActivo)}>
            <TabsList aria-label="Vista del activo">
              <TabsTrigger value="metricas">Métricas</TabsTrigger>
              <TabsTrigger value="timeline">Timeline</TabsTrigger>
            </TabsList>
          </Tabs>
        )}
      </div>
      {activoId == null && <Vacio>Elige un activo para ver sus métricas y su timeline.</Vacio>}
      {activoId != null && vista === "timeline" && <TimelineActivo activoId={activoId} />}
      {activoId != null && vista === "metricas" && error && !data && (
        <p className="text-sm text-destructive-text">No se pudieron cargar las métricas del activo.</p>
      )}
      {m && (
        <>
          <Seccion titulo="Ficha">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-base font-medium">{m.ficha.nombre}</span>
              <Badge variant={ESTADOS_ACTIVO[m.ficha.estado].variant}>{ESTADOS_ACTIVO[m.ficha.estado].texto}</Badge>
            </div>
            <dl className="grid gap-x-8 gap-y-1.5 text-sm @lg:grid-cols-2">
              {[
                ["Tipo", m.ficha.tipo === "equipo" ? "Equipo" : "Espacio"],
                ["Subcategoría", m.ficha.subcategoria ?? "-"],
                ["Placa", m.ficha.placa ?? "-"],
                ["Dado de alta", formatearFechaHoraLACS(m.ficha.creado_en)],
                ["Pertenece a", m.ficha.padre ?? "-"],
                ["Ubicado en", m.ficha.espacio ?? "-"],
              ].map(([k, v]) => (
                <div key={k} className="flex justify-between gap-4">
                  <dt className="text-muted-foreground">{k}</dt>
                  <dd className="text-right">{v}</dd>
                </div>
              ))}
            </dl>
          </Seccion>

          <Cifras className="@2xl:grid-cols-4">
            <Cifra valor={m.conteos.reservas} etiqueta="Reservas" />
            <Cifra valor={m.conteos.prestamos} etiqueta="Préstamos" />
            <Cifra valor={m.conteos.denegadas} etiqueta="Denegadas" />
            <Cifra valor={m.conteos.canceladas} etiqueta="Canceladas" />
            <Cifra valor={m.conteos.no_show} etiqueta="No show" />
            <Cifra valor={m.conteos.devueltas_tarde} etiqueta="Devueltas tarde" />
            <Cifra valor={m.conteos.no_devueltas} etiqueta="No devueltas" />
            <Cifra valor={m.seguro.reservas_que_salen} etiqueta="Salen de Barranquilla" />
          </Cifras>

          <div className="grid gap-4 @4xl:grid-cols-2">
            <Seccion titulo="Reservas por estado">
              <BarrasH
                titulo="Reservas por estado"
                datos={m.estados.map((e) => ({ etiqueta: legible(e.estado, ETIQUETA_ESTADO), total: e.total }))}
              />
            </Seccion>
            <Seccion titulo="Uso">
              <Datos
                filas={[
                  ["Horas prestadas", duracion(m.uso.prestado_seg)],
                  ["Duración promedio", duracion(m.uso.duracion_seg)],
                  ["Anticipación promedio", duracion(m.uso.anticipacion_seg)],
                  ["Usuarios distintos", m.uso.usuarios],
                ]}
              />
              <Subtitulo>Reservas cercanas</Subtitulo>
              <Datos
                filas={[
                  ["Última", <ReservaCercana key="ultima" reserva={m.ultima_reserva} />],
                  ["Próxima", <ReservaCercana key="proxima" reserva={m.proxima_reserva} />],
                ]}
              />
            </Seccion>
          </div>

          <div className="grid gap-4 @4xl:grid-cols-2">
            <Seccion titulo="Puntualidad">
              <Puntualidad datos={m.puntualidad} />
            </Seccion>
            <Seccion titulo="Top usuarios de este activo">
              <TopTabla
                filas={m.usuarios}
                nombre={(u) => <Persona nombre={u.nombre} telefonoWa={u.telefono_wa} />}
                columnas={[
                  { titulo: "Reservas", valor: (u) => u.reservas },
                  { titulo: "Préstamos", valor: (u) => u.prestamos },
                  { titulo: "Devueltas tarde", valor: (u) => u.devueltas_tarde },
                ]}
              />
            </Seccion>
          </div>

          <Seccion titulo="Cuándo se reserva">
            <CuandoSeReserva m={m} filtro={filtro} />
          </Seccion>

          <Seccion titulo="Historial de inventario">
            <p className="text-xs text-muted-foreground">
              Todo el tiempo · {veces(m.inventario.veces_mantenimiento)} en mantenimiento ·{" "}
              {veces(m.inventario.veces_no_devuelto)} no devuelto
            </p>
            {m.inventario.historial.length === 0 ? (
              <Vacio>Este activo no ha cambiado de estado.</Vacio>
            ) : (
              <ul className="divide-y rounded-md border">
                {m.inventario.historial.map((h, idx) => (
                  <li key={idx} className="space-y-0.5 px-3 py-2 text-sm">
                    <span className="block font-medium">
                      {textoEstado(h.estado_anterior)} → {textoEstado(h.estado_nuevo)}
                    </span>
                    <span className="block text-xs text-muted-foreground">
                      {formatearFechaHoraLACS(h.cambiado_en)}
                      {h.motivo ? ` · ${legible(h.motivo, ETIQUETA_MOTIVO)}` : ""}
                      {h.nota ? ` · ${h.nota}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Seccion>
        </>
      )}
    </div>
  )
}
