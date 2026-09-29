import { useEffect, useState, type ReactNode } from "react"

import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { usePolling } from "@/hooks/use-polling"
import {
  fetchMetricas,
  type EstadoRecurso,
  type FiltroMetricas,
  type Metricas,
  type Periodo,
} from "@/lib/api"
import { claveDe, hoy, sumarDias } from "@/features/calendario/calendario-utils"
import { ESTADOS_ACTIVO } from "@/features/activos/tree-utils"
import { MetricasActivo, type VistaActivo } from "./MetricasActivo"

// Claves que el bot guarda en auditoria_bot.intent_detectado. Las *_cpa son
// históricas (el laboratorio se llamaba CPA): se muestran igual que las *_lacs.
const ETIQUETA_INTENT: Record<string, string> = {
  reservar: "Reservar",
  registro: "Registro",
  consultar_disponibilidad: "Consulta de disponibilidad",
  cancelar: "Cancelar reserva",
  confirmar_reserva: "Confirmar reserva",
  menu_fallback: "No entendido (menú)",
  saludo: "Saludo",
  ayuda: "Ayuda",
  otro: "Otro",
  reserva_dashboard: "Reserva creada en el panel",
  confirmacion_lacs: "Confirmación de la LACS",
  confirmacion_cpa: "Confirmación de la LACS",
  denegacion_lacs: "Denegación de la LACS",
  denegacion_cpa: "Denegación de la LACS",
  aviso_previo: "Aviso previo",
  aviso_hora: "Aviso de hora",
  aviso_no_show: "Aviso de no presentación",
  aviso_devolucion: "Aviso de devolución",
  oferta_reserva: "Oferta de reserva",
}

export const ETIQUETA_ESTADO: Record<string, string> = {
  pendiente: "Pendiente",
  confirmada: "Confirmada",
  denegada: "Denegada",
  cancelada: "Cancelada",
  entregada: "Entregada",
  completada: "Completada",
  devuelta_tarde: "Devuelta tarde",
  no_show: "No se presentó",
  no_devuelta: "No devuelta",
}

const ETIQUETA_AVISO: Record<string, string> = {
  previo: "Previo",
  hora: "De la hora",
  no_show: "No presentación",
  devolucion: "Devolución",
}

const ETIQUETA_CANAL: Record<string, string> = { whatsapp: "WhatsApp", dashboard: "Panel" }

// Domingo primero, como lo devuelve Postgres (EXTRACT(DOW)); se muestra desde el lunes.
export const DIAS_SEMANA = ["Dom", "Lun", "Mar", "Mié", "Jue", "Vie", "Sáb"]
export const ORDEN_SEMANA = [1, 2, 3, 4, 5, 6, 0]

const ETIQUETA_PERIODO: Record<Exclude<Periodo, "rango">, string> = {
  "30": "los últimos 30 días",
  "90": "los últimos 90 días",
  todo: "todo el tiempo",
}

// Las pruebas E2E del bot escriben con este número (no llega a WhatsApp real):
// se marcan para no confundirlas con estudiantes, sin ocultarlas.
const TELEFONO_PRUEBA = "000000000000"

export function telefono(valor: string | null) {
  if (!valor) return "-"
  return valor === TELEFONO_PRUEBA ? "Prueba del bot" : valor
}

// Una clave nueva que todavía no esté en el mapa se ve legible igual.
export function legible(clave: string | null, mapa: Record<string, string>) {
  if (!clave) return "-"
  if (mapa[clave]) return mapa[clave]
  const texto = clave.replaceAll("_", " ")
  return texto.charAt(0).toUpperCase() + texto.slice(1)
}

/** 5400 -> "1 h 30 min"; 200000 -> "2 d 7 h". */
export function duracion(seg: number | null | undefined) {
  if (seg == null) return "—"
  const min = Math.round(seg / 60)
  if (min < 60) return `${min} min`
  const h = Math.floor(min / 60)
  if (h < 24) return min % 60 ? `${h} h ${min % 60} min` : `${h} h`
  return h % 24 ? `${Math.floor(h / 24)} d ${h % 24} h` : `${Math.floor(h / 24)} d`
}

function porcentaje(parte: number, total: number) {
  return total ? `${Math.round((parte / total) * 100)} %` : "—"
}

// "dia" viene del backend como fecha sin hora -- se formatea en UTC para no
// correr el día al convertir a la zona de la LACS.
const ETIQUETA_DIA = new Intl.DateTimeFormat("es-CO", {
  timeZone: "UTC",
  day: "numeric",
  month: "short",
})

function etiquetaDia(dia: string) {
  return ETIQUETA_DIA.format(new Date(`${dia}T00:00:00Z`))
}

/** "los últimos 30 días", o "el periodo del 1 sep. al 15 oct." para el rango personalizado. */
function etiquetaFiltro(f: FiltroMetricas) {
  if (f.periodo !== "rango") return ETIQUETA_PERIODO[f.periodo]
  return f.desde && f.hasta ? `el periodo del ${etiquetaDia(f.desde)} al ${etiquetaDia(f.hasta)}` : "el rango elegido"
}

/** La respuesta corresponde a este pedido (no a uno anterior que llegó tarde). */
export function respuestaDe(
  d: { periodo: Periodo; desde: string | null; hasta: string | null },
  f: FiltroMetricas,
) {
  return (
    d.periodo === f.periodo &&
    (f.periodo !== "rango" || (d.desde === f.desde && d.hasta === f.hasta))
  )
}

export function Vacio({ children = "Sin datos en este periodo." }: { children?: ReactNode }) {
  return <p className="text-xs text-muted-foreground">{children}</p>
}

export function Seccion({ titulo, children, className }: { titulo: string; children: ReactNode; className?: string }) {
  return (
    <Card className={className}>
      <CardContent className="space-y-2 py-2">
        <h2 className="text-sm font-semibold">{titulo}</h2>
        {children}
      </CardContent>
    </Card>
  )
}

/**
 * Barras finas ancladas a la línea base, con tooltip al pasar el mouse. La
 * tabla oculta (sr-only) le da a lectores de pantalla el mismo detalle que
 * el tooltip da a la vista.
 */
export function ReservasPorDiaChart({
  datos,
  filtro,
}: {
  datos: { dia: string; total: number }[]
  filtro: FiltroMetricas
}) {
  const [hover, setHover] = useState<number | null>(null)
  const max = Math.max(1, ...datos.map((d) => d.total))

  if (datos.length === 0) {
    return <Vacio>Sin reservas en {etiquetaFiltro(filtro)}.</Vacio>
  }

  return (
    <div className="relative">
      <div className="flex h-16 items-end gap-[2px] @6xl:h-24">
        {datos.map((d, idx) => (
          <div
            key={d.dia}
            className="group relative flex h-full flex-1 items-end"
            onMouseEnter={() => setHover(idx)}
            onMouseLeave={() => setHover((h) => (h === idx ? null : h))}
          >
            <div
              className="w-full rounded-t-sm bg-primary/60 transition-colors group-hover:bg-primary"
              style={{ height: `${Math.max((d.total / max) * 100, d.total > 0 ? 6 : 2)}%` }}
            />
            {hover === idx && (
              <div className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-1 -translate-x-1/2 whitespace-nowrap rounded-md bg-foreground px-2 py-1 text-xs text-background">
                {etiquetaDia(d.dia)} · {d.total} reserva{d.total === 1 ? "" : "s"}
              </div>
            )}
          </div>
        ))}
      </div>
      {/* Eje mínimo: primer día, máximo y último día. Sin esto las barras no
          dicen de cuándo son ni cuánto miden sin pasar el mouse. */}
      <div className="mt-1 flex justify-between text-xs text-muted-foreground tabular-nums" aria-hidden>
        <span>{etiquetaDia(datos[0].dia)}</span>
        <span>máx. {max} por día</span>
        <span>{etiquetaDia(datos[datos.length - 1].dia)}</span>
      </div>
      <table className="sr-only">
        <caption>Reservas por día en {etiquetaFiltro(filtro)}</caption>
        <tbody>
          {datos.map((d) => (
            <tr key={d.dia}>
              <td>{etiquetaDia(d.dia)}</td>
              <td>{d.total}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export type Barra = { etiqueta: string; total: number }

/** Barras horizontales con el conteo al lado; tabla sr-only para lectores de pantalla. */
export function BarrasH({ datos, titulo }: { datos: Barra[]; titulo: string }) {
  const max = Math.max(1, ...datos.map((d) => d.total))
  if (datos.every((d) => d.total === 0)) return <Vacio />
  return (
    <>
      <ul className="space-y-1.5" aria-hidden>
        {datos.map((d) => (
          <li key={d.etiqueta} className="grid grid-cols-[minmax(6rem,9rem)_1fr_2.5rem] items-center gap-2 text-xs">
            <span className="truncate">{d.etiqueta}</span>
            <span className="h-2 rounded-sm bg-muted">
              <span
                className="block h-full rounded-sm bg-primary/60"
                style={{ width: `${(d.total / max) * 100}%` }}
              />
            </span>
            <span className="text-right tabular-nums">{d.total}</span>
          </li>
        ))}
      </ul>
      <table className="sr-only">
        <caption>{titulo}</caption>
        <tbody>
          {datos.map((d) => (
            <tr key={d.etiqueta}>
              <td>{d.etiqueta}</td>
              <td>{d.total}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

/** Barras verticales (día de la semana, hora): misma idea que ReservasPorDiaChart, con etiqueta bajo cada barra. */
export function BarrasV({ datos, titulo }: { datos: Barra[]; titulo: string }) {
  const max = Math.max(1, ...datos.map((d) => d.total))
  if (datos.every((d) => d.total === 0)) return <Vacio />
  return (
    <>
      <div className="flex h-24 items-end gap-[2px]" aria-hidden>
        {datos.map((d) => (
          <div key={d.etiqueta} className="flex h-full min-w-0 flex-1 flex-col justify-end" title={`${d.etiqueta}: ${d.total}`}>
            <div
              className="w-full rounded-t-sm bg-primary/60"
              style={{ height: `${Math.max((d.total / max) * 100, d.total > 0 ? 6 : 2)}%` }}
            />
          </div>
        ))}
      </div>
      <div className="flex gap-[2px] text-[11px] text-muted-foreground tabular-nums" aria-hidden>
        {datos.map((d) => (
          <span key={d.etiqueta} className="min-w-0 flex-1 truncate text-center">
            {d.etiqueta}
          </span>
        ))}
      </div>
      <table className="sr-only">
        <caption>{titulo}</caption>
        <tbody>
          {datos.map((d) => (
            <tr key={d.etiqueta}>
              <td>{d.etiqueta}</td>
              <td>{d.total}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

export function Cifra({ valor, etiqueta }: { valor: ReactNode; etiqueta: string }) {
  return (
    <Card className="min-w-[130px] flex-1">
      <CardContent className="py-2">
        <div className="text-2xl font-bold tabular-nums">{valor}</div>
        <div className="text-xs text-muted-foreground">{etiqueta}</div>
      </CardContent>
    </Card>
  )
}

type Columna<T> = { titulo: string; valor: (fila: T) => ReactNode }

/**
 * Top 10: tabla desde ~670 px (@2xl) y lista de tarjetas en pantallas
 * angostas. La primera columna es el nombre; el resto son cifras.
 */
export function TopTabla<T>({
  filas,
  nombre,
  columnas,
}: {
  filas: T[]
  nombre: (fila: T) => string
  columnas: Columna<T>[]
}) {
  if (filas.length === 0) return <Vacio />
  return (
    <>
      <ul className="divide-y rounded-md border @2xl:hidden">
        {filas.map((f, idx) => (
          <li key={idx} className="space-y-0.5 px-3 py-2 text-sm">
            <span className="block font-medium">{nombre(f)}</span>
            <span className="block text-xs text-muted-foreground">
              {columnas.map((c) => `${c.titulo}: ${c.valor(f)}`).join(" · ")}
            </span>
          </li>
        ))}
      </ul>
      <div className="hidden rounded-md border @2xl:block">
        <Table className="@6xl:text-base">
          <TableHeader>
            <TableRow>
              <TableHead className="@6xl:py-3">Nombre</TableHead>
              {columnas.map((c) => (
                <TableHead key={c.titulo} className="text-right @6xl:py-3">
                  {c.titulo}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {filas.map((f, idx) => (
              <TableRow key={idx}>
                <TableCell className="whitespace-normal @6xl:py-3">{nombre(f)}</TableCell>
                {columnas.map((c) => (
                  <TableCell key={c.titulo} className="text-right tabular-nums @6xl:py-3">
                    {c.valor(f)}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </>
  )
}

const COLUMNAS_ACTIVO: Columna<Metricas["activos"]["equipo"][number]>[] = [
  { titulo: "Reservas", valor: (a) => a.reservas },
  { titulo: "Préstamos", valor: (a) => a.prestamos },
  { titulo: "Denegadas", valor: (a) => a.denegadas },
  { titulo: "Canceladas", valor: (a) => a.canceladas },
  { titulo: "No show", valor: (a) => a.no_show },
]

const COLUMNAS_USUARIO: Columna<Metricas["usuarios"][number]>[] = [
  { titulo: "Reservas", valor: (u) => u.reservas },
  { titulo: "Préstamos", valor: (u) => u.prestamos },
  { titulo: "Devueltas tarde", valor: (u) => u.devueltas_tarde },
  { titulo: "No show", valor: (u) => u.no_show },
  { titulo: "No devueltas", valor: (u) => u.no_devueltas },
  { titulo: "Retraso prom.", valor: (u) => duracion(u.retraso_devolucion_seg) },
]

export function Puntualidad({ datos }: { datos: Metricas["puntualidad"] }) {
  const filas = [
    { nombre: "Entregas", d: datos.entrega },
    { nombre: "Devoluciones", d: datos.devolucion },
  ]
  if (!datos.entrega && !datos.devolucion) return <Vacio />
  return (
    <div className="grid gap-3 @lg:grid-cols-2">
      {filas.map(({ nombre, d }) => (
        <div key={nombre}>
          <div className="text-xs text-muted-foreground">{nombre} a tiempo</div>
          <div className="text-2xl font-bold tabular-nums">{d ? porcentaje(d.a_tiempo, d.total) : "—"}</div>
          <div className="text-xs text-muted-foreground">
            {d
              ? `${d.a_tiempo} de ${d.total} · retraso prom. de las tardías: ${duracion(d.retraso_seg)}`
              : "Sin registros"}
          </div>
        </div>
      ))}
    </div>
  )
}

function PanelActivos({ activos }: { activos: Metricas["activos"] }) {
  const [tipo, setTipo] = useState<"equipo" | "espacio">("equipo")
  return (
    <Seccion titulo="Top 10 activos">
      <Tabs value={tipo} onValueChange={(v) => setTipo(v as "equipo" | "espacio")}>
        <TabsList>
          <TabsTrigger value="equipo">Equipos</TabsTrigger>
          <TabsTrigger value="espacio">Espacios</TabsTrigger>
        </TabsList>
      </Tabs>
      <TopTabla filas={activos[tipo]} nombre={(a) => a.nombre} columnas={COLUMNAS_ACTIVO} />
    </Seccion>
  )
}

/** Un rango necesita las dos fechas y hasta >= desde (YYYY-MM-DD se compara como texto). */
export function filtroValido(f: FiltroMetricas) {
  return f.periodo !== "rango" || Boolean(f.desde && f.hasta && f.hasta >= f.desde)
}

export function MetricasPanel() {
  const [periodo, setPeriodo] = useState<Periodo>("30")
  // Al elegir "Personalizado" arrancan con los últimos 30 días (fechas civiles de la LACS).
  const [desde, setDesde] = useState(() => claveDe(sumarDias(hoy(), -30)))
  const [hasta, setHasta] = useState(() => claveDe(hoy()))
  const [vista, setVista] = useState<"general" | "activo">("general")
  // Vive aquí para no perderse al cambiar de pestaña o de periodo.
  const [activoId, setActivoId] = useState<number | null>(null)
  const [vistaActivo, setVistaActivo] = useState<VistaActivo>("metricas")
  const filtro: FiltroMetricas = { periodo, desde, hasta }
  const valido = filtroValido(filtro)
  const { data, error, reload } = usePolling(() => (valido ? fetchMetricas(filtro) : Promise.resolve(null)), 60000)

  // usePolling solo recarga por intervalo: al cambiar de filtro se pide de nuevo.
  useEffect(() => {
    reload()
  }, [periodo, desde, hasta, reload])

  if (error && !data) {
    return <p className="text-sm text-destructive-text">No se pudieron cargar las métricas.</p>
  }

  // Una respuesta que aún es de otro pedido no se pinta como si fuera del nuevo.
  const m = data && valido && respuestaDe(data, filtro) ? data : null
  const r = m?.resumen

  return (
    <div className="@container space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold tracking-tight">Métricas</h1>
        <div className="flex flex-wrap gap-2">
          <Tabs value={vista} onValueChange={(v) => setVista(v as "general" | "activo")}>
            <TabsList>
              <TabsTrigger value="general">General</TabsTrigger>
              <TabsTrigger value="activo">Por activo</TabsTrigger>
            </TabsList>
          </Tabs>
          <Tabs value={periodo} onValueChange={(v) => setPeriodo(v as Periodo)}>
            <TabsList>
              <TabsTrigger value="30">30 días</TabsTrigger>
              <TabsTrigger value="90">90 días</TabsTrigger>
              <TabsTrigger value="todo">Todo</TabsTrigger>
              <TabsTrigger value="rango">Personalizado</TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
      </div>

      {periodo === "rango" && (
        <div className="flex flex-col gap-2 @sm:flex-row @sm:flex-wrap @sm:items-end">
          <div className="space-y-1">
            <Label htmlFor="metricas-desde">Desde</Label>
            <Input
              id="metricas-desde"
              type="date"
              value={desde}
              max={hasta || undefined}
              onChange={(e) => setDesde(e.target.value)}
              className="@sm:w-44"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="metricas-hasta">Hasta</Label>
            <Input
              id="metricas-hasta"
              type="date"
              value={hasta}
              min={desde || undefined}
              onChange={(e) => setHasta(e.target.value)}
              className="@sm:w-44"
            />
          </div>
          {!valido && (
            <p role="alert" className="text-xs text-destructive-text">
              {desde && hasta ? "«Hasta» no puede ser anterior a «Desde»." : "Elige las dos fechas."}
            </p>
          )}
        </div>
      )}

      {vista === "activo" && <MetricasActivo activoId={activoId} onActivo={setActivoId} vista={vistaActivo} onVista={setVistaActivo} filtro={filtro} />}

      {vista === "general" && (
        <>

      <div className="flex flex-wrap gap-3">
        <Cifra valor={r?.reservas ?? "—"} etiqueta="Reservas" />
        <Cifra valor={r?.prestamos ?? "—"} etiqueta="Préstamos" />
        <Cifra valor={r?.denegadas ?? "—"} etiqueta="Denegadas" />
        <Cifra valor={r?.canceladas ?? "—"} etiqueta="Canceladas" />
        <Cifra valor={r?.no_show ?? "—"} etiqueta="No show" />
        <Cifra valor={r?.devoluciones_problema ?? "—"} etiqueta="Devueltas tarde o no devueltas" />
        <Cifra valor={r?.no_devueltos_hoy ?? "—"} etiqueta="Activos sin devolver hoy" />
        <Cifra valor={r?.grupos_agrupados ?? "—"} etiqueta="Reservas con varios activos" />
      </div>

      {m && (
        <>
          <Seccion titulo="Inventario y seguro">
            <div className="flex flex-wrap gap-3">
              <Cifra valor={m.inventario.adicionados.equipo} etiqueta="Equipos adicionados" />
              <Cifra valor={m.inventario.adicionados.espacio} etiqueta="Espacios adicionados" />
              <Cifra valor={m.inventario.dados_de_baja.periodo} etiqueta="Bajas en el periodo" />
              <Cifra valor={m.inventario.dados_de_baja.hoy} etiqueta="No disponibles hoy" />
              <Cifra valor={m.inventario.mantenimiento.periodo} etiqueta="Entradas a mantenimiento" />
              <Cifra valor={m.inventario.mantenimiento.hoy} etiqueta="En mantenimiento hoy" />
            </div>
            <div className="pt-1 text-xs text-muted-foreground">Activos hoy por estado</div>
            <BarrasH
              titulo="Activos por estado"
              datos={Object.entries(m.inventario.total_hoy).map(([estado, total]) => ({
                etiqueta: ESTADOS_ACTIVO[estado as EstadoRecurso]?.texto ?? legible(estado, {}),
                total: total ?? 0,
              }))}
            />
            <div className="pt-1 text-xs text-muted-foreground">Seguro: reservas por grupo</div>
            <div className="flex flex-wrap gap-3">
              <Cifra valor={m.seguro.salen} etiqueta="Grupos que salen de Barranquilla" />
              <Cifra valor={m.seguro.lineas_salen} etiqueta="Activos en esos grupos" />
              <Cifra valor={m.seguro.no_salen} etiqueta="Grupos que no salen" />
              <Cifra valor={m.seguro.sin_dato} etiqueta="Sin dato (reservas viejas)" />
            </div>
          </Seccion>

          <Seccion titulo="Reservas por estado">
            <BarrasH
              titulo="Reservas por estado"
              datos={m.estados.map((e) => ({ etiqueta: legible(e.estado, ETIQUETA_ESTADO), total: e.total }))}
            />
          </Seccion>

          <div className="grid gap-4 @4xl:grid-cols-2">
            <PanelActivos activos={m.activos} />
            <Seccion titulo="Top 10 usuarios">
              <TopTabla
                filas={m.usuarios}
                nombre={(u) => (u.nombre ? `${u.nombre} · ${telefono(u.telefono_wa)}` : telefono(u.telefono_wa))}
                columnas={COLUMNAS_USUARIO}
              />
            </Seccion>
          </div>

          <Seccion titulo="Puntualidad">
            <Puntualidad datos={m.puntualidad} />
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

          <div className="grid gap-4 @3xl:grid-cols-2">
            <Seccion titulo="Hábitos">
              <dl className="space-y-1 text-sm">
                <div className="flex justify-between gap-2">
                  <dt className="text-muted-foreground">Duración promedio</dt>
                  <dd className="tabular-nums">{duracion(m.habitos.duracion_seg)}</dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt className="text-muted-foreground">Anticipación promedio</dt>
                  <dd className="tabular-nums">{duracion(m.habitos.anticipacion_seg)}</dd>
                </div>
              </dl>
              <div className="pt-1 text-xs text-muted-foreground">Canal de origen</div>
              <BarrasH
                titulo="Reservas por canal"
                datos={m.habitos.canales.map((c) => ({ etiqueta: legible(c.canal, ETIQUETA_CANAL), total: c.total }))}
              />
            </Seccion>

            <Seccion titulo="Bot">
              <dl className="space-y-1 text-sm">
                <div className="flex justify-between gap-2">
                  <dt className="text-muted-foreground">Mensajes registrados</dt>
                  <dd className="tabular-nums">{m.bot.recibidos}</dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt className="text-muted-foreground">Bloqueados por guardrails</dt>
                  <dd className="tabular-nums">{m.bot.bloqueados}</dd>
                </div>
              </dl>
              <div className="pt-1 text-xs text-muted-foreground">Tipos de mensaje más frecuentes</div>
              <BarrasH
                titulo="Mensajes por tipo"
                datos={m.bot.intents.map((i) => ({ etiqueta: legible(i.intent, ETIQUETA_INTENT), total: i.total }))}
              />
              <div className="pt-1 text-xs text-muted-foreground">Avisos automáticos (enviados / fallidos)</div>
              {m.bot.avisos.length === 0 ? (
                <Vacio />
              ) : (
                <ul className="space-y-0.5 text-sm">
                  {m.bot.avisos.map((a) => (
                    <li key={a.tipo} className="flex justify-between gap-2">
                      <span>{legible(a.tipo, ETIQUETA_AVISO)}</span>
                      <span className="tabular-nums">
                        {a.enviados} / {a.fallidos}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Seccion>
          </div>
        </>
      )}
        </>
      )}
    </div>
  )
}
