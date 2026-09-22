import { PDFDocument, StandardFonts, rgb, type PDFFont } from "pdf-lib"

import type { AnclaFirma, EstadoReserva, MomentoActa } from "@/lib/api"
import { ZONA_LACS } from "@/features/calendario/calendario-utils"

// Todo el trabajo pesado del PDF vive acá: el resto del front solo llama a
// `estamparFirmas` y arma un Blob con el resultado. Decisión del usuario,
// tomada a sabiendas del compromiso de seguridad que implica (ver cabecera
// del plan): el PDF firmado se compone EN EL NAVEGADOR con pdf-lib, no en el
// servidor -- el backend solo valida (tamaño, cabecera %PDF-) y guarda.

const TAMANO_TEXTO_PT = 8.5

/**
 * A qué estado pasaría la reserva si se registrara AHORA el acta de este
 * momento. Solo para avisos en la UI antes de guardar (BotonActaPdf,
 * FirmarActaDialog) -- el estado real lo decide siempre el backend, con su
 * propio reloj, al aplicar la transición (ver actas_service.aplicar_transicion).
 */
export function estadoResultante(momento: MomentoActa, fin: Date): EstadoReserva {
  if (momento === "entrega") return "entregada"
  if (new Date() > fin) return "devuelta_tarde"
  return "completada"
}

const FORMATO_FECHA_ACTA = new Intl.DateTimeFormat("es-CO", {
  timeZone: ZONA_LACS,
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
  hour12: true,
})

/**
 * "22/09/2026, 3:45 p. m." en hora de la LACS, sea cual sea la del
 * navegador -- misma zona horaria que usa el resto del dashboard
 * (`ZONA_LACS`) y no `getDate()/getHours()`, que leerían la del navegador de
 * quien firma. `Intl` en es-CO separa "p. m." con un espacio fino (U+202F) o
 * de no separación (U+00A0) según el runtime -- ninguno de los dos existe en
 * WinAnsi, la codificación de Helvetica (la fuente core que usa reportlab y
 * que se reutiliza acá para que el trazo case con el resto del PDF), así que
 * pdf-lib los dibujaría como un cuadro relleno en vez de un espacio. Mismo
 * problema, documentado, que "→"/"✓" en acta_pdf.py -- se normalizan a un
 * espacio normal antes de dibujar.
 */
export function formatearFechaActa(fecha: Date): string {
  return FORMATO_FECHA_ACTA.format(fecha).replace(/[  ]/g, " ")
}

// Las 12 claves que devuelve el backend (ver acta_pdf._celda_firma y
// generar_acta_con_anclas): por cuadro de firma, la zona de firma
// (`usuario`/`admin`, la raya de "Firma" con su etiqueta -- ver A5 del plan
// de cédula/fecha automáticas), más su hueco de fecha (`_fecha`) y su hueco
// de C.C. (`_cc`). Cada cuadro del acta tiene su propia línea "Fecha y hora
// ___" y su propia línea "C.C. ___", así que se rellenan las DOS de este
// momento, no una sola para ambos firmantes.
function clavesDe(momento: MomentoActa) {
  const prefijo = momento === "entrega" ? "entrega" : "devolucion"
  return {
    usuario: `${prefijo}_usuario`,
    usuarioFecha: `${prefijo}_usuario_fecha`,
    usuarioCc: `${prefijo}_usuario_cc`,
    admin: `${prefijo}_admin`,
    adminFecha: `${prefijo}_admin_fecha`,
    adminCc: `${prefijo}_admin_cc`,
  }
}

/**
 * Estampa el PNG del lienzo de firma (ver FirmaCanvas.exportarPng) dentro de
 * la zona de firma -- calco EXACTO, sin conservar proporción ni centrar: el
 * lienzo del navegador ya tiene la proporción de esa zona (prop `proporcion`
 * de FirmaCanvas, calculada como `ancla.alto / ancla.ancho`), así que estirar
 * a `{ancho, alto}` no deforma nada.
 */
async function estamparImagen(pdf: PDFDocument, png: Uint8Array, ancla: AnclaFirma) {
  const pagina = pdf.getPage(ancla.pagina - 1)
  const imagen = await pdf.embedPng(png)

  pagina.drawImage(imagen, {
    x: ancla.x,
    y: ancla.y,
    width: ancla.ancho,
    height: ancla.alto,
  })
}

/**
 * Escribe `texto` sobre el hueco que marca `ancla` -- sirve igual para la
 * fecha y para la cédula del administrador. El backend ya entrega `ancla.x`
 * corrido más allá de la etiqueta ("Fecha y hora "/"C.C. ", ver
 * acta_pdf._Ancla.despl_x): acá NO se mide ningún texto ni se conoce la
 * etiqueta, solo se dibuja en el hueco tal cual llega. Eso elimina el
 * acoplamiento que había antes entre este archivo y acta_pdf.py -- mantener
 * la misma cadena literal de la etiqueta sincronizada en dos lenguajes ya
 * causó un bug por desalineación en una iteración anterior.
 */
function estamparTexto(pdf: PDFDocument, ancla: AnclaFirma, fuente: PDFFont, texto: string) {
  const pagina = pdf.getPage(ancla.pagina - 1)

  // Tapar los guiones bajos (o la cédula que hubiera antes, si se está
  // corrigiendo) antes de escribir encima -- sin esto el texto se superpone
  // y queda tachado e ilegible (verificado mirando el PDF, no supuesto). La
  // hoja es blanca, así que el rectángulo desaparece igual impreso que en
  // pantalla.
  pagina.drawRectangle({
    x: ancla.x,
    y: ancla.y,
    width: ancla.ancho,
    height: ancla.alto - 2,
    color: rgb(1, 1, 1),
  })

  pagina.drawText(texto, {
    x: ancla.x,
    // El ancla marca la base de la línea y su alto es el interlineado (11pt
    // para una fuente de 8.5), así que la línea base del texto queda un par
    // de puntos por encima: justo sobre los guiones bajos, no debajo.
    y: ancla.y + (ancla.alto - TAMANO_TEXTO_PT) / 2,
    size: TAMANO_TEXTO_PT,
    font: fuente,
  })
}

/**
 * Compone el acta firmada: carga el PDF base (generado por reportlab, con
 * sus anclas ya calculadas por el backend -- ver acta_pdf.py y
 * `/acta/estado`), estampa las dos firmas del momento (usuario y
 * administrador LACS), la fecha y hora en los dos cuadros, y -- si se
 * tecleó -- la cédula del administrador. A mano solo queda la firma: la
 * cédula del usuario ya viene impresa por el servidor (ver
 * acta_pdf._celda_firma) y no se toca acá.
 *
 * Las coordenadas de pdf-lib y las de reportlab comparten el mismo origen
 * (esquina inferior izquierda de la página, en puntos): NO hay que invertir
 * el eje Y en ningún punto de este archivo. Es el tipo de cosa que alguien
 * "corrige" más tarde creyendo que hay que voltear la coordenada -- no hay
 * que hacerlo, las anclas ya vienen en el sistema de pdf-lib tal cual.
 */
export async function estamparFirmas(
  pdfBase: ArrayBuffer,
  anclas: Record<string, AnclaFirma>,
  momento: MomentoActa,
  firmas: { usuario: Blob; admin: Blob },
  fecha: Date,
  cedulaAdmin?: string
): Promise<Uint8Array> {
  const pdf = await PDFDocument.load(pdfBase)
  const claves = clavesDe(momento)

  const [pngUsuario, pngAdmin] = await Promise.all([
    firmas.usuario.arrayBuffer(),
    firmas.admin.arrayBuffer(),
  ])

  const anclaUsuario = anclas[claves.usuario]
  const anclaAdmin = anclas[claves.admin]
  if (!anclaUsuario || !anclaAdmin) {
    throw new Error("El acta base no trae las anclas de firma esperadas para este momento.")
  }

  await estamparImagen(pdf, new Uint8Array(pngUsuario), anclaUsuario)
  await estamparImagen(pdf, new Uint8Array(pngAdmin), anclaAdmin)

  const fuente = await pdf.embedFont(StandardFonts.Helvetica)
  const textoFecha = formatearFechaActa(fecha)

  // La MISMA fecha se replica en los dos cuadros (usuario y administrador)
  // de este momento -- lo pidió el usuario: es la fecha del instante en que
  // se firma, no dos relojes distintos. Se tratan sin reventar si alguna
  // ancla faltara: una firma sin su fecha impresa sigue siendo un acta
  // válida, y preferimos guardarla a perder las dos firmas que el
  // administrador acaba de recoger.
  for (const clave of [claves.usuarioFecha, claves.adminFecha]) {
    const ancla = anclas[clave]
    if (ancla) estamparTexto(pdf, ancla, fuente, textoFecha)
  }

  // La cédula del administrador es opcional -- si se dejó vacía, la línea
  // del PDF se conserva con sus guiones (ver acta_pdf._celda_firma). La del
  // USUARIO nunca se toca acá: viene impresa del servidor en el acta base.
  const cedulaLimpia = cedulaAdmin?.trim()
  if (cedulaLimpia) {
    const anclaCc = anclas[claves.adminCc]
    if (anclaCc) estamparTexto(pdf, anclaCc, fuente, cedulaLimpia)
  }

  return pdf.save()
}
