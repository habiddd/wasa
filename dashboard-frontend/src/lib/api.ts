const BASE = "/dashboard/api"

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    ...init,
  })
  const data = await resp.json().catch(() => null)
  if (!resp.ok) {
    throw new Error((data && data.error) || `HTTP ${resp.status}`)
  }
  return data as T
}

// --- Servicios ---------------------------------------------------------

export type EstadoServicio = {
  ok: boolean
  detalle?: string
  whatsapp_conectado?: boolean
  numero?: string | null
  buffer_atascado?: boolean
}

export type EstadoStatus = {
  postgres: EstadoServicio
  redis: EstadoServicio
  baileys: EstadoServicio
  llm: EstadoServicio
  flask: EstadoServicio
  chequeado_en: string
}

export function fetchStatus() {
  return request<EstadoStatus>("/status")
}

// --- QR ------------------------------------------------------------------

export type EstadoQr = {
  conectado: boolean
  numero: string | null
  qr_imagen: string | null
}

export function fetchQr() {
  return request<EstadoQr>("/qr")
}

// --- Métricas --------------------------------------------------------------

export type Interaccion = {
  telefono_wa: string | null
  intent: string | null
  permitido: boolean
  motivo_bloqueo: string | null
  estado_envio: string
  creado_en: string | null
}

export type Metricas = {
  reservas_por_dia: { dia: string; total: number }[]
  total_reservas_30d: number
  total_bloqueadas_30d: number
  ultimas_interacciones: Interaccion[]
}

export function fetchMetricas() {
  return request<Metricas>("/metricas")
}

// --- Usuarios --------------------------------------------------------------

export type Rol = "estudiante" | "staff" | "admin"

export type Usuario = {
  id: number
  nombre: string | null
  telefono_wa: string
  codigo_uninorte: string | null
  cedula: string | null
  rol: Rol
  activo: boolean
  creado_en: string | null
}

export type CambiosUsuario = Partial<
  Pick<
    Usuario,
    "nombre" | "telefono_wa" | "codigo_uninorte" | "cedula" | "rol" | "activo"
  >
>

export function fetchUsuarios() {
  return request<Usuario[]>("/usuarios")
}

export function actualizarUsuario(id: number, cambios: CambiosUsuario) {
  return request<Usuario>(`/usuarios/${id}`, {
    method: "PATCH",
    body: JSON.stringify(cambios),
  })
}

// --- Sesiones (reservas) -----------------------------------------------

// "pendiente": el activo requiere confirmación y la CPA todavía no la da.
// Una reserva con activos confirmados y pendientes a la vez es "parcial"
// (ver calendario-utils, EventoCalendario.parcial). "denegada": la CPA no
// aprobó ese activo y quedó libre.
export type EstadoReserva =
  | "confirmada"
  | "pendiente"
  | "denegada"
  | "cancelada"
  | "completada"
  | "no_show"

// Toda reserva es particular o para una asignatura; en ese caso lleva el
// NRC (identificador de la asignatura que cursa el estudiante).
export type TipoUso = "particular" | "asignatura"

// Independiente de TipoUso: un grupo puede ser particular o de asignatura.
export type Modalidad = "individual" | "grupo"

export type Sesion = {
  id: number
  usuario_id: number
  usuario_nombre: string | null
  usuario_telefono: string
  usuario_codigo_uninorte: string | null
  usuario_rol: Rol
  recurso_id: number
  recurso_nombre: string | null
  recurso_tipo: TipoRecurso | null
  // Reservas creadas juntas en el mismo carrito comparten grupo (ej. una
  // cámara y sus lentes). NULL en las anteriores al carrito.
  grupo_id: number | null
  // La reserva es particular o de una asignatura, y `nrc` es el de la
  // asignatura (null si es particular).
  tipo_uso: TipoUso
  nrc: string | null
  // La reserva es individual o de un grupo de personas. `tamano_grupo`
  // INCLUYE a quien reserva (null si es individual); `integrantes` son sus
  // códigos uninorte en el orden en que se capturaron -- integrantes[0] es
  // siempre quien reserva.
  modalidad: Modalidad
  tamano_grupo: number | null
  integrantes: string[]
  inicio: string | null
  fin: string | null
  estado: EstadoReserva
  canal_origen: string
  creado_en: string | null
}

export function fetchSesiones() {
  return request<Sesion[]>("/reservas")
}

// El calendario pide por rango en vez de las últimas 500 por fecha de
// creación: así un mes que se navega hacia atrás llega completo. El backend
// devuelve las reservas que *solapan* el rango, incluidas las de varios días
// que empezaron antes.
// Lo que el calendario puede editar de una reserva. El usuario y el grupo no
// se tocan: reasignarlos sería crear otra reserva, no editar esta.
export type CambiosReserva = {
  inicio?: string
  fin?: string
  estado?: EstadoReserva
  recurso_id?: number
}

export function actualizarReserva(id: number, cambios: CambiosReserva) {
  return request<Sesion>(`/reservas/${id}`, {
    method: "PATCH",
    body: JSON.stringify(cambios),
  })
}

export function actualizarReservaGrupo(
  grupoId: number,
  cambios: CambiosReserva & {
    recurso_ids?: number[]
    tipo_uso?: TipoUso
    nrc?: string | null
    modalidad?: Modalidad
    tamano_grupo?: number | null
    integrantes?: string[]
  }
) {
  return request<Sesion[]>(`/reservas/grupo/${grupoId}`, {
    method: "PATCH",
    body: JSON.stringify(cambios),
  })
}

export function eliminarReservaGrupo(grupoId: number) {
  return request<void>(`/reservas/grupo/${grupoId}`, { method: "DELETE" })
}

// La CPA aprueba los activos pendientes y el backend le avisa al estudiante
// por WhatsApp. `notificado` es false si el mensaje no salió: la reserva
// queda confirmada igual.
export function confirmarReservaGrupo(grupoId: number) {
  return request<{ reservas: Sesion[]; notificado: boolean }>(
    `/reservas/grupo/${grupoId}/confirmar`,
    { method: "POST" }
  )
}

// Confirma o deniega un solo activo pendiente. El backend le avisa al
// estudiante qué se decidió y cómo quedó el resto de su reserva.
export function decidirReserva(id: number, decision: "confirmar" | "denegar") {
  return request<{ reservas: Sesion[]; notificado: boolean }>(`/reservas/${id}/${decision}`, {
    method: "POST",
  })
}

// Una reserva hecha para varios activos se edita como un bloque: el backend
// aplica el cambio a todas sus líneas en una sola transacción o a ninguna.
export function actualizarReservasBloque(
  ids: number[],
  cambios: Omit<CambiosReserva, "recurso_id">
) {
  return request<Sesion[]>("/reservas/bloque", {
    method: "PATCH",
    body: JSON.stringify({ ids, ...cambios }),
  })
}

export function fetchReservasRango(desde: Date, hasta: Date) {
  const params = new URLSearchParams({
    desde: desde.toISOString(),
    hasta: hasta.toISOString(),
  })
  return request<Sesion[]>(`/reservas?${params}`)
}

// --- Activos -----------------------------------------------------------

export type TipoRecurso = "equipo" | "espacio"
export type EstadoRecurso = "disponible" | "en_mantenimiento" | "no_disponible"

export type Recurso = {
  id: number
  tipo: TipoRecurso
  nombre: string
  descripcion: string | null
  ubicacion: string | null
  placa: string | null
  padre_id: number | null
  // Lugar/espacio donde vive el activo -- jerarquía distinta e
  // independiente de padre_id (ver flask-app/app/models.py).
  espacio_id: number | null
  categoria: string | null
  subcategoria: string | null
  marca: string | null
  modelo: string | null
  identificador: string | null
  requiere_confirmacion: boolean
  estado: EstadoRecurso
  creado_en: string | null
}

export type CambiosActivo = Partial<
  Pick<
    Recurso,
    | "tipo"
    | "nombre"
    | "descripcion"
    | "ubicacion"
    | "placa"
    | "categoria"
    | "subcategoria"
    | "marca"
    | "modelo"
    | "identificador"
    | "requiere_confirmacion"
    | "estado"
    | "padre_id"
    | "espacio_id"
  >
>

export function fetchActivos() {
  return request<Recurso[]>("/activos")
}

export function crearActivo(cambios: CambiosActivo) {
  return request<Recurso>("/activos", {
    method: "POST",
    body: JSON.stringify(cambios),
  })
}

// `propagar_espacio` no es un campo de Recurso: es una opción de la
// petición que le dice al backend si debe arrastrar a los hijos (por
// padre_id) que estaban en el mismo lugar (ver dashboard.py). El backend
// puede devolver `hijos_actualizados` cuando hubo propagación, solo para
// que el toast de confirmación diga cuántos se movieron.
export function actualizarActivo(
  id: number,
  cambios: CambiosActivo & { propagar_espacio?: boolean }
) {
  return request<Recurso & { hijos_actualizados?: number }>(`/activos/${id}`, {
    method: "PATCH",
    body: JSON.stringify(cambios),
  })
}
