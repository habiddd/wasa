import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { usePolling } from "@/hooks/use-polling"
import { fetchMetricas, type Interaccion } from "@/lib/api"
import { formatearFechaHoraLACS } from "@/lib/fechas"

const ESTADO_ENVIO_INFO: Record<
  string,
  { variant: "success" | "destructive" | "warning" | "secondary"; texto: string }
> = {
  "n/a": { variant: "secondary", texto: "—" },
  error_http: { variant: "destructive", texto: "Error de envío" },
  pendiente: { variant: "warning", texto: "Pendiente" },
  enviado: { variant: "warning", texto: "Enviado" },
  // Entregado en gris y leído en verde: son pasos distintos y antes se veían iguales.
  entregado: { variant: "secondary", texto: "Entregado" },
  leido: { variant: "success", texto: "Leído" },
  reproducido: { variant: "success", texto: "Reproducido" },
  fallido: { variant: "destructive", texto: "Fallido" },
}

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

const ETIQUETA_MOTIVO: Record<string, string> = {
  intent_no_permitido: "Pedido no permitido",
  rate_limit_excedido: "Demasiados mensajes seguidos",
  mensaje_sospechoso: "Mensaje sospechoso",
}

// Las pruebas E2E del bot escriben con este número (no llega a WhatsApp real):
// se marcan para no confundirlas con estudiantes, sin ocultarlas.
const TELEFONO_PRUEBA = "000000000000"

function telefono(valor: string | null) {
  if (!valor) return "-"
  return valor === TELEFONO_PRUEBA ? "Prueba del bot" : valor
}

// Una clave nueva que todavía no esté en el mapa se ve legible igual.
function legible(clave: string | null, mapa: Record<string, string>) {
  if (!clave) return "-"
  if (mapa[clave]) return mapa[clave]
  const texto = clave.replaceAll("_", " ")
  return texto.charAt(0).toUpperCase() + texto.slice(1)
}

function PillEstadoEnvio({ estado }: { estado: string }) {
  const info = ESTADO_ENVIO_INFO[estado] ?? { variant: "secondary" as const, texto: estado || "—" }
  return <Badge variant={info.variant}>{info.texto}</Badge>
}

// "dia" viene del backend como fecha sin hora (func.date() en SQL, ver
// dashboard.py) -- se formatea en UTC para no correr el día al convertir a
// la zona de la LACS.
const ETIQUETA_DIA = new Intl.DateTimeFormat("es-CO", {
  timeZone: "UTC",
  day: "numeric",
  month: "short",
})

function etiquetaDia(dia: string) {
  return ETIQUETA_DIA.format(new Date(`${dia}T00:00:00Z`))
}

/**
 * Barras finas ancladas a la línea base, con tooltip al pasar el mouse. La
 * tabla oculta (sr-only) le da a lectores de pantalla el mismo detalle que
 * el tooltip da a la vista.
 */
function ReservasPorDiaChart({ datos }: { datos: { dia: string; total: number }[] }) {
  const [hover, setHover] = useState<number | null>(null)
  const max = Math.max(1, ...datos.map((d) => d.total))

  if (datos.length === 0) {
    return <p className="text-xs text-muted-foreground">Sin reservas en los últimos 30 días.</p>
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
        <caption>Reservas por día en los últimos 30 días</caption>
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

/** Pantallas angostas: una tarjeta por interacción, sin columnas. */
function ListaInteracciones({ interacciones }: { interacciones: Interaccion[] }) {
  return (
    <ul className="divide-y rounded-md border">
      {interacciones.map((i, idx) => (
        <li key={idx} className="space-y-1 px-3 py-2.5 text-sm">
          <span className="flex items-start justify-between gap-2">
            <span className="font-medium">{telefono(i.telefono_wa)}</span>
            <PillEstadoEnvio estado={i.estado_envio} />
          </span>
          <span className="block text-xs text-muted-foreground">
            {legible(i.intent, ETIQUETA_INTENT)} · Permitido: {i.permitido ? "sí" : "no"}
          </span>
          {i.motivo_bloqueo && (
            <span className="block text-xs text-muted-foreground">{legible(i.motivo_bloqueo, ETIQUETA_MOTIVO)}</span>
          )}
          <span className="block text-xs text-muted-foreground">
            {formatearFechaHoraLACS(i.creado_en)}
          </span>
        </li>
      ))}
    </ul>
  )
}

/**
 * Desde ~670 px: tabla. Intent y Permitido tienen columna propia solo si
 * hay espacio; si no, van en una línea bajo el teléfono. Motivo bloqueo es
 * lo primero que sobra, y absorbe el espacio libre (con whitespace-normal)
 * en vez de que el navegador lo reparta entre columnas cortas.
 *
 * En pantallas muy anchas (@6xl) sube el tamaño de letra y el padding: hay
 * espacio de sobra para leer más cómodo, no solo para que la tabla se vea
 * ancha.
 */
function TablaInteracciones({ interacciones }: { interacciones: Interaccion[] }) {
  return (
    <div className="rounded-md border">
      <Table className="@6xl:text-base">
        <TableHeader>
          <TableRow>
            <TableHead className="@6xl:py-3">Fecha</TableHead>
            <TableHead className="@6xl:py-3">Teléfono</TableHead>
            <TableHead className="hidden @3xl:table-cell @6xl:py-3">Tipo de mensaje</TableHead>
            <TableHead className="hidden @3xl:table-cell @6xl:py-3">Permitido</TableHead>
            <TableHead className="hidden @4xl:table-cell @6xl:py-3">Motivo del bloqueo</TableHead>
            <TableHead className="@6xl:py-3">Entrega</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {interacciones.map((i, idx) => (
            <TableRow key={idx}>
              <TableCell className="@6xl:py-3">{formatearFechaHoraLACS(i.creado_en)}</TableCell>
              <TableCell className="whitespace-normal @6xl:py-3">
                {telefono(i.telefono_wa)}
                <p className="mt-0.5 text-xs text-muted-foreground @3xl:hidden">
                  {legible(i.intent, ETIQUETA_INTENT)} · Permitido: {i.permitido ? "sí" : "no"}
                  {i.motivo_bloqueo && ` · ${legible(i.motivo_bloqueo, ETIQUETA_MOTIVO)}`}
                </p>
              </TableCell>
              <TableCell className="hidden @3xl:table-cell @6xl:py-3">{legible(i.intent, ETIQUETA_INTENT)}</TableCell>
              <TableCell className="hidden @3xl:table-cell @6xl:py-3">
                {i.permitido ? "sí" : "no"}
              </TableCell>
              <TableCell className="hidden whitespace-normal @4xl:table-cell @6xl:py-3">
                {legible(i.motivo_bloqueo, ETIQUETA_MOTIVO)}
              </TableCell>
              <TableCell className="@6xl:py-3">
                <PillEstadoEnvio estado={i.estado_envio} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

export function MetricasPanel() {
  const { data, error } = usePolling(fetchMetricas, 30000)

  if (error && !data) {
    return <p className="text-sm text-destructive-text">No se pudieron cargar las métricas.</p>
  }

  return (
    <div className="@container space-y-4">
      <h1 className="text-xl font-semibold tracking-tight">Métricas del bot</h1>
      <div className="flex flex-wrap gap-3">
        <Card className="min-w-[140px]">
          <CardContent className="py-2">
            <div className="text-2xl font-bold">{data?.total_reservas_30d ?? "—"}</div>
            <div className="text-xs text-muted-foreground">Reservas (30 días)</div>
          </CardContent>
        </Card>
        <Card className="min-w-[140px]">
          <CardContent className="py-2">
            <div className="text-2xl font-bold">{data?.total_bloqueadas_30d ?? "—"}</div>
            <div className="text-xs text-muted-foreground">
              Mensajes bloqueados (30 días)
            </div>
          </CardContent>
        </Card>
        <Card className="min-w-[280px] flex-1">
          <CardContent className="py-2">
            <div className="mb-1.5 text-xs text-muted-foreground">Reservas por día (últimos 30 días)</div>
            {data && <ReservasPorDiaChart datos={data.reservas_por_dia} />}
          </CardContent>
        </Card>
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold">Últimas interacciones del bot</h2>
        {data && (
          <>
            <div className="@2xl:hidden">
              <ListaInteracciones interacciones={data.ultimas_interacciones} />
            </div>
            <div className="hidden @2xl:block">
              <TablaInteracciones interacciones={data.ultimas_interacciones} />
            </div>
          </>
        )}
      </div>
    </div>
  )
}
