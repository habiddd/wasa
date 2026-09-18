import { AlertTriangle } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { usePolling } from "@/hooks/use-polling"
import { fetchQr, fetchStatus, type EstadoServicio } from "@/lib/api"
import { formatearHoraLACS } from "@/lib/fechas"

const NOMBRES: Record<string, string> = {
  postgres: "Postgres",
  redis: "Redis",
  baileys: "Baileys / WhatsApp",
  llm: "LLM local",
  flask: "Flask",
}

function EstadoBadge({ ok }: { ok: boolean }) {
  return (
    <Badge variant={ok ? "success" : "destructive"}>
      {ok ? "OK" : "FALLA"}
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
        <p className="break-words">{servicio.detalle || ""}</p>
        {clave === "baileys" && servicio.numero && <p>Número: {servicio.numero}</p>}
        {clave === "baileys" && servicio.buffer_atascado && (
          <p className="flex items-center gap-1 text-warning">
            <AlertTriangle className="size-3.5" />
            Buffer de eventos atascado (reintentando solo)
          </p>
        )}
      </CardContent>
    </Card>
  )
}

export function EstadoServiciosPanel() {
  const { data, error, loading } = usePolling(fetchStatus, 10000)

  return (
    <div className="space-y-3">
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
      {error && <p className="text-sm text-destructive">No se pudo consultar el estado.</p>}
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
      texto = "✅ WhatsApp conectado" + (data.numero ? ` (número ${data.numero})` : "")
    } else if (data.qr_imagen) {
      texto = "Escanea este código con WhatsApp → Dispositivos vinculados:"
    } else {
      texto = "Esperando código QR de Baileys…"
    }
  }

  return (
    <div className="text-center">
      <p className="text-muted-foreground">{texto}</p>
      {data && !data.conectado && data.qr_imagen && (
        <img
          src={data.qr_imagen}
          alt="Código QR de vinculación"
          className="mx-auto mt-4 max-w-[260px] rounded-lg border"
        />
      )}
    </div>
  )
}
