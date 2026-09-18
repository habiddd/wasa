import type { EstadoReserva, Rol, Sesion, TipoRecurso } from "@/lib/api"

// Las reservas se guardan en UTC (ver models.Reserva) pero el negocio opera en
// hora local de la LACS, igual que el bot (recursos_service.ZONA_HORARIA_LACS).
// El calendario agrupa por día en esta zona a propósito: así muestra lo mismo
// aunque quien abra el dashboard tenga el navegador en otra zona horaria.
export const ZONA_LACS = "America/Bogota"

export type Vista = "mes" | "semana" | "dia"

/** Fecha de calendario (sin hora) en la zona de la LACS. `mes` es 1-12. */
export type DiaCivil = { anio: number; mes: number; dia: number }

const PARTES = new Intl.DateTimeFormat("en-CA", {
  timeZone: ZONA_LACS,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
})

function partesEnZona(fecha: Date) {
  const partes = PARTES.formatToParts(fecha)
  const valor = (tipo: string) => Number(partes.find((p) => p.type === tipo)?.value ?? 0)
  return {
    anio: valor("year"),
    mes: valor("month"),
    dia: valor("day"),
    // Algunos motores devuelven "24" para la medianoche con hour12:false.
    hora: valor("hour") % 24,
    minuto: valor("minute"),
    segundo: valor("second"),
  }
}

function dosDigitos(n: number) {
  return String(n).padStart(2, "0")
}

export function claveDe(dia: DiaCivil) {
  return `${dia.anio}-${dosDigitos(dia.mes)}-${dosDigitos(dia.dia)}`
}

export function diaDe(fecha: Date): DiaCivil {
  const { anio, mes, dia } = partesEnZona(fecha)
  return { anio, mes, dia }
}

export function hoy(): DiaCivil {
  return diaDe(new Date())
}

export function mismoDia(a: DiaCivil, b: DiaCivil) {
  return a.anio === b.anio && a.mes === b.mes && a.dia === b.dia
}

export function sumarDias(dia: DiaCivil, n: number): DiaCivil {
  // Date en UTC solo como aritmética de calendario: nunca se muestra tal cual.
  const d = new Date(Date.UTC(dia.anio, dia.mes - 1, dia.dia + n))
  return { anio: d.getUTCFullYear(), mes: d.getUTCMonth() + 1, dia: d.getUTCDate() }
}

export function sumarMeses(dia: DiaCivil, n: number): DiaCivil {
  const mes = dia.mes - 1 + n
  const anio = dia.anio + Math.floor(mes / 12)
  const mesNormalizado = ((mes % 12) + 12) % 12
  const diasDelMes = new Date(Date.UTC(anio, mesNormalizado + 1, 0)).getUTCDate()
  return { anio, mes: mesNormalizado + 1, dia: Math.min(dia.dia, diasDelMes) }
}

/** 0 = lunes ... 6 = domingo (la semana de la LACS empieza el lunes). */
export function diaDeSemana(dia: DiaCivil) {
  const domingoPrimero = new Date(Date.UTC(dia.anio, dia.mes - 1, dia.dia)).getUTCDay()
  return (domingoPrimero + 6) % 7
}

/**
 * Instante UTC que corresponde a una hora de pared en la zona de la LACS.
 * Colombia no tiene horario de verano, pero el ajuste se hace en dos pasadas
 * para no depender de eso.
 */
export function instanteEnZona(dia: DiaCivil, hora = 0, minuto = 0): Date {
  const objetivo = Date.UTC(dia.anio, dia.mes - 1, dia.dia, hora, minuto)
  let fecha = new Date(objetivo)
  for (let i = 0; i < 2; i++) {
    const p = partesEnZona(fecha)
    const comoUtc = Date.UTC(p.anio, p.mes - 1, p.dia, p.hora, p.minuto, p.segundo)
    const desfase = objetivo - comoUtc
    if (desfase === 0) break
    fecha = new Date(fecha.getTime() + desfase)
  }
  return fecha
}

export function inicioDelDia(dia: DiaCivil) {
  return instanteEnZona(dia)
}

export function finDelDia(dia: DiaCivil) {
  return instanteEnZona(sumarDias(dia, 1))
}

// --- Periodo visible --------------------------------------------------------

export function lunesDeLaSemana(dia: DiaCivil) {
  return sumarDias(dia, -diaDeSemana(dia))
}

/** Días que dibuja cada vista, en orden. El mes se rellena a semanas completas. */
export function diasDelPeriodo(vista: Vista, ancla: DiaCivil): DiaCivil[] {
  if (vista === "dia") return [ancla]
  if (vista === "semana") {
    const lunes = lunesDeLaSemana(ancla)
    return Array.from({ length: 7 }, (_, i) => sumarDias(lunes, i))
  }

  const primero: DiaCivil = { anio: ancla.anio, mes: ancla.mes, dia: 1 }
  const diasDelMes = new Date(Date.UTC(ancla.anio, ancla.mes, 0)).getUTCDate()
  const inicio = lunesDeLaSemana(primero)
  const semanas = Math.ceil((diaDeSemana(primero) + diasDelMes) / 7)
  return Array.from({ length: semanas * 7 }, (_, i) => sumarDias(inicio, i))
}

export function desplazar(vista: Vista, ancla: DiaCivil, delta: number): DiaCivil {
  if (vista === "mes") return sumarMeses(ancla, delta)
  return sumarDias(ancla, delta * (vista === "semana" ? 7 : 1))
}

// --- Reservas ---------------------------------------------------------------

export type ReservaUbicada = {
  reserva: Sesion
  inicio: Date
  fin: Date
}

/** Descarta las reservas sin fechas utilizables y las ordena por inicio. */
export function ubicarReservas(reservas: Sesion[]): ReservaUbicada[] {
  const ubicadas: ReservaUbicada[] = []
  for (const reserva of reservas) {
    if (!reserva.inicio || !reserva.fin) continue
    const inicio = new Date(reserva.inicio)
    const fin = new Date(reserva.fin)
    if (Number.isNaN(inicio.getTime()) || Number.isNaN(fin.getTime())) continue
    ubicadas.push({ reserva, inicio, fin: fin > inicio ? fin : new Date(inicio.getTime() + 60000) })
  }
  return ubicadas.sort((a, b) => a.inicio.getTime() - b.inicio.getTime())
}

// --- Eventos (una reserva hecha = un evento) --------------------------------

/**
 * Un evento del calendario: todas las reservas del mismo usuario en el mismo
 * horario. Una sola confirmación puede cubrir varios activos (una cámara y
 * sus lentes), y pintar una caja por activo llenaba el día de bloques
 * idénticos.
 */
export type EventoCalendario = {
  clave: string
  inicio: Date
  fin: Date
  /** Una línea por activo/lugar reservado. */
  reservas: Sesion[]
  usuarioId: number
  usuarioNombre: string
  usuarioTelefono: string
  usuarioRol: Rol
  /** Números de reserva (grupo_id) que componen el evento; normalmente uno. */
  numeros: number[]
  /** Estado con el que se pinta el evento; ver PRIORIDAD_ESTADO. */
  estado: EstadoReserva
  estadosMixtos: boolean
  /** Alguna línea espera la confirmación de la LACS (estado "pendiente"). */
  porConfirmar: boolean
  /** Reserva parcial: mezcla activos confirmados y pendientes. */
  parcial: boolean
  /** null cuando el evento mezcla activos y lugares. */
  tipo: TipoRecurso | null
}

// Con qué estado se pinta un evento cuyas líneas no coinciden: manda el más
// vigente, porque es el que le importa a quien mira el calendario. Pendiente
// va primero: una reserva parcial es algo que la LACS todavía tiene que atender.
const PRIORIDAD_ESTADO: EstadoReserva[] = [
  "pendiente",
  "confirmada",
  "no_show",
  "completada",
  "cancelada",
  "denegada",
]

/** Junta en un solo evento las líneas de una misma reserva y horario. */
export function agruparEnEventos(reservas: ReservaUbicada[]): EventoCalendario[] {
  const porClave = new Map<string, ReservaUbicada[]>()
  for (const item of reservas) {
    // El grupo identifica la reserva completa. El fallback conserva juntas
    // las líneas antiguas que aún no tenían grupo_id.
    const grupo = item.reserva.grupo_id ?? `${item.reserva.usuario_id}|${item.inicio.getTime()}|${item.fin.getTime()}`
    const clave = `${grupo}|${item.inicio.getTime()}|${item.fin.getTime()}`
    const lista = porClave.get(clave)
    if (lista) lista.push(item)
    else porClave.set(clave, [item])
  }

  const eventos: EventoCalendario[] = []
  for (const [clave, items] of porClave) {
    const lineas = items.map((i) => i.reserva)
    const primera = lineas[0]
    const estados = new Set(lineas.map((l) => l.estado))
    const tipos = new Set(lineas.map((l) => l.recurso_tipo))

    eventos.push({
      clave,
      inicio: items[0].inicio,
      fin: items[0].fin,
      reservas: lineas,
      usuarioId: primera.usuario_id,
      usuarioNombre: primera.usuario_nombre || primera.usuario_telefono,
      usuarioTelefono: primera.usuario_telefono,
      usuarioRol: primera.usuario_rol,
      numeros: [...new Set(lineas.map((l) => l.grupo_id).filter((n): n is number => n !== null))],
      estado: PRIORIDAD_ESTADO.find((e) => estados.has(e)) ?? primera.estado,
      estadosMixtos: estados.size > 1,
      porConfirmar: estados.has("pendiente"),
      parcial: estados.has("pendiente") && estados.has("confirmada"),
      tipo: tipos.size === 1 ? (primera.recurso_tipo ?? null) : null,
    })
  }

  return eventos.sort((a, b) => a.inicio.getTime() - b.inicio.getTime())
}

export function etiquetaEvento(evento: EventoCalendario) {
  const primero = etiquetaReserva(evento.reservas[0])
  return evento.reservas.length > 1 ? `${primero} +${evento.reservas.length - 1}` : primero
}

/** Lo que dice el badge de estado de una reserva completa. */
export function etiquetaEstadoEvento(evento: EventoCalendario) {
  if (evento.parcial) return "parcial"
  return evento.estadosMixtos ? "estados mixtos" : evento.estado
}

/** "N.º 8" o "N.º 8, 9" cuando el evento junta dos reservas del mismo horario. */
export function etiquetaNumeros(evento: EventoCalendario) {
  return evento.numeros.length ? `N.º ${evento.numeros.join(", ")}` : "sin número"
}

// Mismo valor que DIAS_MINIMOS_TRAMITE_SEGURO en recursos_service.py -- solo
// para textos ("mínimo 5 días hábiles"). Son días HÁBILES (lunes a viernes,
// sin festivos de Colombia): la fecha mínima la calcula siempre el backend
// (ver fetchFechaMinimaSeguro / Sesion.fecha_minima_seguro en lib/api.ts),
// nunca se recalcula acá.
export const DIAS_MINIMOS_SEGURO = 5

// --- Rejilla ----------------------------------------------------------------

/** Lo único que la rejilla necesita de lo que pinta: un rango de tiempo. */
type ConRango = { inicio: Date; fin: Date }

/**
 * Agrupa por día lo que se va a pintar. Algo que dura varios días aparece en
 * cada uno de los días que ocupa, no solo en el que empieza.
 */
export function agruparPorDia<T extends ConRango>(items: T[], dias: DiaCivil[]) {
  const porDia = new Map<string, T[]>()
  for (const dia of dias) {
    const desde = inicioDelDia(dia).getTime()
    const hasta = finDelDia(dia).getTime()
    porDia.set(
      claveDe(dia),
      items.filter((i) => i.inicio.getTime() < hasta && i.fin.getTime() > desde)
    )
  }
  return porDia
}

export type BloqueDia<T extends ConRango = ConRango> = T & {
  /** Porcentajes sobre la altura del día, ya recortados a sus 24 horas. */
  topPct: number
  altoPct: number
  columna: number
  columnas: number
  continuaAntes: boolean
  continuaDespues: boolean
}

/**
 * Posiciona en la rejilla horaria lo que cae en un día y reparte en columnas
 * lo que se solapa (dos reservas distintas a la misma hora).
 */
export function bloquesDelDia<T extends ConRango>(items: T[], dia: DiaCivil): BloqueDia<T>[] {
  const desde = inicioDelDia(dia).getTime()
  const hasta = finDelDia(dia).getTime()
  const total = hasta - desde

  const bloques: BloqueDia<T>[] = items
    .map((r) => {
      const inicioRecortado = Math.max(r.inicio.getTime(), desde)
      const finRecortado = Math.min(r.fin.getTime(), hasta)
      return {
        ...r,
        topPct: ((inicioRecortado - desde) / total) * 100,
        // Mínimo visible para reservas muy cortas.
        altoPct: Math.max(((finRecortado - inicioRecortado) / total) * 100, 1.5),
        columna: 0,
        columnas: 1,
        continuaAntes: r.inicio.getTime() < desde,
        continuaDespues: r.fin.getTime() > hasta,
      }
    })
    .sort((a, b) => a.topPct - b.topPct || b.altoPct - a.altoPct)

  // Reparto en columnas: se acumula un grupo mientras haya solape con alguno
  // de sus bloques y, al cerrarlo, todos comparten el mismo número de columnas.
  let grupo: BloqueDia<T>[] = []
  let finDelGrupo = -1

  function cerrarGrupo() {
    const columnas = grupo.reduce((max, b) => Math.max(max, b.columna + 1), 1)
    for (const b of grupo) b.columnas = columnas
    grupo = []
    finDelGrupo = -1
  }

  for (const bloque of bloques) {
    if (grupo.length && bloque.topPct >= finDelGrupo) cerrarGrupo()

    const ocupadas = new Set(
      grupo.filter((b) => b.topPct + b.altoPct > bloque.topPct).map((b) => b.columna)
    )
    let columna = 0
    while (ocupadas.has(columna)) columna++
    bloque.columna = columna

    grupo.push(bloque)
    finDelGrupo = Math.max(finDelGrupo, bloque.topPct + bloque.altoPct)
  }
  if (grupo.length) cerrarGrupo()

  return bloques
}

// --- Formato ----------------------------------------------------------------

const HORA = new Intl.DateTimeFormat("es-CO", {
  timeZone: ZONA_LACS,
  hour: "numeric",
  minute: "2-digit",
  hour12: true,
})

const FECHA_LARGA = new Intl.DateTimeFormat("es-CO", {
  timeZone: ZONA_LACS,
  weekday: "long",
  day: "numeric",
  month: "long",
  year: "numeric",
})

const FECHA_CORTA = new Intl.DateTimeFormat("es-CO", {
  timeZone: ZONA_LACS,
  day: "numeric",
  month: "short",
})

const MES_ANIO = new Intl.DateTimeFormat("es-CO", {
  timeZone: ZONA_LACS,
  month: "long",
  year: "numeric",
})

function capitalizar(texto: string) {
  return texto.charAt(0).toUpperCase() + texto.slice(1)
}

export function hora(fecha: Date) {
  return HORA.format(fecha)
}

/**
 * Valor para un <input type="datetime-local">: el navegador lo interpreta en
 * la zona del equipo, así que se escribe y se lee en hora de la LACS para que
 * el formulario muestre lo mismo que el calendario.
 */
export function aInputLocal(fecha: Date) {
  const { anio, mes, dia, hora, minuto } = partesEnZona(fecha)
  return `${anio}-${dosDigitos(mes)}-${dosDigitos(dia)}T${dosDigitos(hora)}:${dosDigitos(minuto)}`
}

/** Inversa de aInputLocal: "2026-09-02T15:00" en hora LACS -> instante UTC. */
export function desdeInputLocal(valor: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(valor)
  if (!m) return null
  const [, anio, mes, dia, hora, minuto] = m.map(Number)
  return instanteEnZona({ anio, mes, dia }, hora, minuto)
}

/** "3pm" / "3:30pm": en las celdas del mes no cabe "3:00 p. m." + el recurso. */
export function horaCompacta(fecha: Date) {
  const { hora, minuto } = partesEnZona(fecha)
  const sufijo = hora < 12 ? "am" : "pm"
  const doce = hora % 12 || 12
  return minuto ? `${doce}:${dosDigitos(minuto)}${sufijo}` : `${doce}${sufijo}`
}

export function fechaHora(fecha: Date) {
  return `${capitalizar(FECHA_LARGA.format(fecha))}, ${HORA.format(fecha)}`
}

/** "2026-09-25" -> "Viernes, 25 de septiembre de 2026" -- fechas del backend
 * (ej. `fecha_minima_seguro`, `fetchFechaMinimaSeguro`) que no traen hora. */
export function fechaCivilLarga(iso: string) {
  const [anio, mes, dia] = iso.split("-").map(Number)
  return capitalizar(FECHA_LARGA.format(inicioDelDia({ anio, mes, dia })))
}

export function etiquetaPeriodo(vista: Vista, ancla: DiaCivil) {
  if (vista === "mes") return capitalizar(MES_ANIO.format(inicioDelDia(ancla)))
  if (vista === "dia") return capitalizar(FECHA_LARGA.format(inicioDelDia(ancla)))
  const lunes = lunesDeLaSemana(ancla)
  const domingo = sumarDias(lunes, 6)
  const inicio = FECHA_CORTA.format(inicioDelDia(lunes))
  const fin = FECHA_CORTA.format(inicioDelDia(domingo))
  return `${inicio} – ${fin} ${domingo.anio}`
}

export const NOMBRES_DIA = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]

// El color codifica el estado (mismos tonos que los badges de Sesiones) y el
// icono codifica el tipo de recurso: la paleta del dashboard es neutra y no
// tiene dos acentos que distingan activo/lugar sin inventar tokens nuevos.
// Por lo mismo, pendiente comparte el ámbar de no_show pero con borde
// punteado y sin relleno: todavía no es una reserva firme. Denegada comparte
// el rojo de cancelada, sin relleno.
export const ESTILO_ESTADO: Record<
  EstadoReserva,
  {
    chip: string
    barra: string
    badge: "success" | "secondary" | "destructive" | "warning" | "outline"
    badgeClase?: string
  }
> = {
  confirmada: {
    chip: "border-success/40 bg-success/10 text-success hover:bg-success/20",
    barra: "bg-success",
    badge: "success",
  },
  pendiente: {
    chip: "border-dashed border-warning bg-warning/5 text-warning hover:bg-warning/15",
    barra: "border border-dashed border-warning",
    badge: "outline",
    badgeClase: "border-dashed border-warning text-warning",
  },
  completada: {
    chip: "border-border bg-muted text-muted-foreground hover:bg-muted/70",
    barra: "bg-muted-foreground",
    badge: "secondary",
  },
  cancelada: {
    chip: "border-destructive/40 bg-destructive/10 text-destructive line-through hover:bg-destructive/20",
    barra: "bg-destructive",
    badge: "destructive",
  },
  denegada: {
    chip: "border-destructive/40 text-destructive line-through hover:bg-destructive/10",
    barra: "border border-destructive",
    badge: "outline",
    badgeClase: "border-destructive/50 text-destructive",
  },
  no_show: {
    chip: "border-warning/40 bg-warning/10 text-warning hover:bg-warning/20",
    barra: "bg-warning",
    badge: "warning",
  },
}

export function estiloDe(estado: EstadoReserva) {
  return ESTILO_ESTADO[estado] ?? ESTILO_ESTADO.completada
}

export function etiquetaReserva(reserva: Sesion) {
  return reserva.recurso_nombre || `Recurso #${reserva.recurso_id}`
}

export function etiquetaUsuario(reserva: Sesion) {
  return reserva.usuario_nombre || reserva.usuario_telefono
}
