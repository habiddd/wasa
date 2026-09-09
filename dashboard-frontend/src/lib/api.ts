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

export type EstadoReserva = "confirmada" | "cancelada" | "completada" | "no_show"

export type Sesion = {
  id: number
  usuario_id: number
  usuario_nombre: string | null
  usuario_telefono: string
  usuario_rol: Rol
  recurso_id: number
  recurso_nombre: string | null
  inicio: string | null
  fin: string | null
  estado: EstadoReserva
  canal_origen: string
  creado_en: string | null
}

export function fetchSesiones() {
  return request<Sesion[]>("/reservas")
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
  categoria: string | null
  marca: string | null
  modelo: string | null
  identificador: string | null
  requiere_seguro: boolean
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
    | "marca"
    | "modelo"
    | "identificador"
    | "requiere_seguro"
    | "estado"
    | "padre_id"
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

export function actualizarActivo(id: number, cambios: CambiosActivo) {
  return request<Recurso>(`/activos/${id}`, {
    method: "PATCH",
    body: JSON.stringify(cambios),
  })
}
