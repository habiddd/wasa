import { getDocument, GlobalWorkerOptions } from "pdfjs-dist"
// Patrón estándar de Vite para el worker de pdf.js: `?url` hace que Vite lo
// trate como asset y devuelva la URL final (con hash, en el build de
// producción) en vez de intentar empaquetarlo como módulo JS normal -- el
// worker corre en su propio hilo, cargado por esa URL, no importado.
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url"

import type { AnclaFirma } from "@/lib/api"

GlobalWorkerOptions.workerSrc = workerUrl

// Decisión del usuario (ver cabecera del plan): el lienzo de firma muestra
// de fondo la zona real del acta (la raya de "Firma" con su etiqueta),
// renderizada en el navegador con pdf.js, para que la persona vea dónde
// firmar y escriba encima en vez de rayar a ciegas. La cédula y la fecha ya
// no se escriben a mano -- el lienzo volvió a ser solo la franja de firma,
// no el cuadro completo, así es imposible tachar sin querer lo que el
// servidor y el propio estampado ya imprimieron. El costo de renderizar cae
// en el navegador del administrador -- el usuario aceptó ese costo a
// sabiendas; en los contenedores (Flask, Postgres) es cero, pdf.js no toca
// el backend.
//
// Escala generosa para que el fondo se vea nítido aunque el lienzo se
// estire en una pantalla grande -- mismo criterio que ESCALA_EXPORTACION en
// FirmaCanvas.tsx: el usuario dijo que el costo en el navegador no importa.
export const ESCALA_RECORTE = 3

/**
 * Renderiza la página del PDF base con pdf.js y recorta la zona que marca
 * `ancla` (una de las claves `entrega_usuario`/`entrega_admin`/
 * `devolucion_usuario`/`devolucion_admin`, ver acta_pdf.py) -- el resultado
 * es el fondo que pinta FirmaCanvas detrás del trazo, para que la persona
 * vea la raya real de "Firma" y firme encima en vez de a ciegas.
 */
export async function recortarCuadro(
  pdfBase: ArrayBuffer,
  ancla: AnclaFirma,
  escala: number = ESCALA_RECORTE
): Promise<HTMLCanvasElement> {
  // pdf.js CONSUME el ArrayBuffer que se le pasa (queda "detached": ya no se
  // puede leer ni volver a pasar a nada más). Quien llama a `recortarCuadro`
  // sigue necesitando los bytes originales después -- `estamparFirmas`
  // (actas-pdf.ts) carga el mismo `pdfBase` con pdf-lib para componer el PDF
  // firmado -- así que acá se trabaja sobre una COPIA (`slice(0)`), nunca
  // sobre el buffer que recibimos. Sin esto, `PDFDocument.load(pdfBase)`
  // fallaría más tarde con el buffer ya vacío, y el error no señala esta
  // línea como culpable: es fácil de cometer y silencioso.
  const copia = pdfBase.slice(0)
  const documento = await getDocument({ data: copia }).promise
  const pagina = await documento.getPage(ancla.pagina)

  // Alto de la página EN PUNTOS (viewport a escala 1) -- hace falta para
  // convertir el origen de coordenadas de la ancla, ver más abajo.
  const alturaPaginaPt = pagina.getViewport({ scale: 1 }).height

  const viewport = pagina.getViewport({ scale: escala })
  const lienzoPagina = document.createElement("canvas")
  lienzoPagina.width = viewport.width
  lienzoPagina.height = viewport.height
  const ctxPagina = lienzoPagina.getContext("2d")
  if (!ctxPagina) throw new Error("No se pudo preparar el lienzo para renderizar el PDF.")
  await pagina.render({ canvasContext: ctxPagina, viewport }).promise

  // reportlab (acta_pdf.py) y pdf-lib (actas-pdf.ts) comparten origen
  // ABAJO-izquierda -- por eso ninguno de los dos invierte el eje Y al leer
  // o escribir una ancla. El canvas 2D, en cambio, tiene su origen
  // ARRIBA-izquierda: acá, y SOLO acá, hay que invertir. Es exactamente el
  // tipo de ajuste que alguien podría "corregir" después en el sitio
  // equivocado (p. ej. en estamparFirmas) creyendo que falta en todos
  // lados -- no hace falta, y moverlo rompería el estampado, que ya está
  // verificado sin inversión.
  const sx = ancla.x * escala
  const sy = (alturaPaginaPt - ancla.y - ancla.alto) * escala
  const sw = ancla.ancho * escala
  const sh = ancla.alto * escala

  const recorte = document.createElement("canvas")
  recorte.width = sw
  recorte.height = sh
  const ctxRecorte = recorte.getContext("2d")
  if (!ctxRecorte) throw new Error("No se pudo preparar el lienzo para recortar el cuadro.")
  ctxRecorte.drawImage(lienzoPagina, sx, sy, sw, sh, 0, 0, sw, sh)

  return recorte
}
