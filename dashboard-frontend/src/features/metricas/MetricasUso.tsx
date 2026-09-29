import { useEffect, type ReactNode } from "react"

import { GraduationCap, User, type LucideIcon } from "lucide-react"

import { Card, CardContent } from "@/components/ui/card"
import { usePolling } from "@/hooks/use-polling"
import { fetchMetricasUso, type FiltroMetricas, type MetricasUso as Datos } from "@/lib/api"
import { BarrasH, Cifra, Cifras, filtroValido, Persona, respuestaDe, Subtitulo, Vacio } from "./MetricasPanel"

type Seccion = Datos["asignatura"] | Datos["particular"]

const plural = (n: number, uno: string, varios: string) => `${n} ${n === 1 ? uno : varios}`

/**
 * Una de las dos secciones (asignatura / particular). Van en tarjetas
 * separadas, cada una con su icono y su explicación, para que no se lean
 * como una sola lista.
 */
function Bloque<S extends Seccion>({
  icono: Icono,
  titulo,
  descripcion,
  datos,
  claves,
  rankingTitulo,
  nombre,
  extra,
  vacio,
}: {
  icono: LucideIcon
  titulo: string
  descripcion: string
  datos: S
  claves: string
  rankingTitulo: string
  nombre: (fila: S["top"][number]) => ReactNode
  extra?: (fila: S["top"][number]) => string
  vacio: string
}) {
  return (
    <Card>
      <CardContent className="space-y-4">
        <div className="flex items-start gap-3">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground" aria-hidden>
            <Icono className="size-4" />
          </span>
          <div className="space-y-0.5">
            <h2 className="text-sm font-semibold">{titulo}</h2>
            <p className="text-xs text-muted-foreground">{descripcion}</p>
          </div>
        </div>

        {datos.equipos === 0 ? (
          <Vacio>{vacio}</Vacio>
        ) : (
          <>
            <Cifras className="rounded-md @2xl:grid-cols-4">
              <Cifra valor={datos.reservas} etiqueta="Reservas" />
              <Cifra valor={datos.equipos} etiqueta="Equipos pedidos" />
              <Cifra valor={datos.prestamos} etiqueta="Equipos prestados" />
              <Cifra valor={datos.claves} etiqueta={claves} />
            </Cifras>

            <div className="grid gap-6 @4xl:grid-cols-[3fr_2fr]">
              <div className="space-y-2">
                <Subtitulo>{rankingTitulo}</Subtitulo>
                <ol className="divide-y rounded-md border">
                  {datos.top.map((f, idx) => (
                    <li key={idx} className="grid grid-cols-[1.5rem_1fr_auto] gap-x-3 gap-y-1 px-3 py-2.5 text-sm">
                      <span className="row-span-2 pt-px text-xs text-muted-foreground tabular-nums">{idx + 1}</span>
                      <div className="min-w-0 font-medium">{nombre(f)}</div>
                      <div className="text-right tabular-nums">
                        <div className="font-medium">{plural(f.equipos, "equipo", "equipos")}</div>
                        <div className="text-xs whitespace-nowrap text-muted-foreground">
                          {[plural(f.reservas, "reserva", "reservas"), extra?.(f)].filter(Boolean).join(" · ")}
                        </div>
                      </div>
                      {/* Los equipos que más pide esta fila: la pregunta de "qué pide cada NRC". */}
                      <div className="col-span-2 text-xs text-muted-foreground">
                        {f.equipos_top.map((e) => `${e.nombre} ×${e.total}`).join(" · ")}
                      </div>
                    </li>
                  ))}
                </ol>
              </div>
              <div className="space-y-2">
                <Subtitulo>Equipos más pedidos</Subtitulo>
                <BarrasH titulo={`Equipos más pedidos · ${titulo}`} datos={datos.equipos_top.map((e) => ({ etiqueta: e.nombre, total: e.total }))} />
              </div>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

export function MetricasUso({ filtro }: { filtro: FiltroMetricas }) {
  const { data, error, reload } = usePolling(
    () => (filtroValido(filtro) ? fetchMetricasUso(filtro) : Promise.resolve(null)),
    60000,
  )

  // usePolling solo recarga por intervalo: al cambiar de filtro se pide de nuevo.
  useEffect(() => {
    reload()
  }, [filtro.periodo, filtro.desde, filtro.hasta, reload])

  if (error && !data) return <p className="text-sm text-destructive-text">No se pudieron cargar las métricas por uso.</p>
  const m = data && filtroValido(filtro) && respuestaDe(data, filtro) ? data : null
  if (!m) return null

  return (
    <div className="space-y-4">
      <Bloque
        icono={GraduationCap}
        titulo="Para asignaturas (con NRC)"
        descripcion="Reservas marcadas para una asignatura. Cuenta cada equipo pedido, en cualquier estado."
        datos={m.asignatura}
        claves="NRC distintos"
        rankingTitulo="NRC que más equipos piden"
        nombre={(f) => `NRC ${f.nrc}`}
        extra={(f) => plural(f.usuarios, "estudiante", "estudiantes")}
        vacio="Ninguna reserva de asignatura pidió equipos en este periodo."
      />
      <Bloque
        icono={User}
        titulo="Particulares (sin NRC)"
        descripcion="Reservas de uso personal. Cuenta cada equipo pedido, en cualquier estado."
        datos={m.particular}
        claves="Estudiantes distintos"
        rankingTitulo="Estudiantes que más equipos piden"
        nombre={(f) => <Persona nombre={f.nombre} telefonoWa={f.telefono_wa} />}
        vacio="Ninguna reserva particular pidió equipos en este periodo."
      />
    </div>
  )
}
