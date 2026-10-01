const BASE = "/dashboard/api"

// fetch() solo rechaza cuando no hubo respuesta (servidor caído, red cortada);
// su TypeError dice "Failed to fetch", que no le sirve a nadie en un toast.
async function conectar(url: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init)
  } catch {
    throw new Error("No hay conexión con el servidor. Revisa la red e intenta de nuevo.")
  }
}

// El backend manda `{error}` en español para los errores esperados; esto es
// solo el respaldo cuando no hay cuerpo (un 500, un 502 de nginx, etc.).
function errorHttp(status: number, data: { error?: string } | null): Error {
  if (data?.error) return new Error(data.error)
  if (status === 401 || status === 403) {
    return new Error("La sesión del panel expiró. Recarga la página para volver a entrar.")
  }
  if (status === 404) return new Error("Lo que buscas ya no existe. Recarga para ver los datos al día.")
  if (status >= 500) {
    return new Error(`El servidor falló al procesar el pedido (error ${status}). Intenta de nuevo en un momento.`)
  }
  return new Error(`El servidor rechazó el pedido (error ${status}).`)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await conectar(`${BASE}${path}`, {
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    ...init,
  })
  const data = await resp.json().catch(() => null)
  if (!resp.ok) throw errorHttp(resp.status, data)
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

// --- Canal de WhatsApp (Baileys / Meta) --------------------------------

export type ProveedorWhatsapp = "baileys" | "meta"

export type EstadoProveedores = {
  activo: ProveedorWhatsapp
  disponibles: {
    baileys: { estado: EstadoServicio }
    meta: {
      configurado: boolean
      faltan: string[]
      estado: {
        ok: boolean
        detalle?: string
        numero?: string | null
        nombre_verificado?: string | null
        calidad?: string | null
      } | null
    }
  }
}

export function fetchProveedorWhatsapp() {
  return request<EstadoProveedores>("/whatsapp/proveedor")
}

export function cambiarProveedorWhatsapp(proveedor: ProveedorWhatsapp) {
  return request<{ activo: ProveedorWhatsapp }>("/whatsapp/proveedor", {
    method: "PUT",
    body: JSON.stringify({ proveedor }),
  })
}

export type MetricasMeta = {
  numero: {
    telefono: string | null
    nombre_verificado: string | null
    estado_nombre: string | null
    calidad: string | null
    estado: string | null
    modo: string | null
    limite_diario: string | null
    rendimiento: string | null
  }
  cuenta: {
    nombre: string | null
    moneda: string | null
    revision: string | null
    verificacion_negocio: string | null
    puede_iniciar: string | null
    bloqueos: string[]
  }
  uso: {
    enviados: number
    entregados: number
    por_dia: { dia: number; enviados: number; entregados: number }[]
  }
  costos: {
    total: number
    detalle: { categoria: string; tipo: string; mensajes: number; costo: number }[]
  }
  plantillas: { name: string; status: string; category: string; language: string }[]
  ventanas_abiertas: { numero: string; segundos_restantes: number }[]
  consultado_en: string
  periodo: { desde: string; hasta: string }
}

export type PeriodoMeta = "7" | "30" | "365" | "rango"
export type FiltroMeta = { periodo: PeriodoMeta; desde?: string; hasta?: string }

export function fetchMetricasMeta({ periodo, desde, hasta }: FiltroMeta) {
  const q = new URLSearchParams({ periodo })
  if (periodo === "rango" && desde && hasta) {
    q.set("desde", desde)
    q.set("hasta", hasta)
  }
  return request<MetricasMeta>(`/whatsapp/meta/metricas?${q}`)
}

// --- Métricas --------------------------------------------------------------

export type Periodo = "30" | "90" | "todo" | "rango"

// desde y hasta (YYYY-MM-DD, ambos incluidos) solo cuentan con periodo "rango".
export type FiltroMetricas = { periodo: Periodo; desde?: string; hasta?: string }

// Descargas: un <a href> basta, el navegador ya manda las credenciales del panel.
export const URL_EXCEL_DATOS = `${BASE}/datos/excel`
export function urlExcelMetricas(filtro: FiltroMetricas) {
  return `${BASE}/metricas/excel?${queryMetricas(filtro)}`
}

function queryMetricas({ periodo, desde, hasta }: FiltroMetricas) {
  const q = new URLSearchParams({ periodo })
  if (periodo === "rango" && desde && hasta) {
    q.set("desde", desde)
    q.set("hasta", hasta)
  }
  return q.toString()
}

export type FilaActivo = {
  nombre: string
  reservas: number
  prestamos: number
  denegadas: number
  canceladas: number
  no_show: number
}

export type FilaUsuario = {
  nombre: string | null
  telefono_wa: string | null
  reservas: number
  prestamos: number
  devueltas_tarde: number
  no_show: number
  no_devueltas: number
  retraso_devolucion_seg: number | null
}

export type PuntualidadEvento = { total: number; a_tiempo: number; retraso_seg: number | null }

export type Metricas = {
  periodo: Periodo
  desde: string | null
  hasta: string | null
  inventario: {
    adicionados: { equipo: number; espacio: number }
    dados_de_baja: { periodo: number; hoy: number }
    mantenimiento: { periodo: number; hoy: number }
    total_hoy: Partial<Record<EstadoRecurso, number>>
  }
  seguro: { salen: number; no_salen: number; sin_dato: number; lineas_salen: number }
  resumen: {
    reservas: number
    prestamos: number
    denegadas: number
    canceladas: number
    no_show: number
    devoluciones_problema: number
    no_devueltos_hoy: number
    grupos_agrupados: number
  }
  estados: { estado: string; total: number }[]
  activos: { equipo: FilaActivo[]; espacio: FilaActivo[] }
  usuarios: FilaUsuario[]
  puntualidad: { entrega?: PuntualidadEvento; devolucion?: PuntualidadEvento }
  reservas_por_dia: { dia: string; total: number }[]
  por_dia_semana: number[] // 0 = domingo
  por_hora: number[]
  habitos: {
    duracion_seg: number | null
    anticipacion_seg: number | null
    canales: { canal: string; total: number }[]
  }
  bot: {
    recibidos: number
    bloqueados: number
    intents: { intent: string | null; total: number }[]
    avisos: { tipo: string; enviados: number; fallidos: number }[]
  }
}

export function fetchMetricas(filtro: FiltroMetricas) {
  return request<Metricas>(`/metricas?${queryMetricas(filtro)}`)
}

type EquipoContado = { nombre: string; total: number }

/** Una sección de /metricas/uso; `claves` = NRC distintos (asignatura) o estudiantes distintos (particular). */
type UsoEquipos<Fila> = {
  reservas: number
  equipos: number
  prestamos: number
  claves: number
  equipos_top: EquipoContado[]
  top: (Fila & { reservas: number; equipos: number; prestamos: number; usuarios: number; equipos_top: EquipoContado[] })[]
}

export type MetricasUso = {
  periodo: Periodo
  desde: string | null
  hasta: string | null
  asignatura: UsoEquipos<{ nrc: string }>
  particular: UsoEquipos<{ nombre: string | null; telefono_wa: string | null }>
}

export function fetchMetricasUso(filtro: FiltroMetricas) {
  return request<MetricasUso>(`/metricas/uso?${queryMetricas(filtro)}`)
}

type ReservaCercana = {
  id: number
  inicio: string
  fin: string
  estado: string
  nombre: string | null
  telefono_wa: string | null
}

export type MetricasActivo = {
  periodo: Periodo
  desde: string | null
  hasta: string | null
  ficha: {
    id: number
    nombre: string
    tipo: TipoRecurso
    subcategoria: string | null
    placa: string | null
    estado: EstadoRecurso
    creado_en: string | null
    padre: string | null
    espacio: string | null
  }
  conteos: {
    reservas: number
    prestamos: number
    denegadas: number
    canceladas: number
    no_show: number
    devueltas_tarde: number
    no_devueltas: number
  }
  estados: { estado: string; total: number }[]
  uso: {
    prestado_seg: number
    duracion_seg: number | null
    anticipacion_seg: number | null
    usuarios: number
  }
  usuarios: (Pick<FilaUsuario, "nombre" | "telefono_wa" | "reservas" | "prestamos" | "devueltas_tarde">)[]
  puntualidad: Metricas["puntualidad"]
  seguro: { reservas_que_salen: number }
  reservas_por_dia: { dia: string; total: number }[]
  por_dia_semana: number[] // 0 = domingo
  por_hora: number[]
  inventario: {
    historial: {
      cambiado_en: string
      estado_anterior: EstadoRecurso | null
      estado_nuevo: EstadoRecurso | null
      motivo: string | null
      nota: string | null
      reserva_id: number | null
    }[]
    veces_mantenimiento: number
    veces_no_devuelto: number
  }
  ultima_reserva: ReservaCercana | null
  proxima_reserva: ReservaCercana | null
}

export function fetchMetricasActivo(id: number, filtro: FiltroMetricas) {
  return request<MetricasActivo>(`/metricas/activo/${id}?${queryMetricas(filtro)}`)
}

export type TipoEventoTimeline =
  | "alta"
  | "reserva_creada"
  | "reserva_estado"
  | "reserva_horario"
  | "reserva_eliminada"
  | "acta"
  | "activo_estado"

export type EventoTimeline = {
  tipo: TipoEventoTimeline
  fecha: string
  reserva_id: number | null
  grupo_id: number | null
  usuario: string | null
  telefono_wa: string | null
  // De reserva o de activo según el tipo (EstadoReserva o EstadoRecurso).
  estado_anterior: string | null
  estado_nuevo: string | null
  inicio: string | null
  fin: string | null
  detalle: string | null
  // Solo en las actas.
  momento: "entrega" | "devolucion" | null
  origen: "firmada" | "subida" | null
}

export type TimelineActivo = {
  periodo: Periodo
  desde: string | null
  hasta: string | null
  recurso_id: number
  eventos: EventoTimeline[]
  truncado: boolean
}

export function fetchTimelineActivo(id: number, filtro: FiltroMetricas) {
  return request<TimelineActivo>(`/metricas/activo/${id}/timeline?${queryMetricas(filtro)}`)
}

// --- Usuarios --------------------------------------------------------------

export type Rol = "estudiante" | "staff" | "admin"

const ETIQUETA_ROL: Record<Rol, string> = {
  estudiante: "Estudiante",
  staff: "Staff",
  admin: "Administrador",
}

export function etiquetaRol(rol: string | null | undefined): string {
  if (!rol) return "-"
  return ETIQUETA_ROL[rol as Rol] ?? rol
}

export type Usuario = {
  id: number
  nombre: string | null
  telefono_wa: string
  codigo_uninorte: string | null
  cedula: string | null
  rol: Rol
  activo: boolean
  // false = falta nombre, ID uninorte o cédula (registro a medio hacer,
  // sea por WhatsApp o creado desde el dashboard). Ver
  // usuarios_service.preparar_usuario_dashboard.
  registro_completo?: boolean
  creado_en: string | null
}

export type CambiosUsuario = Partial<
  Pick<
    Usuario,
    "nombre" | "telefono_wa" | "codigo_uninorte" | "cedula" | "rol" | "activo"
  >
>

export function fetchUsuarios(opciones?: { incluirIncompletos?: boolean }) {
  const params = opciones?.incluirIncompletos ? "?incluir_incompletos=1" : ""
  return request<Usuario[]>(`/usuarios${params}`)
}

export function actualizarUsuario(id: number, cambios: CambiosUsuario) {
  return request<Usuario>(`/usuarios/${id}`, {
    method: "PATCH",
    body: JSON.stringify(cambios),
  })
}

export function eliminarUsuario(id: number) {
  return request<void>(`/usuarios/${id}`, { method: "DELETE" })
}

// --- Sesiones (reservas) -----------------------------------------------

// "pendiente": el activo requiere confirmación y la LACS todavía no la da.
// Una reserva con activos confirmados y pendientes a la vez es "parcial"
// (ver calendario-utils, EventoCalendario.parcial). "denegada": la LACS no
// aprobó ese activo y quedó libre. "entregada"/"devuelta_tarde": solo se
// llega a ellos registrando el acta correspondiente (ver actas-pdf.ts,
// FirmarActaDialog y BotonActaPdf) -- el selector de estado del dashboard no
// permite ponerlos a mano. "retrasada" NO es un estado: es derivado
// (`entregada` con `fin` ya pasado) y viaja en `Sesion.retrasada`.
export type EstadoReserva =
  | "confirmada"
  | "pendiente"
  | "denegada"
  | "cancelada"
  | "completada"
  | "no_show"
  | "entregada"
  | "devuelta_tarde"
  | "no_devuelta"

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
  // ¿Los activos salen de Barranquilla? true = trámite de seguro (mín. 5
  // días hábiles). null = reserva anterior a la pregunta.
  sale_de_barranquilla: boolean | null
  // Fecha mínima de inicio (YYYY-MM-DD) calculada por el backend desde
  // creado_en, con festivos de Colombia. null si no sale de Barranquilla.
  fecha_minima_seguro: string | null
  // Nota libre y opcional de la reserva completa (no de cada activo). null
  // = sin nota. Máximo 300 caracteres (recursos_service.NOTA_MAX_CARACTERES
  // en el bot, VARCHAR(300) en el esquema).
  nota: string | null
  inicio: string | null
  fin: string | null
  estado: EstadoReserva
  // Derivado por el backend (estado == "entregada" && fin < ahora), no una
  // columna: no hace falta cron ni worker, se calcula al serializar. Ver
  // actas_service.esta_retrasada.
  retrasada: boolean
  // A diferencia de "retrasada", esta SÍ es una columna (Reserva.entrega_tardia):
  // el estudiante recogió después del margen de no-show pero dentro de su
  // ventana, y se le entregó igual (ver actas_service.aplicar_transicion).
  // No es un estado nuevo a propósito, para que sobreviva a la devolución.
  entrega_tardia: boolean
  canal_origen: string
  creado_en: string | null
}

export function fetchSesiones() {
  return request<Sesion[]>("/reservas")
}

// Ventanas del selector temporal del panel de Reservas. El recorte lo hace el
// backend (ver VENTANAS_RESERVAS en dashboard.py): pedir solo la ventana visible
// evita que Flask serialice el histórico entero cada 30 s en cada pestaña abierta.
export type VentanaReservas = "24h" | "7d" | "30d" | "1a" | "todas"

export type ReservasEnVentana = {
  reservas: Sesion[]
  truncado: boolean
  total: number
  ventana: VentanaReservas
}

// `incluirAbiertas`: suma los préstamos que siguen afuera (entregada /
// no_devuelta) aunque su fin quede antes de la ventana -- ver la vista "Hoy".
export function fetchReservasVentana(ventana: VentanaReservas, incluirAbiertas = false) {
  const extra = incluirAbiertas ? "&incluir_abiertas=1" : ""
  return request<ReservasEnVentana>(`/reservas?ventana=${ventana}${extra}`)
}

// La conversación de WhatsApp de una sesión NO viaja dentro de `Sesion`: el
// listado trae hasta 500 filas de golpe y el hilo solo interesa cuando
// alguien abre una en concreto, así que se pide aparte, bajo demanda, desde
// ConversacionSesionDialog.
export type MotivoConversacion = "ok" | "sin_conversacion" | "sin_mensajes"

export type MensajeConversacion = {
  id: number
  entrante: string | null
  respuesta: string | null
  intent: string | null
  permitido: boolean
  motivo_bloqueo: string | null
  estado_envio: string
  creado_en: string | null
  // true solo en el mensaje más cercano al instante en que se creó la
  // reserva (ver heurística del hueco en dashboard.py) -- el frontend pinta
  // ahí el separador "Aquí se creó la reserva".
  es_ancla: boolean
}

export type ConversacionSesion = {
  reserva_id: number
  telefono: string | null
  origen: string
  motivo: MotivoConversacion
  truncada: boolean
  mensajes: MensajeConversacion[]
}

export function fetchConversacionSesion(reservaId: number) {
  return request<ConversacionSesion>(`/reservas/${reservaId}/conversacion`)
}

// Fecha mínima de inicio HOY para una reserva que sale de Barranquilla (5
// días hábiles, festivos de Colombia calculados por el backend) -- la usa
// el formulario de edición en ReservaDetalle.tsx.
export type FechaMinimaSeguro = { dias_habiles: number; hoy: string; fecha_minima: string }

export function fetchFechaMinimaSeguro() {
  return request<FechaMinimaSeguro>("/seguro/fecha-minima")
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
    sale_de_barranquilla?: boolean
    nota?: string | null
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

// El acta de entrega y devolución es un PDF, no JSON -- `request<T>` hace
// `resp.json()` sobre toda respuesta y reventaría con un blob. Este helper
// aparte hace el mismo manejo de errores (intenta leer `{error}` del cuerpo
// para que el toast diga algo útil) pero devuelve el blob si todo salió bien.
export async function descargarActaPdf(grupoId: number): Promise<Blob> {
  const resp = await conectar(`${BASE}/reservas/grupo/${grupoId}/pdf`)
  if (!resp.ok) {
    const data = await resp.json().catch(() => null)
    throw errorHttp(resp.status, data)
  }
  return resp.blob()
}

// --- Actas firmadas ------------------------------------------------------
// El PDF firmado se compone en el navegador con pdf-lib (ver
// features/reservas/actas-pdf.ts): el backend solo valida y guarda. Este
// bloque es el contrato con `/dashboard/api/reservas/grupo/<id>/acta*`.

export type MomentoActa = "entrega" | "devolucion"
export type OrigenActa = "firmada" | "subida"

// Dónde va cada firma dentro del PDF base, en puntos desde la esquina
// inferior izquierda -- mismo sistema de coordenadas que reportlab usó para
// componer el PDF (ver acta_pdf.py, _Ancla) y que pdf-lib usa para leerlo, así
// que no hay que invertir nada al estampar.
export type AnclaFirma = { pagina: number; x: number; y: number; ancho: number; alto: number }

export type ActaRegistrada = {
  id: number
  momento: MomentoActa
  origen: OrigenActa
  bytes: number
  sha256: string
  firmado_por: string | null
  nota: string | null
  creado_en: string | null
}

export type EstadoActa = {
  // null: la reserva ya está cerrada (completada/devuelta tarde/cancelada/
  // etc.) y no queda nada por firmar.
  momento_siguiente: MomentoActa | null
  tiene_acta: boolean
  // entrega_usuario, entrega_admin, devolucion_usuario, devolucion_admin --
  // cada una es la zona de firma (la raya de "Firma" con su etiqueta, usa
  // recorte-acta.ts para el fondo del lienzo y actas-pdf.ts para estampar el
  // trazo), más "..._fecha" y "..._cc": los huecos donde escribir la fecha y
  // la cédula del administrador, YA desplazados más allá de su etiqueta por
  // el backend (ver acta_pdf._Ancla.despl_x) -- el frontend no mide ningún
  // texto, solo dibuja en `ancla.x`. Más "entrega_nota" y "devolucion_nota":
  // el hueco de observaciones libres del administrador (ver
  // reserva_actas.nota). Ya no hay "..._caja": el lienzo volvió a ser solo
  // la franja de firma, no el cuadro completo. La cédula del USUARIO no
  // viaja acá -- la imprime el servidor directo en el PDF (ver
  // acta_pdf._celda_firma). Y "entrega_linea_<id>"/"devolucion_linea_<id>"
  // (id = Reserva.id, una por fila de la tabla): la caja "[  ]" de cada
  // activo, donde se dibuja un chulito (entregado/devuelto) o una X (no
  // entregado), ver FirmarActaDialog.tsx y actas-pdf.ts -- las actas
  // firmadas antes de este cambio no traen estas claves, y el frontend lo
  // tolera igual que con "..._nota". Las anclas son las de la última acta
  // guardada si existe, si no las del acta base recién generada.
  anclas: Record<string, AnclaFirma>
  historial: ActaRegistrada[]
  // Todas las líneas del grupo en el orden del PDF, sin los filtros del
  // calendario -- de acá sale la lista del paso "Equipos".
  lineas: { id: number; nombre: string; estado: EstadoReserva }[]
}

export function obtenerEstadoActa(grupoId: number) {
  return request<EstadoActa>(`/reservas/grupo/${grupoId}/acta/estado`)
}

// Igual que descargarActaPdf: respuesta binaria, no pasa por `request<T>`.
export async function descargarActaHistorica(grupoId: number, actaId: number): Promise<Blob> {
  const resp = await conectar(`${BASE}/reservas/grupo/${grupoId}/actas/${actaId}/pdf`)
  if (!resp.ok) {
    const data = await resp.json().catch(() => null)
    throw errorHttp(resp.status, data)
  }
  return resp.blob()
}

// multipart/form-data a mano: `request<T>` siempre manda
// Content-Type: application/json cuando hay body, y un FormData necesita que
// el navegador ponga su propio boundary -- si se fija el header a mano el
// multipart queda mal formado y Flask no separa los campos.
export async function registrarActa(
  grupoId: number,
  datos: {
    archivo: Blob
    momento: MomentoActa
    origen: OrigenActa
    anclas?: Record<string, AnclaFirma>
    firmadoPor?: string
    nota?: string
    /** Ids de las líneas marcadas como entregadas/devueltas; sin él, todas. */
    equipos?: number[]
  }
): Promise<{ reservas: Sesion[]; acta: ActaRegistrada }> {
  const formData = new FormData()
  formData.append("archivo", datos.archivo, "acta.pdf")
  formData.append("momento", datos.momento)
  formData.append("origen", datos.origen)
  if (datos.anclas) formData.append("anclas", JSON.stringify(datos.anclas))
  if (datos.firmadoPor) formData.append("firmado_por", datos.firmadoPor)
  if (datos.nota) formData.append("nota", datos.nota)
  if (datos.equipos) formData.append("equipos", datos.equipos.join(","))

  const resp = await conectar(`${BASE}/reservas/grupo/${grupoId}/actas`, {
    method: "POST",
    body: formData,
  })
  const data = await resp.json().catch(() => null)
  if (!resp.ok) {
    throw errorHttp(resp.status, data)
  }
  return data as { reservas: Sesion[]; acta: ActaRegistrada }
}

// La LACS aprueba los activos pendientes y el backend le avisa al estudiante
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

// --- Crear reserva desde el dashboard (features/reservas/NuevaReservaDialog) ----
// Mismos campos y mismas validaciones de forma que el wizard del bot por
// WhatsApp (ver recursos_service._avanzar_wizard); a diferencia del bot,
// fecha bloqueada/plazo del seguro/inicio pasado no bloquean -- solo avisan
// (`advertencias` en la respuesta). Ver docs/ARQUITECTURA.md, sección "Crear
// reservas desde el dashboard".

export type ClienteNuevo = {
  nombre: string
  telefono: string
  codigo_uninorte?: string
  cedula?: string
}

export type NuevaReserva = {
  usuario_id?: number
  cliente_nuevo?: ClienteNuevo
  recurso_ids: number[]
  inicio: string
  fin: string
  tipo_uso: TipoUso
  nrc?: string
  modalidad: Modalidad
  integrantes?: string[]
  sale_de_barranquilla: boolean
  nota?: string
  notificar: boolean
}

export type ReservaCreada = {
  reservas: Sesion[]
  grupo_id: number
  usuario: Usuario
  usuario_creado: boolean
  advertencias: string[]
  notificado: boolean | null
}

export function crearReserva(datos: NuevaReserva) {
  return request<ReservaCreada>("/reservas", {
    method: "POST",
    body: JSON.stringify(datos),
  })
}

// IDs de recurso ocupados (confirmada/pendiente) en un rango horario -- lo usa
// el formulario de "Nueva reserva" para avisar en vivo si un activo marcado
// deja de estar libre mientras se completan los demás campos.
export function fetchOcupados(inicio: Date, fin: Date) {
  const params = new URLSearchParams({
    inicio: inicio.toISOString(),
    fin: fin.toISOString(),
  })
  return request<{ ocupados: number[] }>(`/reservas/ocupados?${params}`)
}

// --- Activos -----------------------------------------------------------

export type TipoRecurso = "equipo" | "espacio"
export type EstadoRecurso = "disponible" | "en_mantenimiento" | "no_disponible" | "no_devuelto"

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
// `motivo_estado`/`nota_estado`: obligatorios al sacar un activo de
// "no_devuelto" (métrica, ver recurso_estado_historial en 02-schema.sql).
export type MotivoSalidaNoDevuelto = "devuelto_tarde" | "encontrado" | "otro"
export type OpcionesCambioActivo = {
  propagar_espacio?: boolean
  motivo_estado?: MotivoSalidaNoDevuelto
  nota_estado?: string
}

export function actualizarActivo(
  id: number,
  cambios: CambiosActivo & OpcionesCambioActivo
) {
  return request<Recurso & { hijos_actualizados?: number }>(`/activos/${id}`, {
    method: "PATCH",
    body: JSON.stringify(cambios),
  })
}

// --- Excepciones (lista negra de fechas) -------------------------------------
// Días en que el bot no acepta reservas. Fechas civiles "YYYY-MM-DD" (hora LACS).
// dia_semana / dias_semana: 0 = lunes ... 6 = domingo (igual que diaDeSemana()).

export type OrigenExcepcion = "dia_semana" | "festivo" | "manual" | "desbloqueada"
export type TipoExcepcion = "bloqueo" | "desbloqueo"

export type ReglasExcepcion = { dias_semana: number[]; festivos_co: boolean; actualizado_en: string | null }

export type DiaExcepcion = {
  fecha: string
  dia_semana: number
  bloqueada: boolean
  origen: OrigenExcepcion | null
  festivo: string | null
  bloqueada_por_regla: boolean
  excepcion: { tipo: TipoExcepcion; motivo: string | null; actualizado_en: string | null } | null
}

export function fetchExcepciones(desde: string, hasta: string) {
  const params = new URLSearchParams({ desde, hasta })
  return request<{ reglas: ReglasExcepcion; dias: DiaExcepcion[] }>(`/excepciones?${params}`)
}

export function fetchReglasExcepcion() {
  return request<ReglasExcepcion>("/excepciones/reglas")
}

export function guardarReglasExcepcion(reglas: { dias_semana: number[]; festivos_co: boolean }) {
  return request<ReglasExcepcion>("/excepciones/reglas", { method: "PUT", body: JSON.stringify(reglas) })
}

export function guardarExcepcion(datos: { desde: string; hasta?: string; tipo: TipoExcepcion; motivo?: string | null }) {
  return request<{ dias: DiaExcepcion[] }>("/excepciones/fechas", { method: "POST", body: JSON.stringify(datos) })
}

export function eliminarExcepcion(fecha: string) {
  return request<DiaExcepcion>(`/excepciones/fechas/${fecha}`, { method: "DELETE" })
}
