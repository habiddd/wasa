import { useState, type ComponentType } from "react"
import {
  Activity,
  BarChart3,
  Boxes,
  CalendarClock,
  CalendarDays,
  ChevronRight,
  ListChecks,
  MapPin,
  QrCode,
  UserRound,
  Users,
  type LucideIcon,
} from "lucide-react"
import { Toaster } from "sonner"

import { ThemeProvider } from "@/components/theme-provider"
import { ThemeToggle } from "@/components/theme-toggle"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  Sidebar,
  SidebarContent,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarProvider,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar"
import { TooltipProvider } from "@/components/ui/tooltip"
import { ActivosPanel } from "@/features/activos/ActivosPanel"
import { ActivosProvider } from "@/features/activos/activos-context"
import { LugaresPanel } from "@/features/activos/LugaresPanel"
import { CalendarioPanel } from "@/features/calendario/CalendarioPanel"
import { MetricasPanel } from "@/features/metricas/MetricasPanel"
import { EstadoServiciosPanel, QrWhatsappPanel } from "@/features/servicios/ServiciosPanel"
import { SesionesTab } from "@/features/usuarios/SesionesTab"
import { ListaUsuariosPanel } from "@/features/usuarios/UsuariosPanel"

const TAB_STORAGE_KEY = "dashboard-cpa-tab-activa"

type ItemMenu = {
  value: string
  label: string
  icon: LucideIcon
  Panel: ComponentType
}

type GrupoMenu = {
  value: string
  label: string
  icon: LucideIcon
  children: ItemMenu[]
}

const MENU: (ItemMenu | GrupoMenu)[] = [
  {
    value: "servicios",
    label: "Servicios",
    icon: Activity,
    children: [
      { value: "servicios-estado", label: "Estado", icon: ListChecks, Panel: EstadoServiciosPanel },
      { value: "servicios-qr", label: "QR / WhatsApp", icon: QrCode, Panel: QrWhatsappPanel },
    ],
  },
  { value: "metricas", label: "Métricas", icon: BarChart3, Panel: MetricasPanel },
  { value: "calendario", label: "Calendario", icon: CalendarDays, Panel: CalendarioPanel },
  {
    value: "usuarios",
    label: "Usuarios",
    icon: Users,
    children: [
      { value: "usuarios-lista", label: "Usuarios", icon: UserRound, Panel: ListaUsuariosPanel },
      { value: "usuarios-sesiones", label: "Sesiones", icon: CalendarClock, Panel: SesionesTab },
    ],
  },
  { value: "activos", label: "Activos", icon: Boxes, Panel: ActivosPanel },
  { value: "lugares", label: "Lugares", icon: MapPin, Panel: LugaresPanel },
]

function esGrupo(item: ItemMenu | GrupoMenu): item is GrupoMenu {
  return "children" in item
}

const TABS: ItemMenu[] = MENU.flatMap((item) => (esGrupo(item) ? item.children : [item]))

function tabInicial() {
  try {
    const guardada = localStorage.getItem(TAB_STORAGE_KEY)
    if (guardada && TABS.some((t) => t.value === guardada)) return guardada
  } catch {
    // localStorage no disponible
  }
  return "servicios-estado"
}

function MenuLateral({
  tab,
  onTabChange,
}: {
  tab: string
  onTabChange: (value: string) => void
}) {
  const { state, isMobile } = useSidebar()
  const colapsado = state === "collapsed" && !isMobile

  const [gruposAbiertos, setGruposAbiertos] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(
      MENU.filter(esGrupo).map((grupo) => [
        grupo.value,
        grupo.children.some((c) => c.value === tab),
      ])
    )
  )

  function setGrupoAbierto(value: string, abierto: boolean) {
    setGruposAbiertos((prev) => ({ ...prev, [value]: abierto }))
  }

  // Con el sidebar retraído el submenú no se ve, así que el clic en el grupo
  // navega directo a su pestaña: la activa del grupo, o la primera.
  function irAlGrupo(grupo: GrupoMenu) {
    const destino = grupo.children.find((c) => c.value === tab) ?? grupo.children[0]
    setGrupoAbierto(grupo.value, true)
    onTabChange(destino.value)
  }

  return (
    <SidebarMenu className="gap-0.5">
      {MENU.map((item) =>
        esGrupo(item) ? (
          <Collapsible
            key={item.value}
            open={gruposAbiertos[item.value] ?? false}
            onOpenChange={(abierto) => {
              // Retraído, el trigger no debe alternar el submenú: solo navega.
              if (!colapsado) setGrupoAbierto(item.value, abierto)
            }}
            className="group/collapsible"
          >
            <SidebarMenuItem>
              <CollapsibleTrigger asChild>
                <SidebarMenuButton
                  isActive={item.children.some((c) => c.value === tab)}
                  tooltip={item.label}
                  className="h-9 text-lg"
                  onClick={() => {
                    if (colapsado) irAlGrupo(item)
                  }}
                >
                  <item.icon />
                  <span>{item.label}</span>
                  <ChevronRight className="ml-auto transition-transform group-data-[state=open]/collapsible:rotate-90" />
                </SidebarMenuButton>
              </CollapsibleTrigger>
              <CollapsibleContent>
                <SidebarMenuSub>
                  {item.children.map((child) => (
                    <SidebarMenuSubItem key={child.value}>
                      <SidebarMenuSubButton
                        isActive={tab === child.value}
                        onClick={() => onTabChange(child.value)}
                      >
                        <child.icon />
                        <span>{child.label}</span>
                      </SidebarMenuSubButton>
                    </SidebarMenuSubItem>
                  ))}
                </SidebarMenuSub>
              </CollapsibleContent>
            </SidebarMenuItem>
          </Collapsible>
        ) : (
          <SidebarMenuItem key={item.value}>
            <SidebarMenuButton
              isActive={tab === item.value}
              onClick={() => onTabChange(item.value)}
              tooltip={item.label}
              className="h-9 text-lg"
            >
              <item.icon />
              <span>{item.label}</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        )
      )}
    </SidebarMenu>
  )
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
          <TooltipProvider>
            <Sidebar collapsible="icon">
              <SidebarHeader>
                <div className="px-2 py-1.5 group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:text-center">
                  <p className="text-sm font-semibold group-data-[collapsible=icon]:hidden">
                    CPA Reservas Bot
                  </p>
                  <p className="text-xs text-muted-foreground group-data-[collapsible=icon]:hidden">
                    Panel de monitoreo
                  </p>
                </div>
              </SidebarHeader>
              <SidebarContent>
                <MenuLateral tab={tab} onTabChange={handleTabChange} />
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
          </TooltipProvider>
        </SidebarProvider>
      </ActivosProvider>
      <Toaster />
    </ThemeProvider>
  )
}

export default App
