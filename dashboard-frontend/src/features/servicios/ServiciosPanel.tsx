import { useEffect, useState, type ReactNode } from "react"
import { AlertTriangle, CircleCheck, ExternalLink } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { usePolling } from "@/hooks/use-polling"
import {
  cambiarProveedorWhatsapp,
  fetchMetricasMeta,
  fetchProveedorWhatsapp,
  fetchQr,
  fetchStatus,
  type EstadoServicio,
  type FiltroMeta,
  type MetricasMeta,
  type PeriodoMeta,
  type ProveedorWhatsapp,
} from "@/lib/api"
import { formatearHoraLACS } from "@/lib/fechas"
import { claveDe, hoy, sumarDias } from "@/features/calendario/calendario-utils"
import {
  BarrasH,
  Cifra,
  Cifras,
  Columnas,
  Datos,
  etiquetaDia,
  Subtitulo,
  Vacio,
} from "@/features/metricas/MetricasPanel"

const NOMBRES: Record<string, string> = {
  postgres: "Postgres",
  redis: "Redis",
  baileys: "Baileys / WhatsApp",
  llm: "LLM local",
  flask: "Flask",
}

// Baileys reporta el estado crudo de su socket; el resto ya manda texto.
const DETALLE_BAILEYS: Record<string, string> = {
  open: "conectado",
  connecting: "conectando…",
  close: "desconectado",
}

function EstadoBadge({ ok }: { ok: boolean }) {
  return (
    <Badge variant={ok ? "success" : "destructive"}>
      {ok ? "Funcionando" : "Con falla"}
    </Badge>
  )
}

function ServicioCard({ clave, servicio }: { clave: string; servicio: EstadoServicio }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {NOMBRES[clave] ?? clave}
          <EstadoBadge ok={servicio.ok} />
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-1 text-sm text-muted-foreground">
        <p className="break-words">
          {(clave === "baileys" && DETALLE_BAILEYS[servicio.detalle ?? ""]) || servicio.detalle || ""}
        </p>
        {clave === "baileys" && servicio.numero && <p>Número: {servicio.numero}</p>}
        {clave === "baileys" && servicio.buffer_atascado && (
          <p className="flex items-center gap-1 text-warning-text">
            <AlertTriangle className="size-3.5" />
            Buffer de eventos atascado (reintentando solo)
          </p>
        )}
      </CardContent>
    </Card>
  )
}

const NOMBRE_CANAL: Record<ProveedorWhatsapp, string> = {
  baileys: "Baileys",
  meta: "Meta (API oficial)",
}

// Con qué canal salen y entran los mensajes. Cambiarlo no toca la sesión de
// Baileys: queda conectado, solo deja de contestar.
function CanalWhatsappCard() {
  const { data, reload } = usePolling(fetchProveedorWhatsapp, 10000)
  const [pendiente, setPendiente] = useState<ProveedorWhatsapp | null>(null)
  const [cambiando, setCambiando] = useState(false)

  if (!data) return <Skeleton className="h-32 w-full" />

  // El detalle de cada canal vive en su propia sección (métricas de Meta,
  // sesión de Baileys); aquí solo se avisa lo que impide elegirlo.
  const { meta } = data.disponibles

  async function confirmar() {
    if (!pendiente) return
    setCambiando(true)
    try {
      await cambiarProveedorWhatsapp(pendiente)
      toast.success(`Los mensajes ahora salen por ${NOMBRE_CANAL[pendiente]}.`)
      reload()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setCambiando(false)
      setPendiente(null)
    }
  }

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Canal de WhatsApp</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <div className="flex gap-2">
            {(["baileys", "meta"] as const).map((p) =>
              data.activo === p ? (
                // El activo no se deshabilita: se vería gris, como si no se pudiera usar.
                <Button key={p} aria-pressed className="pointer-events-none">
                  <CircleCheck aria-hidden />
                  {NOMBRE_CANAL[p]}
                </Button>
              ) : (
                <Button
                  key={p}
                  variant="outline"
                  aria-pressed={false}
                  disabled={p === "meta" && !meta.configurado}
                  onClick={() => setPendiente(p)}
                >
                  {NOMBRE_CANAL[p]}
                </Button>
              ),
            )}
          </div>
          <p className="text-muted-foreground">
            Los mensajes salen y entran por <span className="font-medium text-foreground">{NOMBRE_CANAL[data.activo]}</span>.
          </p>
          {meta.configurado && meta.estado && !meta.estado.ok && (
            <p className="flex gap-1.5 break-words text-warning-text">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              Meta no responde: {meta.estado.detalle}
            </p>
          )}
          {!meta.configurado && (
            <p className="break-words text-muted-foreground">
              Para habilitar Meta, completa en <code>.env</code>: {meta.faltan.join(", ")}.
            </p>
          )}
        </CardContent>
        <Dialog open={!!pendiente} onOpenChange={(abierto) => !abierto && !cambiando && setPendiente(null)}>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>¿Cambiar el canal de WhatsApp?</DialogTitle>
              <DialogDescription>
                Los mensajes empezarán a salir por {pendiente && NOMBRE_CANAL[pendiente]}.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={() => setPendiente(null)} disabled={cambiando}>
                Cancelar
              </Button>
              <Button onClick={confirmar} disabled={cambiando}>
                Cambiar
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </Card>
      {data.activo === "meta" && meta.configurado && <MetricasMetaCard />}
    </>
  )
}

// Traducción de los valores que devuelve la Graph API.
const TEXTO_META: Record<string, string> = {
  GREEN: "alta",
  YELLOW: "media",
  RED: "baja",
  UNKNOWN: "sin datos",
  CONNECTED: "conectado",
  DISCONNECTED: "desconectado",
  FLAGGED: "marcado",
  RESTRICTED: "restringido",
  LIVE: "producción",
  SANDBOX: "pruebas",
  STANDARD: "estándar",
  HIGH: "alto",
  APPROVED: "aprobada",
  PENDING: "en revisión",
  REJECTED: "rechazada",
  PAUSED: "pausada",
  DISABLED: "deshabilitada",
  AVAILABLE: "sí",
  LIMITED: "limitado",
  BLOCKED: "bloqueado",
  AVAILABLE_WITHOUT_REVIEW: "disponible sin revisión",
  not_verified: "sin verificar",
  verified: "verificado",
  pending: "en revisión",
  SERVICE: "Servicio (respuestas)",
  UTILITY: "Utilidad",
  MARKETING: "Marketing",
  AUTHENTICATION: "Autenticación",
}
const tr = (v: string | null | undefined) => (v ? TEXTO_META[v] ?? v : "—")

// Rótulos cortos para el gráfico de cobros (los largos se cortaban).
const CATEGORIA_COBRO: Record<string, string> = {
  SERVICE: "Respuestas",
  UTILITY: "Plantillas de utilidad",
  MARKETING: "Plantillas de marketing",
  AUTHENTICATION: "Plantillas de autenticación",
}
const etiquetaCobro = (categoria: string, tipo: string) =>
  `${CATEGORIA_COBRO[categoria] ?? tr(categoria)}${tipo.startsWith("FREE") ? " · gratis" : ""}`

const IDIOMAS = new Intl.DisplayNames(["es"], { type: "language" })
function idioma(codigo: string) {
  try {
    return IDIOMAS.of(codigo.replace("_", "-")) ?? codigo
  } catch {
    return codigo
  }
}

// Plantillas de ejemplo que Meta crea en toda cuenta nueva; el bot no las usa.
const esPlantillaDeEjemplo = (nombre: string) => nombre.startsWith("jaspers_market_")

function Dato({ etiqueta, valor }: { etiqueta: string; valor: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{etiqueta}</dt>
      <dd className="break-words">{valor}</dd>
    </div>
  )
}

function limiteDiario(tier: string | null) {
  const n = tier?.match(/\d+/)?.[0]
  if (tier === "TIER_UNLIMITED") return "sin límite"
  return n ? `${Number(n).toLocaleString("es-CO")} personas nuevas cada 24 h` : tr(tier)
}

function tiempoRestante(segundos: number) {
  const h = Math.floor(segundos / 3600)
  const m = Math.floor((segundos % 3600) / 60)
  return h ? `${h} h ${m} min` : `${m} min`
}

const ZONA_META_DIA = 43200 // Meta corta los días a medianoche del Pacífico: mediodía cae en el día correcto.

const ETIQUETA_MES = new Intl.DateTimeFormat("es-CO", { month: "short", year: "2-digit", timeZone: "UTC" })
const ETIQUETA_MES_LARGO = new Intl.DateTimeFormat("es-CO", { month: "long", year: "numeric", timeZone: "UTC" })
const ETIQUETA_FECHA = new Intl.DateTimeFormat("es-CO", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" })
const MAX_DIAS_POR_DIA = 62 // Más de ~2 meses en columnas diarias no se lee: se agrupa por mes.

/** Un punto por día del periodo (o por mes si es largo), con 0 donde no hubo mensajes. */
function serieEnviados(porDia: MetricasMeta["uso"]["por_dia"], { desde, hasta }: MetricasMeta["periodo"]) {
  const enviados = new Map<string, number>()
  for (const p of porDia) {
    const dia = new Date((p.dia + ZONA_META_DIA) * 1000).toISOString().slice(0, 10)
    enviados.set(dia, (enviados.get(dia) ?? 0) + p.enviados)
  }
  const dias: { dia: string; total: number }[] = []
  for (let t = Date.parse(`${desde}T00:00:00Z`); t <= Date.parse(`${hasta}T00:00:00Z`); t += 86_400_000) {
    const dia = new Date(t).toISOString().slice(0, 10)
    dias.push({ dia, total: enviados.get(dia) ?? 0 })
  }
  if (dias.length <= MAX_DIAS_POR_DIA) {
    return { porMes: false, puntos: dias.map((d) => ({ clave: d.dia, eje: etiquetaDia(d.dia), tooltip: etiquetaDia(d.dia), total: d.total })) }
  }
  const meses = new Map<string, number>()
  for (const d of dias) meses.set(d.dia.slice(0, 7), (meses.get(d.dia.slice(0, 7)) ?? 0) + d.total)
  return {
    porMes: true,
    puntos: [...meses].map(([mes, total]) => {
      const fecha = new Date(`${mes}-01T00:00:00Z`)
      return { clave: mes, eje: ETIQUETA_MES.format(fecha), tooltip: ETIQUETA_MES_LARGO.format(fecha), total }
    }),
  }
}

const ETIQUETA_PERIODO_META: Record<Exclude<PeriodoMeta, "rango">, string> = {
  "7": "Última semana",
  "30": "Último mes",
  "365": "Último año",
}

const mensajes = (n: number) => `${n} mensaje${n === 1 ? "" : "s"}`

// Solo se muestra con Meta activo. Se refresca cada minuto y al cambiar el
// periodo: cada carga son tres consultas a la Graph API.
function MetricasMetaCard() {
  const [periodo, setPeriodo] = useState<PeriodoMeta>("30")
  // Al elegir "Personalizado" arrancan con el último mes (fechas civiles de la LACS).
  const [desde, setDesde] = useState(() => claveDe(sumarDias(hoy(), -30)))
  const [hasta, setHasta] = useState(() => claveDe(hoy()))
  const filtro: FiltroMeta = { periodo, desde, hasta }
  const clave = JSON.stringify(filtro)
  const valido = periodo !== "rango" || Boolean(desde && hasta && hasta >= desde)
  const { data, error, reload } = usePolling(
    async () => (valido ? { clave, datos: await fetchMetricasMeta(filtro) } : null),
    60000,
  )
  // usePolling solo recarga por intervalo: al cambiar de filtro se pide de nuevo.
  useEffect(() => {
    reload()
  }, [clave, reload])
  // Una respuesta de otro periodo no se pinta como si fuera la del nuevo.
  const m = valido && data?.clave === clave ? data.datos : null
  // Meta solo guarda los últimos 365 días.
  const minimo = claveDe(sumarDias(hoy(), -364))

  return (
    <Card className="@container">
      <CardHeader className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="space-y-1">
            <CardTitle>API de Meta</CardTitle>
            {m && (
              <CardDescription>
                Consultado a las {formatearHoraLACS(m.consultado_en)} · Meta suma los mensajes con unas horas de retraso.
              </CardDescription>
            )}
          </div>
          <Tabs value={periodo} onValueChange={(v) => setPeriodo(v as PeriodoMeta)}>
            <TabsList aria-label="Periodo">
              <TabsTrigger value="7">Semana</TabsTrigger>
              <TabsTrigger value="30">Mes</TabsTrigger>
              <TabsTrigger value="365">Año</TabsTrigger>
              <TabsTrigger value="rango">Personalizado</TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
        {periodo === "rango" && (
          <div className="flex flex-col gap-2 @sm:flex-row @sm:flex-wrap @sm:items-end @sm:justify-end">
            <div className="space-y-1">
              <Label htmlFor="meta-desde">Desde</Label>
              <Input
                id="meta-desde"
                type="date"
                value={desde}
                min={minimo}
                max={hasta || undefined}
                onChange={(e) => setDesde(e.target.value)}
                aria-invalid={!valido}
                className="@sm:w-44"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="meta-hasta">Hasta</Label>
              <Input
                id="meta-hasta"
                type="date"
                value={hasta}
                min={desde || undefined}
                max={claveDe(hoy())}
                onChange={(e) => setHasta(e.target.value)}
                aria-invalid={!valido}
                className="@sm:w-44"
              />
            </div>
            <p className="text-xs text-muted-foreground @sm:basis-full @sm:text-right">
              {!valido
                ? desde && hasta
                  ? "«Hasta» no puede ser anterior a «Desde»."
                  : "Elige las dos fechas."
                : "Meta guarda las estadísticas de los últimos 365 días."}
            </p>
          </div>
        )}
      </CardHeader>
      {!valido ? null : error && !m ? (
        <CardContent>
          <p className="text-sm text-destructive-text">No se pudieron cargar los datos de Meta: {error}</p>
        </CardContent>
      ) : !m ? (
        <CardContent>
          <Skeleton className="h-64 w-full" />
        </CardContent>
      ) : (
        <ContenidoMetricasMeta data={m} periodo={periodo} />
      )}
    </Card>
  )
}

function ContenidoMetricasMeta({ data, periodo }: { data: MetricasMeta; periodo: PeriodoMeta }) {
  const { numero, cuenta, uso, costos } = data
  const moneda = cuenta.moneda ?? ""
  const dinero = (n: number) => `${n.toLocaleString("es-CO", { maximumFractionDigits: 2 })} ${moneda}`.trim()
  const gratis = costos.detalle.filter((f) => f.costo === 0).reduce((s, f) => s + f.mensajes, 0)
  const cobrados = costos.detalle.reduce((s, f) => s + f.mensajes, 0) - gratis
  const serie = serieEnviados(uso.por_dia, data.periodo)
  const ejeCada = Math.max(1, Math.ceil(serie.puntos.length / 7))
  const plantillas = data.plantillas.filter((p) => !esPlantillaDeEjemplo(p.name))
  const porcentajeEntrega = uso.enviados ? Math.round((uso.entregados / uso.enviados) * 100) : null
  const rango = `${ETIQUETA_FECHA.format(new Date(`${data.periodo.desde}T00:00:00Z`))} al ${ETIQUETA_FECHA.format(new Date(`${data.periodo.hasta}T00:00:00Z`))}`

  return (
    <CardContent className="space-y-6 text-sm">
      <p className="text-xs text-muted-foreground">
        {periodo === "rango" ? "Del" : `${ETIQUETA_PERIODO_META[periodo]}:`} {rango}
      </p>
      <section className="space-y-3">
        <h3 className="font-medium">Gastos</h3>
        {/* Tres columnas desde el móvil: en dos quedaba una celda vacía. */}
        <Cifras className="grid-cols-3">
          <Cifra valor={dinero(costos.total)} etiqueta="Costo total" detalle={moneda ? undefined : "sin moneda: falta método de pago"} />
          <Cifra valor={gratis} etiqueta="Gratis" />
          <Cifra valor={cobrados} etiqueta="Con tarifa" />
        </Cifras>
        <Subtitulo>Mensajes por tipo de cobro</Subtitulo>
        <BarrasH
          titulo="Mensajes por tipo de cobro"
          datos={costos.detalle.map((f) => ({ etiqueta: etiquetaCobro(f.categoria, f.tipo), total: f.mensajes }))}
        />
        {costos.detalle.some((f) => f.costo > 0) && (
          <Datos filas={costos.detalle.filter((f) => f.costo > 0).map((f) => [etiquetaCobro(f.categoria, f.tipo), dinero(f.costo)])} />
        )}
        <p className="max-w-prose text-xs text-muted-foreground">
          Las respuestas dentro de la ventana de 24 h son gratis. Fuera de ella solo salen plantillas aprobadas, que
          Meta cobra según la categoría y el país.
        </p>
      </section>

      <section className="space-y-3">
        <h3 className="font-medium">Mensajes enviados</h3>
        <Cifras>
          <Cifra valor={uso.enviados} etiqueta="Enviados" />
          <Cifra
            valor={uso.entregados}
            etiqueta="Entregados"
            detalle={porcentajeEntrega !== null ? `${porcentajeEntrega} % de los enviados` : undefined}
          />
        </Cifras>
        <Subtitulo>{serie.porMes ? "Por mes" : "Por día"}</Subtitulo>
        <Columnas
          titulo={serie.porMes ? "Mensajes enviados por mes" : "Mensajes enviados por día"}
          unidad={mensajes}
          ejeCada={ejeCada}
          ejeAngosto={ejeCada * 2}
          desdeElFinal
          datos={serie.puntos}
        />
      </section>

      <section className="space-y-2">
        <h3 className="font-medium">Ventanas de 24 h abiertas</h3>
        {data.ventanas_abiertas.length === 0 ? (
          <Vacio>Nadie le ha escrito al bot en las últimas 24 h.</Vacio>
        ) : (
          <div className="max-w-md">
            <Datos
              filas={data.ventanas_abiertas.map((v) => [`+${v.numero}`, `cierra en ${tiempoRestante(v.segundos_restantes)}`])}
            />
          </div>
        )}
      </section>

      <section className="space-y-3">
        <h3 className="font-medium">Número y cuenta</h3>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 @2xl:grid-cols-3 @5xl:grid-cols-4">
          <Dato etiqueta="Número" valor={numero.telefono ?? "—"} />
          <Dato etiqueta="Nombre verificado" valor={numero.nombre_verificado ?? "—"} />
          <Dato etiqueta="Estado" valor={tr(numero.estado)} />
          <Dato etiqueta="Calidad" valor={tr(numero.calidad)} />
          <Dato etiqueta="Modo" valor={tr(numero.modo)} />
          <Dato etiqueta="Límite de mensajes" valor={limiteDiario(numero.limite_diario)} />
          <Dato etiqueta="Velocidad de envío" valor={tr(numero.rendimiento)} />
          <Dato etiqueta="Cuenta de WhatsApp" valor={cuenta.nombre ?? "—"} />
          <Dato etiqueta="Revisión de la cuenta" valor={tr(cuenta.revision)} />
          <Dato etiqueta="Verificación del negocio" valor={tr(cuenta.verificacion_negocio)} />
          <Dato etiqueta="Puede iniciar conversaciones" valor={tr(cuenta.puede_iniciar)} />
        </dl>
        {cuenta.bloqueos.length > 0 && (
          <div className="space-y-1.5">
            <ul className="space-y-1 text-warning-text">
              {cuenta.bloqueos.map((b) => (
                <li key={b} className="flex gap-1.5">
                  <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
                  {b}
                </li>
              ))}
            </ul>
            <a
              href="https://business.facebook.com/settings/whatsapp-business-accounts"
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-xs text-muted-foreground underline underline-offset-4 hover:text-foreground"
            >
              Resolverlo en la configuración del negocio de Meta
              <ExternalLink className="size-3" aria-hidden />
            </a>
          </div>
        )}
      </section>

      <section className="space-y-2">
        <h3 className="font-medium">Plantillas</h3>
        <ul className="space-y-1.5">
          {plantillas.map((p) => (
            <li key={`${p.name}-${p.language}`} className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
              <span className="font-mono text-xs">{p.name}</span>
              <span className="text-muted-foreground">
                {tr(p.category)} · {idioma(p.language)}
              </span>
              <Badge
                variant={p.status === "APPROVED" ? "success" : p.status === "PENDING" ? "warning" : "destructive"}
              >
                {tr(p.status)}
              </Badge>
            </li>
          ))}
        </ul>
      </section>
    </CardContent>
  )
}

export function EstadoServiciosPanel() {
  const { data, error, loading } = usePolling(fetchStatus, 10000)

  return (
    <div className="space-y-3">
      <h1 className="text-xl font-semibold tracking-tight">Estado de los servicios</h1>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-3">
        {loading && !data
          ? Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-24 w-full" />
            ))
          : data &&
            Object.keys(NOMBRES).map((clave) => (
              <ServicioCard
                key={clave}
                clave={clave}
                servicio={data[clave as keyof typeof data] as EstadoServicio}
              />
            ))}
      </div>
      {error && <p className="text-sm text-destructive-text">No se pudo consultar el estado.</p>}
      {data && (
        <p className="text-xs text-muted-foreground">
          Última actualización: {formatearHoraLACS(data.chequeado_en)}
        </p>
      )}
    </div>
  )
}

export function QrWhatsappPanel() {
  const { data, error } = usePolling(fetchQr, 5000)

  let texto = "Cargando…"
  if (error) {
    texto = "No se pudo consultar el estado de WhatsApp."
  } else if (data) {
    if (data.conectado) {
      texto = data.numero ? `Vinculado al número ${data.numero}` : "Vinculado"
    } else if (data.qr_imagen) {
      texto = "Escanea este código con WhatsApp → Dispositivos vinculados:"
    } else {
      texto = "Esperando código QR de Baileys…"
    }
  }

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold tracking-tight">WhatsApp</h1>
      <CanalWhatsappCard />
      {/* La sesión de Baileys se muestra siempre: sigue vinculada aunque el canal activo sea Meta. */}
      <Card>
        <CardHeader>
          <CardTitle>Sesión de Baileys</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 text-sm">
          <p className="flex items-center gap-1.5 text-muted-foreground">
            {data?.conectado && <CircleCheck className="size-4 text-success-text" aria-hidden />}
            {texto}
          </p>
          {data && !data.conectado && data.qr_imagen && (
            <img
              src={data.qr_imagen}
              alt="Código QR de vinculación"
              className="mx-auto max-w-[260px] rounded-lg border"
            />
          )}
        </CardContent>
      </Card>
    </div>
  )
}
