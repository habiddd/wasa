import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { fetchReservasVentana, type Sesion, type VentanaReservas } from "@/lib/api"

// Orden de anchura de cada ventana: sirve para decidir, al cambiar de
// ventana, si los datos ya cacheados alcanzan (estrechar, se filtra en
// cliente) o hace falta pedirle más al servidor (ensanchar).
const ANCHURA: Record<VentanaReservas, number> = {
  "24h": 0,
  "7d": 1,
  "30d": 2,
  "1a": 3,
  todas: 4,
}

// Cuánto hacia atrás llega cada ventana, en ms, para recortar en cliente con
// la MISMA regla que el backend (`fin > ahora - delta`, ver VENTANAS_RESERVAS
// en dashboard.py). "todas" no tiene delta: nunca hace falta recortarla.
const DELTA_MS: Record<Exclude<VentanaReservas, "todas">, number> = {
  "24h": 24 * 60 * 60 * 1000,
  "7d": 7 * 24 * 60 * 60 * 1000,
  "30d": 30 * 24 * 60 * 60 * 1000,
  "1a": 365 * 24 * 60 * 60 * 1000,
}

// El histórico viejo es inmutable: sondear "año"/"todas" cada 30 s como las
// demás sería gasto puro. Esas dos se refrescan cada 2 min; el resto, cada 30 s.
const INTERVALO_MS: Record<VentanaReservas, number> = {
  "24h": 30_000,
  "7d": 30_000,
  "30d": 30_000,
  "1a": 120_000,
  todas: 120_000,
}

/** `fin > ahora - delta`, recalculando `ahora` en cada llamada (no al montar).
 * Con `incluirAbiertas`, los préstamos que siguen afuera no se recortan (misma
 * regla que el backend con ?incluir_abiertas=1). */
function recortarAVentana(
  reservas: Sesion[],
  ventana: VentanaReservas,
  incluirAbiertas: boolean
): Sesion[] {
  if (ventana === "todas") return reservas
  const corte = Date.now() - DELTA_MS[ventana]
  return reservas.filter(
    (r) =>
      !r.fin ||
      new Date(r.fin).getTime() > corte ||
      (incluirAbiertas && (r.estado === "entregada" || r.estado === "no_devuelta"))
  )
}

type Cache = {
  ventana: VentanaReservas
  reservas: Sesion[]
  truncado: boolean
}

/**
 * Trae las reservas de una ventana temporal, con caché en cliente y polling
 * adaptativo (ver docs/ARQUITECTURA.md, "Filtro temporal del panel de
 * Reservas"). Sacado de ReservasPanel.tsx a su propio hook porque la lógica
 * de caché + carreras + intervalo variable no cabe cómoda en el componente.
 */
export function useReservasVentana(ventana: VentanaReservas, incluirAbiertas = false) {
  const [cache, setCache] = useState<Cache | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Solo true cuando todavía no hay ningún dato que mostrar (primera carga).
  const [cargando, setCargando] = useState(true)
  // True mientras llega una ventana más ancha que la que ya está en caché.
  const [refrescando, setRefrescando] = useState(false)

  const cacheRef = useRef<Cache | null>(null)
  cacheRef.current = cache

  // Contador de peticiones en vuelo: si el usuario cambia de ventana antes de
  // que responda una petición anterior, su resultado queda "viejo" (el id ya
  // no coincide con el de la última petición) y se descarta. Esto es lo que
  // evita que una respuesta lenta de "todas" pise la vista de "24h".
  const idPeticionRef = useRef(0)

  const pedir = useCallback((ventanaPedida: VentanaReservas) => {
    const idPeticion = ++idPeticionRef.current
    if (cacheRef.current) {
      setRefrescando(true)
    } else {
      setCargando(true)
    }

    fetchReservasVentana(ventanaPedida, incluirAbiertas)
      .then((resp) => {
        if (idPeticion !== idPeticionRef.current) return // respuesta obsoleta
        setCache({ ventana: resp.ventana, reservas: resp.reservas, truncado: resp.truncado })
        setError(null)
      })
      .catch((err) => {
        if (idPeticion !== idPeticionRef.current) return
        setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (idPeticion !== idPeticionRef.current) return
        setCargando(false)
        setRefrescando(false)
      })
  }, [incluirAbiertas])

  useEffect(() => {
    const previa = cacheRef.current
    // Sin caché todavía, o la ventana nueva es más ancha que la cacheada: no
    // alcanza con filtrar en cliente, hay que pedirle al servidor ya mismo.
    // Si es igual o más estrecha, lo cacheado ya la cubre -- no se pide nada
    // acá, el próximo tick del polling de abajo la pedirá con la ventana
    // nueva (más barata que la que se estaba pidiendo antes).
    if (!previa || ANCHURA[ventana] > ANCHURA[previa.ventana]) {
      pedir(ventana)
    }

    const id = setInterval(() => pedir(ventana), INTERVALO_MS[ventana])
    return () => clearInterval(id)
  }, [ventana, pedir])

  const reservas = useMemo(() => {
    if (!cache) return []
    return recortarAVentana(cache.reservas, ventana, incluirAbiertas)
  }, [cache, ventana, incluirAbiertas])

  // El aviso de truncado solo vale para la ventana que efectivamente se pidió
  // al servidor: si se está mostrando un recorte en cliente de una caché más
  // ancha (o todavía no llegó la respuesta de una más ancha), no sabemos si
  // ESTA ventana está truncada, así que no se avisa de algo que no se verificó.
  const truncado = cache !== null && cache.ventana === ventana ? cache.truncado : false

  const reload = useCallback(() => pedir(ventana), [pedir, ventana])

  return { reservas, cargando, refrescando, error, truncado, reload }
}
