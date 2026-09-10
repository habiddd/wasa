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
import { fetchMetricas } from "@/lib/api"
import { formatearFechaHoraCPA } from "@/lib/fechas"

const ESTADO_ENVIO_INFO: Record<
  string,
  { variant: "success" | "destructive" | "warning" | "secondary"; texto: string }
> = {
  "n/a": { variant: "secondary", texto: "—" },
  error_http: { variant: "destructive", texto: "Error de envío" },
  pendiente: { variant: "warning", texto: "Pendiente" },
  enviado: { variant: "warning", texto: "Enviado" },
  entregado: { variant: "success", texto: "Entregado" },
  leido: { variant: "success", texto: "Leído" },
  reproducido: { variant: "success", texto: "Reproducido" },
  fallido: { variant: "destructive", texto: "Fallido" },
}

function PillEstadoEnvio({ estado }: { estado: string }) {
  const info = ESTADO_ENVIO_INFO[estado] ?? { variant: "secondary" as const, texto: estado || "—" }
  return <Badge variant={info.variant}>{info.texto}</Badge>
}

export function MetricasPanel() {
  const { data, error } = usePolling(fetchMetricas, 30000)

  if (error && !data) {
    return <p className="text-sm text-destructive">No se pudieron cargar las métricas.</p>
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-3">
        <Card className="min-w-[140px]">
          <CardContent className="py-2">
            <div className="text-2xl font-bold">{data?.total_reservas_30d ?? "—"}</div>
            <div className="text-xs text-muted-foreground">Reservas (30d)</div>
          </CardContent>
        </Card>
        <Card className="min-w-[140px]">
          <CardContent className="py-2">
            <div className="text-2xl font-bold">{data?.total_bloqueadas_30d ?? "—"}</div>
            <div className="text-xs text-muted-foreground">
              Bloqueadas por guardrails (30d)
            </div>
          </CardContent>
        </Card>
      </div>

      <div>
        <h3 className="mb-2 text-sm font-semibold">Últimas interacciones del bot</h3>
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Fecha</TableHead>
                <TableHead>Teléfono</TableHead>
                <TableHead>Intent</TableHead>
                <TableHead>Permitido</TableHead>
                <TableHead>Motivo bloqueo</TableHead>
                <TableHead>Entrega</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data?.ultimas_interacciones.map((i, idx) => (
                <TableRow key={idx}>
                  <TableCell>{formatearFechaHoraCPA(i.creado_en)}</TableCell>
                  <TableCell>{i.telefono_wa || "-"}</TableCell>
                  <TableCell>{i.intent || "-"}</TableCell>
                  <TableCell>{i.permitido ? "sí" : "no"}</TableCell>
                  <TableCell>{i.motivo_bloqueo || "-"}</TableCell>
                  <TableCell>
                    <PillEstadoEnvio estado={i.estado_envio} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </div>
    </div>
  )
}
