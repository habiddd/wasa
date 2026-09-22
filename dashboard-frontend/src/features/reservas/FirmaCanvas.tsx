import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react"

import { Button } from "@/components/ui/button"

type Punto = { x: number; y: number }

export type FirmaCanvasHandle = {
  /**
   * Exporta TODO el lienzo a PNG, con fondo transparente y solo los trazos
   * -- `null` si todavía no hay ningún trazo. No recorta al bounding box de
   * la tinta: la posición del trazo dentro de la franja de firma sigue
   * siendo información (la firma puede quedar más arriba o más abajo dentro
   * del hueco), y recortarla la correría del sitio donde la persona
   * efectivamente firmó. El lienzo entero ya tiene la proporción de la zona
   * de firma del PDF (prop `proporcion`), así que exportarlo completo mapea
   * 1:1 sin deformar nada (ver actas-pdf.ts, que estampa esta imagen
   * calcando el tamaño exacto de esa zona).
   */
  exportarPng: () => Blob | null
  estaVacio: () => boolean
  /** Borra todos los trazos -- lo usa el diálogo de firma al cancelar o al
   * guardar, para que la próxima apertura empiece en blanco. */
  limpiar: () => void
}

// Resolución generosa: el usuario dijo que el coste en el navegador no
// importa acá (se exporta una vez, no en un bucle), así que se sobremuestrea
// fijo a 3x en vez de limitarse al devicePixelRatio real del equipo -- una
// firma dibujada en una pantalla de baja densidad no debería salir peor que
// una en una de alta densidad.
const ESCALA_EXPORTACION = 3
const GROSOR_TRAZO = 2.5

function dataUrlABlob(dataUrl: string): Blob {
  const [cabecera, base64] = dataUrl.split(",")
  const mime = /:(.*?);/.exec(cabecera)?.[1] ?? "image/png"
  const binario = atob(base64)
  const bytes = new Uint8Array(binario.length)
  for (let i = 0; i < binario.length; i++) bytes[i] = binario.charCodeAt(i)
  return new Blob([bytes], { type: mime })
}

/** Une los puntos de un trazo con curvas cuadráticas por los puntos medios:
 * unirlos con líneas rectas se ve "poligonal" cuando pointermove dispara a
 * baja frecuencia (dedo o lápiz moviéndose rápido). */
function dibujarTrazo(ctx: CanvasRenderingContext2D, trazo: Punto[]) {
  if (trazo.length === 0) return
  if (trazo.length === 1) {
    // Un toque sin arrastre: un punto, no una curva.
    ctx.beginPath()
    ctx.arc(trazo[0].x, trazo[0].y, GROSOR_TRAZO / 2, 0, Math.PI * 2)
    ctx.fillStyle = "#000"
    ctx.fill()
    return
  }
  ctx.beginPath()
  ctx.moveTo(trazo[0].x, trazo[0].y)
  for (let i = 1; i < trazo.length - 1; i++) {
    const medio = { x: (trazo[i].x + trazo[i + 1].x) / 2, y: (trazo[i].y + trazo[i + 1].y) / 2 }
    ctx.quadraticCurveTo(trazo[i].x, trazo[i].y, medio.x, medio.y)
  }
  const ultimo = trazo[trazo.length - 1]
  ctx.lineTo(ultimo.x, ultimo.y)
  ctx.strokeStyle = "#000"
  ctx.lineWidth = GROSOR_TRAZO
  ctx.lineCap = "round"
  ctx.lineJoin = "round"
  ctx.stroke()
}

type Props = {
  className?: string
  /** Avisa tras cada trazo/deshacer/limpiar -- el diálogo de firma lo usa
   * para habilitar "Siguiente" solo cuando ya hay algo firmado, sin tener
   * que sondear `estaVacio()` fuera de un ciclo de eventos de React. */
  onTrazosCambian?: (vacio: boolean) => void
  /** Recorte de la franja de firma real del acta (ver recorte-acta.ts),
   * pintado de fondo para que la persona vea la raya y dónde firmar.
   * `null`/`undefined` mientras pdf.js todavía lo está renderizando -- el
   * lienzo funciona igual (se puede dibujar), solo que sin la guía visual. */
  fondo?: CanvasImageSource | null
  /** `ancla.alto / ancla.ancho` de la zona de firma del PDF -- fija la
   * proporción del lienzo (vía `aspect-ratio` en CSS) para que lo dibujado
   * mapee 1:1 sobre esa zona sin deformarse al estamparlo (ver actas-pdf.ts). */
  proporcion: number
}

/**
 * Lienzo de firma a mano alzada, del tamaño de la FRANJA de firma del acta
 * (la raya de "Firma" y su etiqueta, no el cuadro completo) -- la cédula y
 * la fecha ya no se escriben a mano (las imprime el servidor y las estampa
 * el propio navegador, ver acta_pdf.py y actas-pdf.ts), así que a mano solo
 * queda la firma. Pointer Events en vez de touch/mouse por separado: cubre
 * ratón, dedo y lápiz con una sola ruta de eventos.
 *
 * Los trazos se guardan como arrays de puntos (coordenadas CSS, no de
 * dispositivo) en vez de capturar un `ImageData` por paso: así "Deshacer" es
 * barato (quitar el último trazo y repintar todo desde cero).
 */
export const FirmaCanvas = forwardRef<FirmaCanvasHandle, Props>(
  function FirmaCanvas({ className, onTrazosCambian, fondo, proporcion }, ref) {
    const canvasRef = useRef<HTMLCanvasElement>(null)
    const trazosRef = useRef<Punto[][]>([])
    const trazoActualRef = useRef<Punto[] | null>(null)
    // El fondo también vive en un ref (no en el estado del componente):
    // `repintar` es imperativo y ya lee los trazos desde refs por la misma
    // razón -- entrar y salir de React en cada trazo sería más lento sin
    // ninguna ganancia, nada de esto se muestra en el árbol de React.
    const fondoRef = useRef<CanvasImageSource | null | undefined>(fondo)
    // Último tamaño en píxeles CSS con el que se midió el lienzo. NO se
    // vuelve a medir con getBoundingClientRect() al repintar ni al exportar:
    // los tres pasos del diálogo están siempre montados y los inactivos se
    // esconden con `hidden`, y un elemento oculto mide 0x0. Al exportar la
    // firma del usuario (paso 1) estando ya en el paso 2 eso producía un
    // canvas de 0x0 cuyo toDataURL() devuelve "data:," -- un blob vacío que
    // pdf-lib rechaza con "The input is not a PNG file!", sin más pistas.
    const tamanoRef = useRef({ ancho: 0, alto: 0 })
    // Los trazos viven en refs (repintar es imperativo, vía canvas 2D) --
    // este estado solo existe para que los botones Limpiar/Deshacer se
    // habiliten o deshabiliten cuando cambia el contenido.
    const [hayTrazos, setHayTrazos] = useState(false)

    function repintar() {
      const canvas = canvasRef.current
      const ctx = canvas?.getContext("2d")
      if (!canvas || !ctx) return
      const dpr = window.devicePixelRatio || 1
      ctx.setTransform(1, 0, 0, 1, 0, 0)
      ctx.clearRect(0, 0, canvas.width, canvas.height)
      ctx.scale(dpr, dpr)
      if (fondoRef.current) {
        // Coordenadas CSS (mismo sistema que usan los trazos, ver
        // `puntoDesdeEvento`): se estira el recorte de pdf.js al tamaño en
        // pantalla del lienzo, que ya tiene la proporción de la caja
        // (prop `proporcion`), así que no hay deformación. Se pinta ACÁ, en
        // cada repintado -- nunca dentro de `exportarPng` (ver ese método):
        // si el fondo quedara en el PNG exportado, el cuadro del acta
        // saldría impreso DOS VECES al estampar esa imagen sobre el PDF
        // real (el PDF base ya lo trae impreso).
        const { ancho, alto } = tamanoRef.current
        ctx.drawImage(fondoRef.current, 0, 0, ancho, alto)
      }
      for (const trazo of trazosRef.current) dibujarTrazo(ctx, trazo)
      if (trazoActualRef.current) dibujarTrazo(ctx, trazoActualRef.current)
    }

    useEffect(() => {
      fondoRef.current = fondo
      repintar()
      // eslint-disable-next-line react-hooks/exhaustive-deps -- `repintar` no es estable entre renders (lee refs), pero no necesita estarlo: solo repinta.
    }, [fondo])

    // El tamaño del canvas se fija en píxeles de DISPOSITIVO (rect * dpr)
    // pero se dibuja en coordenadas CSS con el contexto escalado por dpr:
    // así el trazo no sale pixelado en pantallas de alta densidad. Un
    // ResizeObserver en vez de un efecto con [] porque el diálogo que
    // contiene este lienzo puede cambiar de tamaño (ej. al pasar de un paso
    // a otro) sin que el componente se desmonte.
    useEffect(() => {
      const canvas = canvasRef.current
      if (!canvas) return
      function ajustarTamano() {
        const dpr = window.devicePixelRatio || 1
        const rect = canvas!.getBoundingClientRect()
        // Un paso oculto mide 0x0: se ignora y se conserva la última medida
        // buena, que es la que usan `repintar` y `exportarPng`.
        if (rect.width === 0 || rect.height === 0) return
        tamanoRef.current = { ancho: rect.width, alto: rect.height }
        canvas!.width = rect.width * dpr
        canvas!.height = rect.height * dpr
        repintar()
      }
      ajustarTamano()
      const observer = new ResizeObserver(ajustarTamano)
      observer.observe(canvas)
      return () => observer.disconnect()
    }, [])

    function puntoDesdeEvento(e: ReactPointerEvent<HTMLCanvasElement>): Punto {
      const rect = canvasRef.current!.getBoundingClientRect()
      return { x: e.clientX - rect.left, y: e.clientY - rect.top }
    }

    function iniciarTrazo(e: ReactPointerEvent<HTMLCanvasElement>) {
      e.preventDefault()
      canvasRef.current?.setPointerCapture(e.pointerId)
      trazoActualRef.current = [puntoDesdeEvento(e)]
      repintar()
    }

    function continuarTrazo(e: ReactPointerEvent<HTMLCanvasElement>) {
      if (!trazoActualRef.current) return
      trazoActualRef.current.push(puntoDesdeEvento(e))
      repintar()
    }

    function terminarTrazo() {
      if (trazoActualRef.current && trazoActualRef.current.length > 0) {
        trazosRef.current.push(trazoActualRef.current)
        setHayTrazos(true)
        onTrazosCambian?.(false)
      }
      trazoActualRef.current = null
      repintar()
    }

    function limpiar() {
      trazosRef.current = []
      trazoActualRef.current = null
      setHayTrazos(false)
      onTrazosCambian?.(true)
      repintar()
    }

    function deshacer() {
      trazosRef.current.pop()
      const vacio = trazosRef.current.length === 0
      setHayTrazos(!vacio)
      onTrazosCambian?.(vacio)
      repintar()
    }

    useImperativeHandle(ref, () => ({
      estaVacio: () => trazosRef.current.length === 0,
      limpiar,
      exportarPng: () => {
        if (trazosRef.current.length === 0) return null
        const canvas = canvasRef.current
        if (!canvas) return null

        // No se recorta al bounding box de la tinta: la posición del trazo
        // dentro de la franja es información, y recortarla la perdería. Se
        // exporta el lienzo COMPLETO, del mismo tamaño en pantalla (CSS px)
        // multiplicado por ESCALA_EXPORTACION -- ver actas-pdf.ts, que
        // estampa esta imagen calcando el tamaño exacto de la zona de firma
        // del PDF, sin escalar conservando proporción ni centrar.
        // Tamaño guardado, no medido acá: para cuando se exporta, este paso
        // del diálogo ya está oculto y mediría 0x0 (ver `tamanoRef`).
        const { ancho, alto } = tamanoRef.current
        if (ancho === 0 || alto === 0) return null
        const salida = document.createElement("canvas")
        salida.width = ancho * ESCALA_EXPORTACION
        salida.height = alto * ESCALA_EXPORTACION
        const ctx = salida.getContext("2d")
        if (!ctx) return null
        // Sin fillRect ni `drawImage` del fondo antes de dibujar: el canvas
        // nace transparente y así se exporta -- solo los trazos, nunca el
        // cuadro del acta que se ve de fondo en pantalla (prop `fondo`). Si
        // el fondo quedara acá, el cuadro saldría impreso DOS VECES al
        // estampar esta imagen sobre el PDF real (que ya lo trae impreso).
        ctx.scale(ESCALA_EXPORTACION, ESCALA_EXPORTACION)
        for (const trazo of trazosRef.current) dibujarTrazo(ctx, trazo)
        return dataUrlABlob(salida.toDataURL("image/png"))
      },
    }))

    return (
      <div className={className}>
        <canvas
          ref={canvasRef}
          role="application"
          aria-label="Lienzo de firma: firma con el ratón, el dedo o un lápiz óptico"
          className="w-full touch-none rounded-md border bg-white"
          // La proporción del cuadro real del PDF, no un alto fijo -- así lo
          // dibujado mapea 1:1 sobre la caja sin deformarse (ver
          // FirmarActaDialog.tsx y actas-pdf.ts).
          style={{ aspectRatio: `1 / ${proporcion}` }}
          onPointerDown={iniciarTrazo}
          onPointerMove={continuarTrazo}
          onPointerUp={terminarTrazo}
          onPointerLeave={terminarTrazo}
          onPointerCancel={terminarTrazo}
        />
        <p className="mt-1 text-xs text-muted-foreground">
          Firma sobre la raya. La cédula y la fecha se ponen solas.
        </p>
        <div className="mt-1.5 flex gap-2">
          <Button type="button" size="sm" variant="outline" onClick={deshacer} disabled={!hayTrazos}>
            Deshacer
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={limpiar} disabled={!hayTrazos}>
            Limpiar
          </Button>
        </div>
      </div>
    )
  }
)
