import { useEffect, useState, type ReactNode } from "react"

import { DownloadIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
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
  URL_EXCEL_DATOS,
  urlExcelMetricas,
  type EstadoRecurso,
  type FiltroMetricas,
  type Metricas,
  type Periodo,
} from "@/lib/api"
import { cn } from "@/lib/utils"
import { claveDe, hoy, sumarDias } from "@/features/calendario/calendario-utils"
import { ESTADOS_ACTIVO } from "@/features/activos/tree-utils"
import { MetricasActivo, type VistaActivo } from "./MetricasActivo"
import { MetricasUso } from "./MetricasUso"

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

export function etiquetaDia(dia: string) {
  return ETIQUETA_DIA.format(new Date(`${dia}T00:00:00Z`))
}

const ETIQUETA_MES = new Intl.DateTimeFormat("es-CO", { timeZone: "UTC", month: "short", year: "2-digit" })

/** Milisegundos legibles: 850 -> "850 ms"; 13400 -> "13,4 s". */
function ms(valor: number | null | undefined) {
  if (valor == null) return "—"
  return valor < 1000 ? `${valor} ms` : `${(valor / 1000).toLocaleString("es-CO", { maximumFractionDigits: 1 })} s`
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

/** Rótulo de un bloque dentro de una sección ("Por día", "Canal de origen"). */
export function Subtitulo({ children }: { children: ReactNode }) {
  return <h3 className="pt-2 text-xs text-muted-foreground first:pt-0">{children}</h3>
}

export function Seccion({ titulo, children, className }: { titulo: string; children: ReactNode; className?: string }) {
  return (
    <Card className={className}>
      <CardContent className="space-y-3">
        <h2 className="text-sm font-semibold">{titulo}</h2>
        {children}
      </CardContent>
    </Card>
  )
}

const DIA_MS = 86_400_000

/**
 * El backend solo manda los días con reservas: se rellenan los huecos con 0
 * para que el eje sea continuo y una racha vacía se vea como tal.
 */
export function conDiasVacios(datos: { dia: string; total: number }[]) {
  if (datos.length === 0) return datos
  const total = new Map(datos.map((d) => [d.dia, d.total]))
  const fin = Date.parse(`${datos[datos.length - 1].dia}T00:00:00Z`)
  const dias = []
  for (let t = Date.parse(`${datos[0].dia}T00:00:00Z`); t <= fin; t += DIA_MS) {
    const dia = new Date(t).toISOString().slice(0, 10)
    dias.push({ dia, total: total.get(dia) ?? 0 })
  }
  return dias
}

/** Paso "redondo" del eje Y (1, 2, 5, 10, 20…) para unas 4 divisiones; los conteos son enteros. */
function pasoEje(max: number) {
  const bruto = Math.max(1, max / 4)
  const base = 10 ** Math.floor(Math.log10(bruto))
  return Math.max(1, [1, 2, 5, 10].map((m) => m * base).find((p) => p >= bruto)!)
}

const numero = (n: number) => n.toLocaleString("es-CO", { maximumFractionDigits: 1 })
const reservas = (n: number) => `${n} reserva${n === 1 ? "" : "s"}`

type Punto = { clave: string; eje: string; tooltip: string; total: number }

/**
 * Columnas con eje Y (marcas redondas y líneas guía), el valor encima de cada
 * barra y tooltip al pasar el mouse. La barra más alta va en grafito pleno.
 * Con muchas barras el valor solo se rotula en contenedores anchos, y el eje X
 * se rotula cada `ejeCada` barras (cada `ejeAngosto` en contenedores angostos).
 * La tabla sr-only da a lectores de pantalla el detalle completo.
 */
export function Columnas({
  datos,
  titulo,
  ejeCada = 1,
  ejeAngosto = 1,
  unidad = reservas,
  desdeElFinal = false,
}: {
  datos: Punto[]
  titulo: string
  ejeCada?: number
  ejeAngosto?: number
  unidad?: (n: number) => string
  /** Cuenta los rótulos del eje desde la última barra, para que siempre se rotule. */
  desdeElFinal?: boolean
}) {
  const [hover, setHover] = useState<number | null>(null)
  const max = Math.max(1, ...datos.map((d) => d.total))
  const paso = pasoEje(max)
  const tope = Math.ceil(max / paso) * paso
  const marcas = Array.from({ length: tope / paso + 1 }, (_, i) => i * paso)
  const valores = datos.length <= 12 ? "" : datos.length <= 45 ? "hidden @3xl:block" : "hidden"
  const alto = (v: number) => `${(v / tope) * 100}%`
  // Con el eje salteado, la primera y la última fecha rotuladas se alinean hacia adentro para no salirse.
  // `pos` es la posición desde donde se cuentan los rótulos: el inicio, o el final con `desdeElFinal`.
  const pos = (idx: number) => (desdeElFinal ? datos.length - 1 - idx : idx)
  const ultima = Math.floor((datos.length - 1) / ejeCada) * ejeCada
  const [bordeInicio, bordeFin] = desdeElFinal ? ["justify-end", "justify-start"] : ["justify-start", "justify-end"]
  const alinear = (idx: number) =>
    ejeCada === 1 ? "justify-center" : pos(idx) === 0 ? bordeInicio : pos(idx) === ultima ? bordeFin : "justify-center"

  return (
    <div className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 pt-4">
      <div className="relative h-36 text-[11px] leading-none text-muted-foreground tabular-nums @6xl:h-44" aria-hidden>
        {/* Reserva el ancho del número más largo; los visibles van posicionados. */}
        <span className="invisible">{tope}</span>
        {marcas.map((v) => (
          <span key={v} className="absolute right-0 translate-y-1/2" style={{ bottom: alto(v) }}>
            {v}
          </span>
        ))}
      </div>
      <div className="relative h-36 @6xl:h-44" aria-hidden>
        {marcas.map((v) => (
          <div
            key={v}
            className={cn("absolute inset-x-0 border-t", v === 0 ? "border-border" : "border-dashed border-border/70")}
            style={{ bottom: alto(v) }}
          />
        ))}
        <div className={cn("absolute inset-0 flex items-end", datos.length <= 60 && "gap-[2px]")}>
          {datos.map((d, idx) => (
            <div
              key={d.clave}
              className="group relative flex h-full min-w-0 flex-1 items-end"
              onMouseEnter={() => setHover(idx)}
              onMouseLeave={() => setHover((h) => (h === idx ? null : h))}
            >
              {d.total > 0 && (
                <>
                  <span
                    className={cn("absolute inset-x-0 mb-0.5 text-center text-[11px] leading-none font-medium tabular-nums", valores)}
                    style={{ bottom: alto(d.total) }}
                  >
                    {d.total}
                  </span>
                  <div
                    className={cn(
                      "w-full rounded-t-sm transition-colors group-hover:bg-primary",
                      d.total === max ? "bg-primary" : "bg-primary/55",
                    )}
                    style={{ height: alto(d.total) }}
                  />
                </>
              )}
              {hover === idx && (
                <div
                  className="pointer-events-none absolute left-1/2 z-10 mb-5 -translate-x-1/2 rounded-md bg-foreground px-2 py-1 text-xs whitespace-nowrap text-background shadow-md"
                  style={{ bottom: alto(d.total) }}
                >
                  {d.tooltip} · {unidad(d.total)}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
      <div />
      <div className={cn("mt-1.5 flex text-[11px] text-muted-foreground tabular-nums", datos.length <= 60 && "gap-[2px]")} aria-hidden>
        {datos.map((d, idx) => (
          <span
            key={d.clave}
            className={cn(
              "flex min-w-0 flex-1 whitespace-nowrap",
              alinear(idx),
              pos(idx) % ejeCada ? "invisible" : pos(idx) % ejeAngosto ? "@max-4xl:invisible" : "",
            )}
          >
            {d.eje}
          </span>
        ))}
      </div>
      <table className="sr-only">
        <caption>{titulo}</caption>
        <tbody>
          {datos.map((d) => (
            <tr key={d.clave}>
              <td>{d.tooltip}</td>
              <td>{d.total}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export type Barra = { etiqueta: string; total: number }

/** Dos claves con la misma etiqueta (p. ej. confirmacion_cpa y confirmacion_lacs) se suman en una barra. */
function sumarPorEtiqueta(barras: Barra[]) {
  const totales = new Map<string, number>()
  for (const b of barras) totales.set(b.etiqueta, (totales.get(b.etiqueta) ?? 0) + b.total)
  return [...totales].map(([etiqueta, total]) => ({ etiqueta, total })).sort((a, b) => b.total - a.total)
}

/** Barras horizontales con el conteo al lado; tabla sr-only para lectores de pantalla. */
export function BarrasH({ datos, titulo }: { datos: Barra[]; titulo: string }) {
  const max = Math.max(1, ...datos.map((d) => d.total))
  if (datos.every((d) => d.total === 0)) return <Vacio />
  return (
    <>
      <ul className="space-y-1.5" aria-hidden>
        {datos.map((d) => (
          <li key={d.etiqueta} className="grid grid-cols-[minmax(6rem,11rem)_1fr_2.5rem] items-center gap-3 text-xs">
            <span className="truncate" title={d.etiqueta}>
              {d.etiqueta}
            </span>
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

const DIAS_LARGOS = ["Domingo", "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado"]

/** Rótulo del gráfico con su dato clave al lado ("Martes concentra el 30 %…"). */
function Encabezado({ titulo, dato }: { titulo: string; dato: ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5">
      <Subtitulo>{titulo}</Subtitulo>
      <p className="text-xs text-muted-foreground tabular-nums">{dato}</p>
    </div>
  )
}

/** Por día, por día de la semana y por hora de inicio; los dos últimos lado a lado desde @3xl. */
export function CuandoSeReserva({ m, filtro }: { m: Pick<Metricas, "reservas_por_dia" | "por_dia_semana" | "por_hora">; filtro: FiltroMetricas }) {
  const dias = conDiasVacios(m.reservas_por_dia)
  const total = dias.reduce((s, d) => s + d.total, 0)
  if (total === 0) return <Vacio>Sin reservas en {etiquetaFiltro(filtro)}.</Vacio>

  const pico = <T extends { total: number }>(xs: T[]) => xs.reduce((a, b) => (b.total > a.total ? b : a))
  const pct = (n: number) => `${Math.round((n / total) * 100)} %`
  const diaPico = pico(dias)
  const semana = ORDEN_SEMANA.map((k) => ({ k, total: m.por_dia_semana[k] }))
  const semanaPico = pico(semana)
  const horas = m.por_hora.map((t, h) => ({ h, total: t }))
  const horaPico = pico(horas)
  const hora = (h: number) => `${h}:00`
  const fuerte = "font-medium text-foreground"

  return (
    <>
      <div className="space-y-1">
        <Encabezado
          titulo="Por día"
          dato={
            <>
              <strong className={fuerte}>{reservas(total)}</strong> · {numero(total / dias.length)} por día en promedio · pico
              de <strong className={fuerte}>{diaPico.total}</strong> el {etiquetaDia(diaPico.dia)}
            </>
          }
        />
        <Columnas
          titulo={`Reservas por día en ${etiquetaFiltro(filtro)}`}
          ejeCada={Math.max(1, Math.ceil(dias.length / 8))}
          ejeAngosto={Math.max(1, Math.ceil(dias.length / 8)) * 3}
          datos={dias.map((d) => ({ clave: d.dia, eje: etiquetaDia(d.dia), tooltip: etiquetaDia(d.dia), total: d.total }))}
        />
      </div>
      <div className="grid gap-6 pt-2 @3xl:grid-cols-2">
        <div className="space-y-1">
          <Encabezado
            titulo="Por día de la semana"
            dato={
              <>
                <strong className={fuerte}>{DIAS_LARGOS[semanaPico.k]}</strong> concentra el {pct(semanaPico.total)} (
                {reservas(semanaPico.total)})
              </>
            }
          />
          <Columnas
            titulo="Reservas por día de la semana"
            datos={semana.map(({ k, total }) => ({ clave: String(k), eje: DIAS_SEMANA[k], tooltip: DIAS_LARGOS[k], total }))}
          />
        </div>
        <div className="space-y-1">
          <Encabezado
            titulo="Por hora de inicio"
            dato={
              <>
                Hora pico <strong className={fuerte}>{hora(horaPico.h)}</strong> · {pct(horaPico.total)} ({reservas(horaPico.total)})
              </>
            }
          />
          <Columnas
            titulo="Reservas por hora de inicio"
            ejeAngosto={3}
            datos={horas.map(({ h, total }) => ({ clave: String(h), eje: String(h), tooltip: `${hora(h)} a ${h}:59`, total }))}
          />
        </div>
      </div>
    </>
  )
}

/**
 * Grupo de cifras en una sola superficie dividida por líneas finas, en vez de
 * una tarjeta por número. Las columnas las fija quien lo usa según cuántas
 * cifras lleva, para que ninguna fila quede con una sola.
 */
export function Cifras({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-2 gap-px overflow-hidden rounded-xl bg-foreground/10 ring-1 ring-foreground/10", className)}>
      {children}
    </dl>
  )
}

export function Cifra({ valor, etiqueta, detalle }: { valor: ReactNode; etiqueta: string; detalle?: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 bg-card px-4 py-3">
      <dt className="text-xs text-muted-foreground">{etiqueta}</dt>
      <dd className="order-first text-2xl font-bold tabular-nums">{valor}</dd>
      {detalle && <dd className="text-xs text-muted-foreground tabular-nums">{detalle}</dd>}
    </div>
  )
}

/** Los ceros van en gris para que las cifras que importan resalten en la tabla. */
function Numero({ valor }: { valor: ReactNode }) {
  return <span className={valor === 0 ? "text-muted-foreground" : undefined}>{valor}</span>
}

/** Nombre del estudiante con su teléfono debajo, en gris. */
export function Persona({ nombre, telefonoWa }: { nombre: string | null; telefonoWa: string | null }) {
  if (!nombre) return <>{telefono(telefonoWa)}</>
  return (
    <>
      <span className="block">{nombre}</span>
      <span className="block text-xs font-normal text-muted-foreground tabular-nums">{telefono(telefonoWa)}</span>
    </>
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
  nombre: (fila: T) => ReactNode
  columnas: Columna<T>[]
}) {
  if (filas.length === 0) return <Vacio />
  return (
    <>
      <ul className="divide-y rounded-md border @2xl:hidden">
        {filas.map((f, idx) => (
          <li key={idx} className="space-y-1 px-3 py-2 text-sm">
            <div className="font-medium">{nombre(f)}</div>
            <div className="text-xs text-muted-foreground tabular-nums">
              {columnas.map((c) => `${c.titulo}: ${c.valor(f)}`).join(" · ")}
            </div>
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
                <TableCell className="min-w-48 whitespace-normal @6xl:py-3">{nombre(f)}</TableCell>
                {columnas.map((c) => (
                  <TableCell key={c.titulo} className="text-right tabular-nums @6xl:py-3">
                    <Numero valor={c.valor(f)} />
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
    <div className="grid gap-4 @lg:grid-cols-2">
      {filas.map(({ nombre, d }) => (
        <div key={nombre} className="space-y-1">
          <div className="text-xs text-muted-foreground">{nombre} a tiempo</div>
          <div className="text-2xl font-bold tabular-nums">{d ? porcentaje(d.a_tiempo, d.total) : "—"}</div>
          {d && (
            <div className="h-1.5 rounded-sm bg-muted" aria-hidden>
              <div className="h-full rounded-sm bg-primary/60" style={{ width: `${d.total ? (d.a_tiempo / d.total) * 100 : 0}%` }} />
            </div>
          )}
          <div className="text-xs text-muted-foreground tabular-nums">
            {!d
              ? "Sin registros"
              : d.retraso_seg == null
                ? `${d.a_tiempo} de ${d.total} · ninguna tardía`
                : `${d.a_tiempo} de ${d.total} · las tardías se atrasan ${duracion(d.retraso_seg)} en promedio`}
          </div>
        </div>
      ))}
    </div>
  )
}

/** Pares etiqueta / valor alineados a los dos extremos. */
export function Datos({ filas }: { filas: [string, ReactNode][] }) {
  return (
    <dl className="space-y-1.5 text-sm">
      {filas.map(([k, v]) => (
        <div key={k} className="flex justify-between gap-4">
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="text-right tabular-nums">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

function PanelActivos({ activos }: { activos: Metricas["activos"] }) {
  const [tipo, setTipo] = useState<"equipo" | "espacio">("equipo")
  return (
    <Card>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">Top 10 activos</h2>
          <Tabs value={tipo} onValueChange={(v) => setTipo(v as "equipo" | "espacio")}>
            <TabsList>
              <TabsTrigger value="equipo">Equipos</TabsTrigger>
              <TabsTrigger value="espacio">Espacios</TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
        <TopTabla filas={activos[tipo]} nombre={(a) => a.nombre} columnas={COLUMNAS_ACTIVO} />
      </CardContent>
    </Card>
  )
}

function Avisos({ avisos }: { avisos: Metricas["bot"]["avisos"] }) {
  if (avisos.length === 0) return <Vacio />
  return (
    <table className="w-full text-sm">
      <thead className="text-xs text-muted-foreground">
        <tr>
          <th className="pb-1 text-left font-normal">Tipo</th>
          <th className="pb-1 text-right font-normal">Enviados</th>
          <th className="pb-1 text-right font-normal">Fallidos</th>
        </tr>
      </thead>
      <tbody className="tabular-nums">
        {avisos.map((a) => (
          <tr key={a.tipo}>
            <td className="py-0.5">{legible(a.tipo, ETIQUETA_AVISO)}</td>
            <td className="py-0.5 text-right">
              <Numero valor={a.enviados} />
            </td>
            {/* Un aviso que no salió es un estudiante que no se enteró: va en rojo. */}
            <td className={cn("py-0.5 text-right", a.fallidos ? "text-destructive-text" : "text-muted-foreground")}>{a.fallidos}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** Un rango necesita las dos fechas y hasta >= desde (YYYY-MM-DD se compara como texto). */
function DescargarExcel({ filtro, valido }: { filtro: FiltroMetricas; valido: boolean }) {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="outline">
          <DownloadIcon />
          Descargar Excel
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Descargar Excel</DialogTitle>
          <DialogDescription>Elige qué archivo quieres.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-2">
          <Button variant="outline" className="h-auto justify-start whitespace-normal py-3 text-left" asChild={valido} disabled={!valido}>
            {valido ? (
              <a href={urlExcelMetricas(filtro)} download>
                <span>
                  <span className="block font-medium">Métricas</span>
                  <span className="block text-xs text-muted-foreground">
                    General y Por uso de {etiquetaFiltro(filtro)}, el periodo que tienes elegido. Tablas con colores y gráficos.
                  </span>
                </span>
              </a>
            ) : (
              <span>Métricas: primero corrige las fechas del periodo.</span>
            )}
          </Button>
          <Button variant="outline" className="h-auto justify-start whitespace-normal py-3 text-left" asChild>
            <a href={URL_EXCEL_DATOS} download>
              <span>
                <span className="block font-medium">Base de datos (raw)</span>
                <span className="block text-xs text-muted-foreground">
                  Todas las tablas completas, sin filtrar por periodo: una hoja por tabla y un índice con cuántas filas trae cada una.
                </span>
              </span>
            </a>
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

/** Turnos agrupados por semana (desde el lunes) y por mes. */
function PorSemanaYMes({ m }: { m: Pick<Metricas, "reservas_por_semana" | "reservas_por_mes"> }) {
  if (m.reservas_por_semana.length === 0) return null
  return (
    <div className="grid gap-6 pt-2 @3xl:grid-cols-2">
      <div className="space-y-1">
        <Subtitulo>Por semana</Subtitulo>
        <Columnas
          titulo="Reservas por semana"
          ejeCada={Math.max(1, Math.ceil(m.reservas_por_semana.length / 6))}
          desdeElFinal
          datos={m.reservas_por_semana.map((s) => ({
            clave: s.inicio,
            eje: etiquetaDia(s.inicio),
            tooltip: `Semana del ${etiquetaDia(s.inicio)}`,
            total: s.total,
          }))}
        />
      </div>
      <div className="space-y-1">
        <Subtitulo>Por mes</Subtitulo>
        <Columnas
          titulo="Reservas por mes"
          ejeCada={Math.max(1, Math.ceil(m.reservas_por_mes.length / 6))}
          desdeElFinal
          datos={m.reservas_por_mes.map((s) => {
            const mes = ETIQUETA_MES.format(new Date(`${s.inicio}T00:00:00Z`))
            return { clave: s.inicio, eje: mes, tooltip: mes, total: s.total }
          })}
        />
      </div>
    </div>
  )
}

function RendimientoBot({ m }: { m: Metricas | null }) {
  const b = m?.bot
  const si = b?.sin_intervencion
  return (
    <>
      <Cifras className="@2xl:grid-cols-4">
        <Cifra valor={b?.recibidos ?? "—"} etiqueta="Mensajes registrados" />
        <Cifra
          valor={b?.bloqueados ?? "—"}
          etiqueta="Bloqueados por guardrails"
          detalle={b && b.recibidos ? `${porcentaje(b.bloqueados, b.recibidos)} de los mensajes` : undefined}
        />
        <Cifra
          valor={si ? porcentaje(si.sin_intervencion, si.solicitudes) : "—"}
          etiqueta="Solicitudes del bot sin intervención humana"
          detalle={si ? `${si.sin_intervencion} de ${si.solicitudes} no pasaron por la LACS` : undefined}
        />
        <Cifra
          valor={ms(b?.latencia.promedio_ms)}
          etiqueta="Tiempo de respuesta promedio"
          detalle={b?.latencia.n ? `mediana ${ms(b.latencia.p50_ms)} · p95 ${ms(b.latencia.p95_ms)}` : undefined}
        />
      </Cifras>

      {b && (
        <>
          <div className="grid gap-4 @4xl:grid-cols-2">
            <Seccion titulo="Tipos de mensaje más frecuentes">
              <BarrasH
                titulo="Mensajes por tipo"
                datos={sumarPorEtiqueta(b.intents.map((i) => ({ etiqueta: legible(i.intent, ETIQUETA_INTENT), total: i.total })))}
              />
            </Seccion>
            <Seccion titulo="Tiempo de respuesta por tipo">
              {b.latencia_por_intent.length === 0 ? (
                <Vacio>Sin mensajes medidos en este periodo (se mide desde que existe la columna latencia_ms).</Vacio>
              ) : (
                <Datos
                  filas={b.latencia_por_intent.map((l): [string, ReactNode] => [
                    `${legible(l.intent, ETIQUETA_INTENT)} (${l.total})`,
                    ms(l.promedio_ms),
                  ])}
                />
              )}
              <p className="text-xs text-muted-foreground">
                Desde que llega el mensaje hasta que sale la respuesta; incluye la clasificación con el LLM.
              </p>
            </Seccion>
          </div>
          <Seccion titulo="Avisos automáticos">
            <Avisos avisos={b.avisos} />
          </Seccion>
        </>
      )}
    </>
  )
}

export function filtroValido(f: FiltroMetricas) {
  return f.periodo !== "rango" || Boolean(f.desde && f.hasta && f.hasta >= f.desde)
}

function General({ m, filtro }: { m: Metricas | null; filtro: FiltroMetricas }) {
  const r = m?.resumen
  const deReservas = (n: number | undefined) =>
    r && r.reservas && n != null ? `${porcentaje(n, r.reservas)} de las reservas` : undefined

  return (
    <>
      <Cifras className="@2xl:grid-cols-4">
        <Cifra valor={r?.reservas ?? "—"} etiqueta="Reservas" />
        <Cifra valor={r?.prestamos ?? "—"} etiqueta="Préstamos" detalle={deReservas(r?.prestamos)} />
        <Cifra valor={r?.denegadas ?? "—"} etiqueta="Denegadas" detalle={deReservas(r?.denegadas)} />
        <Cifra valor={r?.canceladas ?? "—"} etiqueta="Canceladas" detalle={deReservas(r?.canceladas)} />
        <Cifra valor={r?.no_show ?? "—"} etiqueta="No show" detalle={deReservas(r?.no_show)} />
        <Cifra valor={r?.devoluciones_problema ?? "—"} etiqueta="Devueltas tarde o no devueltas" />
        <Cifra valor={r?.no_devueltos_hoy ?? "—"} etiqueta="Activos sin devolver hoy" />
        <Cifra valor={r?.grupos_agrupados ?? "—"} etiqueta="Reservas con varios activos" />
      </Cifras>

      {m && (
        <>
          <div className="grid gap-4 @4xl:grid-cols-2">
            <Seccion titulo="Reservas por estado">
              <BarrasH
                titulo="Reservas por estado"
                datos={m.estados.map((e) => ({ etiqueta: legible(e.estado, ETIQUETA_ESTADO), total: e.total }))}
              />
            </Seccion>
            <Seccion titulo="Puntualidad">
              <Puntualidad datos={m.puntualidad} />
            </Seccion>
          </div>

          <Seccion titulo="Cuándo se reserva">
            <CuandoSeReserva m={m} filtro={filtro} />
            <PorSemanaYMes m={m} />
          </Seccion>

          <PanelActivos activos={m.activos} />

          <Seccion titulo="Top 10 usuarios">
            <TopTabla
              filas={m.usuarios}
              nombre={(u) => <Persona nombre={u.nombre} telefonoWa={u.telefono_wa} />}
              columnas={COLUMNAS_USUARIO}
            />
          </Seccion>

          <div className="grid gap-4 @4xl:grid-cols-2">
            <Seccion titulo="Inventario">
              <Cifras className="rounded-md @lg:grid-cols-3">
                <Cifra valor={m.inventario.adicionados.equipo} etiqueta="Equipos adicionados" />
                <Cifra valor={m.inventario.adicionados.espacio} etiqueta="Espacios adicionados" />
                <Cifra valor={m.inventario.dados_de_baja.periodo} etiqueta="Bajas en el periodo" />
                <Cifra valor={m.inventario.dados_de_baja.hoy} etiqueta="No disponibles hoy" />
                <Cifra valor={m.inventario.mantenimiento.periodo} etiqueta="Entradas a mantenimiento" />
                <Cifra valor={m.inventario.mantenimiento.hoy} etiqueta="En mantenimiento hoy" />
              </Cifras>
              <Subtitulo>Activos hoy por estado</Subtitulo>
              <BarrasH
                titulo="Activos por estado"
                datos={Object.entries(m.inventario.total_hoy).map(([estado, total]) => ({
                  etiqueta: ESTADOS_ACTIVO[estado as EstadoRecurso]?.texto ?? legible(estado, {}),
                  total: total ?? 0,
                }))}
              />
            </Seccion>

            <Seccion titulo="Seguro">
              <Cifras className="rounded-md">
                <Cifra valor={m.seguro.salen} etiqueta="Grupos que salen de Barranquilla" />
                <Cifra valor={m.seguro.lineas_salen} etiqueta="Activos en esos grupos" />
                <Cifra valor={m.seguro.no_salen} etiqueta="Grupos que no salen" />
                <Cifra valor={m.seguro.sin_dato} etiqueta="Sin dato (reservas viejas)" />
              </Cifras>
              <p className="text-xs text-muted-foreground">Cuenta reservas por grupo: un grupo con varios activos cuenta una vez.</p>
            </Seccion>
          </div>

          <Seccion titulo="Hábitos">
            <Datos
              filas={[
                ["Duración promedio", duracion(m.habitos.duracion_seg)],
                ["Anticipación promedio", duracion(m.habitos.anticipacion_seg)],
              ]}
            />
            <Subtitulo>Canal de origen</Subtitulo>
            <BarrasH
              titulo="Reservas por canal"
              datos={m.habitos.canales.map((c) => ({ etiqueta: legible(c.canal, ETIQUETA_CANAL), total: c.total }))}
            />
          </Seccion>
        </>
      )}
    </>
  )
}

export function MetricasPanel() {
  const [periodo, setPeriodo] = useState<Periodo>("30")
  // Al elegir "Personalizado" arrancan con los últimos 30 días (fechas civiles de la LACS).
  const [desde, setDesde] = useState(() => claveDe(sumarDias(hoy(), -30)))
  const [hasta, setHasta] = useState(() => claveDe(hoy()))
  const [vista, setVista] = useState<"general" | "activo" | "uso" | "bot">("general")
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

  // Una respuesta que aún es de otro pedido no se pinta como si fuera del nuevo.
  const m = data && valido && respuestaDe(data, filtro) ? data : null
  // El timeline siempre es de todo el tiempo: ahí el periodo no aplica y no se muestra.
  const conPeriodo = !(vista === "activo" && activoId != null && vistaActivo === "timeline")

  return (
    <div className="@container space-y-4">
      <div className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h1 className="text-xl font-semibold tracking-tight">Métricas</h1>
          <div className="flex flex-wrap items-center gap-2">
            {conPeriodo && (
              <Tabs value={periodo} onValueChange={(v) => setPeriodo(v as Periodo)}>
                <TabsList aria-label="Periodo">
                  <TabsTrigger value="30">30 días</TabsTrigger>
                  <TabsTrigger value="90">90 días</TabsTrigger>
                  <TabsTrigger value="todo">Todo</TabsTrigger>
                  <TabsTrigger value="rango">Personalizado</TabsTrigger>
                </TabsList>
              </Tabs>
            )}
            <DescargarExcel filtro={filtro} valido={valido} />
          </div>
        </div>

        {conPeriodo && periodo === "rango" && (
          <div className="flex flex-col gap-2 @sm:flex-row @sm:flex-wrap @sm:items-end @sm:justify-end">
            <div className="space-y-1">
              <Label htmlFor="metricas-desde">Desde</Label>
              <Input
                id="metricas-desde"
                type="date"
                value={desde}
                max={hasta || undefined}
                onChange={(e) => setDesde(e.target.value)}
                aria-invalid={!valido}
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
                aria-invalid={!valido}
                className="@sm:w-44"
              />
            </div>
            {!valido && (
              <p role="alert" className="text-xs text-destructive-text @sm:basis-full @sm:text-right">
                {desde && hasta ? "«Hasta» no puede ser anterior a «Desde»." : "Elige las dos fechas."}
              </p>
            )}
          </div>
        )}

        {/* Pestañas de vista (qué se mira) separadas del periodo (cuándo): la
            línea las distingue de los filtros segmentados de arriba. */}
        <Tabs value={vista} onValueChange={(v) => setVista(v as typeof vista)} className="border-b">
          <TabsList variant="line" className="h-9">
            <TabsTrigger value="general">General</TabsTrigger>
            <TabsTrigger value="activo">Por activo</TabsTrigger>
            <TabsTrigger value="uso">Por uso</TabsTrigger>
            <TabsTrigger value="bot">Rendimiento bot</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      {(vista === "general" || vista === "bot") &&
        (error && !data ? (
          <p className="text-sm text-destructive-text">No se pudieron cargar las métricas.</p>
        ) : vista === "general" ? (
          <General m={m} filtro={filtro} />
        ) : (
          <RendimientoBot m={m} />
        ))}

      {vista === "uso" && <MetricasUso filtro={filtro} />}

      {vista === "activo" && (
        <MetricasActivo activoId={activoId} onActivo={setActivoId} vista={vistaActivo} onVista={setVistaActivo} filtro={filtro} />
      )}
    </div>
  )
}
