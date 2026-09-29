import { useState } from "react"
import { Plus, RefreshCw } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { cn } from "@/lib/utils"

import { ESTILO_ORIGEN } from "./excepciones-utils"
import { DiaExcepcionDialog } from "./DiaExcepcionDialog"
import { NuevaExcepcionDialog } from "./NuevaExcepcionDialog"
import { ReglasCard } from "./ReglasCard"
import { VistaCalendarioExcepciones } from "./VistaCalendarioExcepciones"
import { VistaListaExcepciones } from "./VistaListaExcepciones"

const VISTA_STORAGE_KEY = "dashboard-lacs-excepciones-vista"

function vistaInicial(): "calendario" | "lista" {
  try {
    const guardada = localStorage.getItem(VISTA_STORAGE_KEY)
    if (guardada === "calendario" || guardada === "lista") return guardada
  } catch {
    // localStorage no disponible
  }
  return "calendario"
}

function LeyendaColor({ clase, etiqueta }: { clase: string; etiqueta: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className={cn("size-2.5 rounded-sm border", clase)} />
      {etiqueta}
    </span>
  )
}

export function ExcepcionesPanel() {
  const [vista, setVista] = useState<"calendario" | "lista">(vistaInicial)
  // Se incrementa con cada cambio (reglas, excepción nueva o quitada) para
  // que las vistas y la tarjeta de reglas vuelvan a pedir datos frescos.
  const [version, setVersion] = useState(0)
  const [fechaSeleccionada, setFechaSeleccionada] = useState<string | null>(null)
  const [mostrarNueva, setMostrarNueva] = useState(false)

  function cambiarVista(nueva: string) {
    setVista(nueva as "calendario" | "lista")
    try {
      localStorage.setItem(VISTA_STORAGE_KEY, nueva)
    } catch {
      // localStorage no disponible
    }
  }

  function onCambio() {
    setVersion((v) => v + 1)
  }

  return (
    <div className="@container space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold tracking-tight">
          Excepciones: fechas sin reservas
        </h1>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => setMostrarNueva(true)}>
            <Plus />
            Añadir fecha
          </Button>
          <Button size="sm" variant="outline" onClick={onCambio}>
            <RefreshCw />
            Refrescar
          </Button>
        </div>
      </div>

      <ReglasCard version={version} onCambio={onCambio} />

      <Tabs value={vista} onValueChange={cambiarVista}>
        <TabsList>
          <TabsTrigger value="calendario">Calendario</TabsTrigger>
          <TabsTrigger value="lista">Lista</TabsTrigger>
        </TabsList>
        <TabsContent value="calendario">
          <VistaCalendarioExcepciones
            version={version}
            onSeleccionarFecha={setFechaSeleccionada}
          />
        </TabsContent>
        <TabsContent value="lista">
          <VistaListaExcepciones
            version={version}
            onCambio={onCambio}
            onSeleccionarFecha={setFechaSeleccionada}
          />
        </TabsContent>
      </Tabs>

      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        <LeyendaColor clase={ESTILO_ORIGEN.dia_semana.celda} etiqueta="Día no hábil (regla)" />
        <LeyendaColor clase={ESTILO_ORIGEN.festivo.celda} etiqueta="Festivo" />
        <LeyendaColor clase={ESTILO_ORIGEN.manual.celda} etiqueta="Bloqueo manual" />
        <LeyendaColor clase={ESTILO_ORIGEN.desbloqueada.celda} etiqueta="Desbloqueada" />
        <span className="ml-auto">
          Fechas en hora de la LACS. El bot rechaza reservas que empiecen o terminen en un día
          bloqueado; el dashboard no bloquea ediciones.
        </span>
      </div>

      {fechaSeleccionada && (
        <DiaExcepcionDialog
          fecha={fechaSeleccionada}
          onClose={() => setFechaSeleccionada(null)}
          onCambio={onCambio}
        />
      )}

      {mostrarNueva && (
        <NuevaExcepcionDialog onClose={() => setMostrarNueva(false)} onCambio={onCambio} />
      )}
    </div>
  )
}
