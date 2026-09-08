import { useState } from "react"
import { Toaster } from "sonner"

import { ThemeProvider } from "@/components/theme-provider"
import { ThemeToggle } from "@/components/theme-toggle"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ActivosPanel } from "@/features/activos/ActivosPanel"
import { ActivosProvider } from "@/features/activos/activos-context"
import { LugaresPanel } from "@/features/activos/LugaresPanel"
import { MetricasPanel } from "@/features/metricas/MetricasPanel"
import { QrPanel } from "@/features/qr/QrPanel"
import { ServiciosPanel } from "@/features/servicios/ServiciosPanel"
import { UsuariosPanel } from "@/features/usuarios/UsuariosPanel"

const TAB_STORAGE_KEY = "dashboard-cpa-tab-activa"
const TABS = [
  { value: "servicios", label: "Servicios" },
  { value: "qr", label: "QR / WhatsApp" },
  { value: "metricas", label: "Métricas" },
  { value: "usuarios", label: "Usuarios" },
  { value: "activos", label: "Activos" },
  { value: "lugares", label: "Lugares" },
] as const

function tabInicial() {
  try {
    const guardada = localStorage.getItem(TAB_STORAGE_KEY)
    if (guardada && TABS.some((t) => t.value === guardada)) return guardada
  } catch {
    // localStorage no disponible
  }
  return "servicios"
}

function App() {
  const [tab, setTab] = useState(tabInicial)

  function handleTabChange(value: string) {
    setTab(value)
    try {
      localStorage.setItem(TAB_STORAGE_KEY, value)
    } catch {
      // localStorage no disponible
    }
  }

  return (
    <ThemeProvider>
      <ActivosProvider>
        <div className="min-h-screen bg-background text-foreground">
          <header className="flex items-start justify-between gap-4 border-b px-6 py-5">
            <div>
              <h1 className="text-lg font-semibold">Dashboard — CPA Reservas Bot</h1>
              <p className="text-sm text-muted-foreground">
                Estado de servicios, vinculación de WhatsApp y métricas de uso.
              </p>
            </div>
            <ThemeToggle />
          </header>

          <main className="mx-auto max-w-6xl p-6">
            <Tabs value={tab} onValueChange={handleTabChange}>
              <TabsList>
                {TABS.map((t) => (
                  <TabsTrigger key={t.value} value={t.value}>
                    {t.label}
                  </TabsTrigger>
                ))}
              </TabsList>

              <TabsContent value="servicios">
                <ServiciosPanel />
              </TabsContent>
              <TabsContent value="qr">
                <QrPanel />
              </TabsContent>
              <TabsContent value="metricas">
                <MetricasPanel />
              </TabsContent>
              <TabsContent value="usuarios">
                <UsuariosPanel />
              </TabsContent>
              <TabsContent value="activos">
                <ActivosPanel />
              </TabsContent>
              <TabsContent value="lugares">
                <LugaresPanel />
              </TabsContent>
            </Tabs>
          </main>
        </div>
      </ActivosProvider>
      <Toaster />
    </ThemeProvider>
  )
}

export default App
