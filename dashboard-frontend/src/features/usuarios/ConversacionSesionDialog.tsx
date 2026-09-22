import { useEffect, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Skeleton } from "@/components/ui/skeleton"
import { EstadoReservaBadge } from "@/features/calendario/EstadoBadge"
import {
  fetchConversacionSesion,
  type ConversacionSesion,
  type MensajeConversacion,
  type Sesion,
} from "@/lib/api"
import { formatearFechaHoraLACS, formatearHoraLACS } from "@/lib/fechas"

function usoDe(s: Sesion) {
  return s.tipo_uso === "asignatura" ? `NRC ${s.nrc}` : "Particular"
}

/** "10/09/2026" en hora de la LACS, para el separador de día entre burbujas. */
function fechaCivil(valor: string | null) {
  if (!valor) return ""
  return formatearFechaHoraLACS(valor).split(",")[0]
}

/**
 * Una burbuja del hilo. `alineacion` decide el lado (usuario a la izquierda,
 * bot a la derecha) -- se omite por completo si el campo llegó vacío, que
 * pasa cuando el bot no llegó a responder (ej. mensaje bloqueado) o cuando
 * la fila es solo la respuesta a algo que ya no se guardó.
 */
function Burbuja({
  texto,
  hora,
  alineacion,
  bloqueado,
  motivoBloqueo,
}: {
  texto: string
  hora: string
  alineacion: "usuario" | "bot"
  bloqueado?: boolean
  motivoBloqueo?: string | null
}) {
  return (
    <div className={alineacion === "bot" ? "ml-auto max-w-[85%] space-y-1" : "max-w-[85%] space-y-1"}>
      <div className="flex items-center gap-1.5">
        <div
          className={
            alineacion === "bot"
              ? "rounded-lg bg-primary/10 px-3 py-1.5 text-sm"
              : "rounded-lg bg-muted px-3 py-1.5 text-sm"
          }
        >
          {texto}
        </div>
        {bloqueado && <Badge variant="destructive">{motivoBloqueo || "Bloqueado"}</Badge>}
      </div>
      <p className={alineacion === "bot" ? "text-right text-xs text-muted-foreground" : "text-xs text-muted-foreground"}>
        {hora}
      </p>
    </div>
  )
}

function FilaMensaje({
  mensaje,
  mostrarSeparadorFecha,
}: {
  mensaje: MensajeConversacion
  mostrarSeparadorFecha: boolean
}) {
  const hora = formatearHoraLACS(mensaje.creado_en)
  return (
    <div className="space-y-2">
      {mostrarSeparadorFecha && (
        <div className="flex items-center gap-2 py-1">
          <div className="h-px flex-1 bg-border" />
          <span className="text-xs text-muted-foreground">{fechaCivil(mensaje.creado_en)}</span>
          <div className="h-px flex-1 bg-border" />
        </div>
      )}
      <div className="space-y-1.5">
        {mensaje.entrante && (
          <Burbuja
            texto={mensaje.entrante}
            hora={hora}
            alineacion="usuario"
            bloqueado={!mensaje.permitido}
            motivoBloqueo={mensaje.motivo_bloqueo}
          />
        )}
        {mensaje.respuesta && <Burbuja texto={mensaje.respuesta} hora={hora} alineacion="bot" />}
        {mensaje.intent && (
          <p className="text-xs text-muted-foreground">intent: {mensaje.intent}</p>
        )}
      </div>
      {mensaje.es_ancla && (
        <div className="flex items-center gap-2 py-1">
          <div className="h-px flex-1 bg-border" />
          <span className="text-xs text-muted-foreground">Aquí se creó la reserva</span>
          <div className="h-px flex-1 bg-border" />
        </div>
      )}
    </div>
  )
}

/**
 * Modal de solo lectura con el hilo de WhatsApp que terminó en una sesión
 * (reserva). Se abre desde una fila de SesionesTab -- para editar la reserva
 * ya está ReservaDetalle en el calendario, este componente no toca nada.
 *
 * Carga perezosa: SesionesTab solo monta este componente mientras hay una
 * sesión seleccionada, así que el `useEffect` de acá abajo pide la
 * conversación al abrir y se desmonta (sin dejar nada pendiente) al cerrar.
 * El listado de sesiones nunca pide este endpoint.
 */
export function ConversacionSesionDialog({
  sesion,
  onClose,
}: {
  sesion: Sesion
  onClose: () => void
}) {
  const [datos, setDatos] = useState<ConversacionSesion | null>(null)
  const [error, setError] = useState(false)
  const [cargando, setCargando] = useState(true)
  // Se incrementa en "Reintentar" para volver a disparar el efecto sin
  // depender de que cambie `sesion.id` (la misma sesión, otro intento).
  const [intento, setIntento] = useState(0)

  useEffect(() => {
    let cancelado = false
    setCargando(true)
    setError(false)
    fetchConversacionSesion(sesion.id)
      .then((res) => {
        if (!cancelado) setDatos(res)
      })
      .catch(() => {
        if (!cancelado) setError(true)
      })
      .finally(() => {
        if (!cancelado) setCargando(false)
      })
    return () => {
      cancelado = true
    }
  }, [sesion.id, intento])

  // Separador de fecha civil: solo cuando el día cambia respecto al mensaje
  // anterior (el primero siempre lo lleva, para ubicar el inicio del hilo).
  const mensajesConSeparador =
    datos?.mensajes.map((m, i) => ({
      mensaje: m,
      separador: i === 0 || fechaCivil(m.creado_en) !== fechaCivil(datos.mensajes[i - 1].creado_en),
    })) ?? []

  return (
    <Dialog open onOpenChange={(abierto) => { if (!abierto) onClose() }}>
      <DialogContent className="flex max-h-[85vh] flex-col sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {sesion.usuario_nombre || sesion.usuario_telefono}
            <EstadoReservaBadge estado={sesion.estado} />
          </DialogTitle>
          <DialogDescription>
            {sesion.recurso_nombre || "-"} · {usoDe(sesion)} ·{" "}
            {formatearFechaHoraLACS(sesion.inicio)} – {formatearFechaHoraLACS(sesion.fin)}
          </DialogDescription>
        </DialogHeader>

        {datos?.truncada && (
          <p className="rounded-md border border-warning/30 bg-warning/10 px-3 py-1.5 text-xs text-warning">
            Conversación recortada a los 100 mensajes más cercanos a la reserva.
          </p>
        )}

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto">
          {cargando && (
            <div className="space-y-3">
              <Skeleton className="h-12 w-2/3" />
              <Skeleton className="ml-auto h-12 w-2/3" />
              <Skeleton className="h-12 w-2/3" />
              <Skeleton className="ml-auto h-12 w-2/3" />
            </div>
          )}

          {!cargando && error && (
            <div className="flex flex-col items-center gap-2 py-8 text-center">
              <p className="text-sm text-destructive">No se pudo cargar la conversación.</p>
              <Button size="sm" variant="outline" onClick={() => setIntento((n) => n + 1)}>
                Reintentar
              </Button>
            </div>
          )}

          {!cargando && !error && datos?.motivo === "sin_conversacion" && (
            <p className="py-8 text-center text-sm text-muted-foreground">
              Esta reserva se creó desde el dashboard, no hay conversación de WhatsApp.
            </p>
          )}

          {!cargando && !error && datos?.motivo === "sin_mensajes" && (
            <p className="py-8 text-center text-sm text-muted-foreground">
              No quedan mensajes guardados de esta conversación.
            </p>
          )}

          {!cargando && !error && datos?.motivo === "ok" && (
            <div className="space-y-2">
              {mensajesConSeparador.map(({ mensaje, separador }) => (
                <FilaMensaje key={mensaje.id} mensaje={mensaje} mostrarSeparadorFecha={separador} />
              ))}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
