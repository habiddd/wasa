import { useState } from "react"
import { Toaster } from "sonner"

import { ThemeProvider } from "@/components/theme-provider"
import { ThemeToggle } from "@/components/theme-toggle"
import {
  Sidebar,
  SidebarContent,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar"
import { ActivosPanel } from "@/features/activos/ActivosPanel"
import { ActivosProvider } from "@/features/activos/activos-context"
import { LugaresPanel } from "@/features/activos/LugaresPanel"
import { MetricasPanel } from "@/features/metricas/MetricasPanel"
import { ServiciosPanel } from "@/features/servicios/ServiciosPanel"
import { UsuariosPanel } from "@/features/usuarios/UsuariosPanel"

const TAB_STORAGE_KEY = "dashboard-cpa-tab-activa"
const TABS = [
  { value: "servicios", label: "Servicios", Panel: ServiciosPanel },
  { value: "metricas", label: "Métricas", Panel: MetricasPanel },
  { value: "usuarios", label: "Usuarios", Panel: UsuariosPanel },
  { value: "activos", label: "Activos", Panel: ActivosPanel },
  { value: "lugares", label: "Lugares", Panel: LugaresPanel },
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

  const activa = TABS.find((t) => t.value === tab) ?? TABS[0]

  return (
    <ThemeProvider>
      <ActivosProvider>
        <SidebarProvider>
          <Sidebar collapsible="offcanvas">
            <SidebarHeader>
              <div className="px-2 py-1.5">
                <p className="text-sm font-semibold">CPA Reservas Bot</p>
                <p className="text-xs text-muted-foreground">Panel de monitoreo</p>
              </div>
            </SidebarHeader>
            <SidebarContent>
              <SidebarMenu className="gap-0.5">
                {TABS.map((t) => (
                  <SidebarMenuItem key={t.value}>
                    <SidebarMenuButton
                      isActive={tab === t.value}
                      onClick={() => handleTabChange(t.value)}
                      className="h-9 text-lg"
                    >
                      {t.label}
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarContent>
          </Sidebar>

          <SidebarInset>
            <header className="flex items-start justify-between gap-3 border-b px-6 py-5">
              <div className="flex items-start gap-3">
                <SidebarTrigger className="mt-1" />
                <div>
                  <h1 className="text-lg font-semibold">Dashboard — CPA Reservas Bot</h1>
                  <p className="text-sm text-muted-foreground">
                    Estado de servicios, vinculación de WhatsApp y métricas de uso.
                  </p>
                </div>
              </div>
              <ThemeToggle />
            </header>

            <main className="mx-auto w-full max-w-6xl p-6">
              <activa.Panel />
            </main>
          </SidebarInset>
        </SidebarProvider>
      </ActivosProvider>
      <Toaster />
    </ThemeProvider>
  )
}

export default App
