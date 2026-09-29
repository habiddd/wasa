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
import { fetchActivos, fetchMetricasActivo, type FiltroMetricas, type Recurso } from "@/lib/api"
import { formatearFechaHoraLACS } from "@/lib/fechas"
import {
  BarrasH,
  BarrasV,
  Cifra,
  DIAS_SEMANA,
  ETIQUETA_ESTADO,
  ORDEN_SEMANA,
  Puntualidad,
  filtroValido,
  respuestaDe,
  ReservasPorDiaChart,
  Seccion,
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

function SelectorActivo({ activoId, onActivo }: { activoId: number | null; onActivo: (id: number) => void }) {
  const { data: activos } = usePolling(fetchActivos, 300000)
  const grupos: [string, Recurso[]][] = [
    ["Equipos", (activos ?? []).filter((a) => a.tipo === "equipo")],
    ["Espacios", (activos ?? []).filter((a) => a.tipo === "espacio")],
  ]
  return (
    <Select value={activoId == null ? "" : String(activoId)} onValueChange={(v) => onActivo(Number(v))}>
      <SelectTrigger className="w-full max-w-md" aria-label="Activo">
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
      <SelectorActivo activoId={activoId} onActivo={onActivo} />
      {activoId == null && <Vacio>Elige un activo para ver sus métricas.</Vacio>}
      {activoId != null && (
        <Tabs value={vista} onValueChange={(v) => onVista(v as VistaActivo)}>
          <TabsList>
            <TabsTrigger value="metricas">Métricas</TabsTrigger>
            <TabsTrigger value="timeline">Timeline</TabsTrigger>
          </TabsList>
        </Tabs>
      )}
      {activoId != null && vista === "timeline" && <TimelineActivo activoId={activoId} filtro={filtro} />}
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
            <dl className="grid gap-x-6 gap-y-1 text-sm @lg:grid-cols-2">
              {[
                ["Tipo", m.ficha.tipo === "equipo" ? "Equipo" : "Espacio"],
                ["Subcategoría", m.ficha.subcategoria ?? "-"],
                ["Placa", m.ficha.placa ?? "-"],
                ["Dado de alta", formatearFechaHoraLACS(m.ficha.creado_en)],
                ["Pertenece a", m.ficha.padre ?? "-"],
                ["Ubicado en", m.ficha.espacio ?? "-"],
              ].map(([k, v]) => (
                <div key={k} className="flex justify-between gap-2">
                  <dt className="text-muted-foreground">{k}</dt>
                  <dd>{v}</dd>
                </div>
              ))}
            </dl>
          </Seccion>

          <div className="flex flex-wrap gap-3">
            <Cifra valor={m.conteos.reservas} etiqueta="Reservas" />
            <Cifra valor={m.conteos.prestamos} etiqueta="Préstamos" />
            <Cifra valor={m.conteos.denegadas} etiqueta="Denegadas" />
            <Cifra valor={m.conteos.canceladas} etiqueta="Canceladas" />
            <Cifra valor={m.conteos.no_show} etiqueta="No show" />
            <Cifra valor={m.conteos.devueltas_tarde} etiqueta="Devueltas tarde" />
            <Cifra valor={m.conteos.no_devueltas} etiqueta="No devueltas" />
            <Cifra valor={m.seguro.reservas_que_salen} etiqueta="Reservas que salen de Barranquilla" />
          </div>

          <div className="grid gap-4 @4xl:grid-cols-2">
            <Seccion titulo="Reservas por estado">
              <BarrasH
                titulo="Reservas por estado"
                datos={m.estados.map((e) => ({ etiqueta: legible(e.estado, ETIQUETA_ESTADO), total: e.total }))}
              />
            </Seccion>
            <Seccion titulo="Uso">
              <dl className="space-y-1 text-sm">
                {[
                  ["Horas prestadas", duracion(m.uso.prestado_seg)],
                  ["Duración promedio", duracion(m.uso.duracion_seg)],
                  ["Anticipación promedio", duracion(m.uso.anticipacion_seg)],
                  ["Usuarios distintos", m.uso.usuarios],
                ].map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-2">
                    <dt className="text-muted-foreground">{k}</dt>
                    <dd className="tabular-nums">{v}</dd>
                  </div>
                ))}
              </dl>
            </Seccion>
          </div>

          <div className="grid gap-4 @4xl:grid-cols-2">
            <Seccion titulo="Última y próxima reserva">
              {[
                ["Última", m.ultima_reserva],
                ["Próxima", m.proxima_reserva],
              ].map(([k, r]) => {
                const reserva = r as typeof m.ultima_reserva
                return (
                  <div key={k as string} className="text-sm">
                    <span className="text-muted-foreground">{k as string}: </span>
                    {reserva
                      ? `${formatearFechaHoraLACS(reserva.inicio)} · ${reserva.nombre ?? telefono(reserva.telefono_wa)} · ${legible(reserva.estado, ETIQUETA_ESTADO)}`
                      : "ninguna"}
                  </div>
                )
              })}
            </Seccion>
            <Seccion titulo="Puntualidad">
              <Puntualidad datos={m.puntualidad} />
            </Seccion>
          </div>

          <Seccion titulo="Top usuarios de este activo">
            <TopTabla
              filas={m.usuarios}
              nombre={(u) => (u.nombre ? `${u.nombre} · ${telefono(u.telefono_wa)}` : telefono(u.telefono_wa))}
              columnas={[
                { titulo: "Reservas", valor: (u) => u.reservas },
                { titulo: "Préstamos", valor: (u) => u.prestamos },
                { titulo: "Devueltas tarde", valor: (u) => u.devueltas_tarde },
              ]}
            />
          </Seccion>

          <Seccion titulo="Cuándo se reserva">
            <div className="text-xs text-muted-foreground">Por día</div>
            <ReservasPorDiaChart datos={m.reservas_por_dia} filtro={filtro} />
            <div className="grid gap-4 @3xl:grid-cols-2">
              <div className="space-y-1">
                <div className="text-xs text-muted-foreground">Por día de la semana</div>
                <BarrasV
                  titulo="Reservas por día de la semana"
                  datos={ORDEN_SEMANA.map((k) => ({ etiqueta: DIAS_SEMANA[k], total: m.por_dia_semana[k] }))}
                />
              </div>
              <div className="space-y-1">
                <div className="text-xs text-muted-foreground">Por hora de inicio</div>
                <BarrasV
                  titulo="Reservas por hora de inicio"
                  datos={m.por_hora.map((total, h) => ({ etiqueta: String(h), total }))}
                />
              </div>
            </div>
          </Seccion>

          <Seccion titulo="Historial de inventario">
            <div className="text-xs text-muted-foreground">
              Todo el tiempo · {m.inventario.veces_mantenimiento} vez/veces en mantenimiento ·{" "}
              {m.inventario.veces_no_devuelto} vez/veces no devuelto
            </div>
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
