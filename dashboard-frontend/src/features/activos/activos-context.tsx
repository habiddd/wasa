import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from "react"

import {
  actualizarActivo,
  crearActivo,
  fetchActivos,
  type CambiosActivo,
  type Recurso,
} from "@/lib/api"

const SUBTAB_STORAGE_KEY = "dashboard-cpa-activos-subtab"

type ActivosContextValue = {
  activos: Recurso[]
  loading: boolean
  error: string | null
  reload: () => Promise<void>
  crear: (cambios: CambiosActivo) => Promise<Recurso>
  actualizar: (id: number, cambios: CambiosActivo) => Promise<Recurso>
  expandidos: Set<number>
  toggleExpandido: (id: number) => void
  expandir: (id: number) => void
  subtabActiva: string
  setSubtabActiva: (clave: string) => void
}

const ActivosContext = createContext<ActivosContextValue | null>(null)

export function ActivosProvider({ children }: { children: ReactNode }) {
  const [activos, setActivos] = useState<Recurso[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [expandidos, setExpandidos] = useState<Set<number>>(new Set())
  const [subtabActiva, setSubtabActivaState] = useState(() => {
    try {
      return localStorage.getItem(SUBTAB_STORAGE_KEY) || "__todos__"
    } catch {
      return "__todos__"
    }
  })

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchActivos()
      setActivos(data)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  const crear = useCallback(
    async (cambios: CambiosActivo) => {
      const nuevo = await crearActivo(cambios)
      await reload()
      return nuevo
    },
    [reload]
  )

  const actualizar = useCallback(
    async (id: number, cambios: CambiosActivo) => {
      const actualizado = await actualizarActivo(id, cambios)
      await reload()
      return actualizado
    },
    [reload]
  )

  const toggleExpandido = useCallback((id: number) => {
    setExpandidos((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const expandir = useCallback((id: number) => {
    setExpandidos((prev) => new Set(prev).add(id))
  }, [])

  const setSubtabActiva = useCallback((clave: string) => {
    setSubtabActivaState(clave)
    try {
      localStorage.setItem(SUBTAB_STORAGE_KEY, clave)
    } catch {
      // localStorage no disponible
    }
  }, [])

  return (
    <ActivosContext.Provider
      value={{
        activos,
        loading,
        error,
        reload,
        crear,
        actualizar,
        expandidos,
        toggleExpandido,
        expandir,
        subtabActiva,
        setSubtabActiva,
      }}
    >
      {children}
    </ActivosContext.Provider>
  )
}

export function useActivosContext() {
  const ctx = useContext(ActivosContext)
  if (!ctx) throw new Error("useActivosContext must be used within ActivosProvider")
  return ctx
}
