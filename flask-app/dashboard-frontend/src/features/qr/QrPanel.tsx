import { usePolling } from "@/hooks/use-polling"
import { fetchQr } from "@/lib/api"

export function QrPanel() {
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
