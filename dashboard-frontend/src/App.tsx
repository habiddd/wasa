import { useState, type ComponentType } from "react"
import {
  Activity,
  BarChart3,
  Boxes,
  CalendarClock,
  CalendarDays,
  CalendarOff,
  ChevronRight,
  ClipboardList,
  ConciergeBell,
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
  SidebarFooter,
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
import { usePolling } from "@/hooks/use-polling"
import { ActivosPanel } from "@/features/activos/ActivosPanel"
import { ActivosProvider } from "@/features/activos/activos-context"
import { LugaresPanel } from "@/features/activos/LugaresPanel"
import { CalendarioPanel } from "@/features/calendario/CalendarioPanel"
import { ExcepcionesPanel } from "@/features/excepciones/ExcepcionesPanel"
import { HoyPanel } from "@/features/hoy/HoyPanel"
import { MetricasPanel } from "@/features/metricas/MetricasPanel"
import { ReservasPanel } from "@/features/reservas/ReservasPanel"
import { EstadoServiciosPanel, QrWhatsappPanel } from "@/features/servicios/ServiciosPanel"
import { SesionesTab } from "@/features/usuarios/SesionesTab"
import { ListaUsuariosPanel } from "@/features/usuarios/UsuariosPanel"
import { fetchStatus, type EstadoServicio } from "@/lib/api"
import { cn } from "@/lib/utils"

// -v2: al estrenar la vista "Hoy" todos aterrizan una vez en ella; después se
// vuelve a recordar la última pestaña como antes.
const TAB_STORAGE_KEY = "dashboard-lacs-tab-activa-v2"

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
  /** Indicador sobre el icono del grupo (ej. salud de los servicios). */
  Adorno?: ComponentType
}

/**
 * Punto de salud sobre el icono de Servicios: verde si todo responde, rojo si
 * algo falla. Se ve también con el menú retraído, que es cuando más sirve.
 */
function PuntoSalud() {
  const { data } = usePolling(fetchStatus, 30000)
  if (!data) return null
  const fallas = Object.values(data).filter(
    (v): v is EstadoServicio => typeof v === "object" && v !== null && !v.ok
  ).length
  const texto = fallas
    ? `${fallas} servicio${fallas === 1 ? "" : "s"} con falla`
    : "Todos los servicios funcionando"
  return (
    <>
      <span
        aria-hidden
        title={texto}
        className={cn(
          "pointer-events-none absolute top-1.5 left-5 size-2 rounded-full ring-2 ring-sidebar",
          fallas ? "bg-destructive" : "bg-success"
        )}
      />
      <span className="sr-only">{texto}</span>
    </>
  )
}

const MENU: (ItemMenu | GrupoMenu)[] = [
  { value: "hoy", label: "Hoy", icon: ConciergeBell, Panel: HoyPanel },
  { value: "metricas", label: "Métricas", icon: BarChart3, Panel: MetricasPanel },
  { value: "calendario", label: "Calendario", icon: CalendarDays, Panel: CalendarioPanel },
  { value: "reservas", label: "Reservas", icon: ClipboardList, Panel: ReservasPanel },
  { value: "excepciones", label: "Excepciones", icon: CalendarOff, Panel: ExcepcionesPanel },
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

// La infraestructura no es el trabajo del mostrador: vive al pie del menú y
// solo pide atención con su punto rojo.
const MENU_PIE: (ItemMenu | GrupoMenu)[] = [
  {
    value: "servicios",
    label: "Servicios",
    icon: Activity,
    Adorno: PuntoSalud,
    children: [
      { value: "servicios-estado", label: "Estado", icon: ListChecks, Panel: EstadoServiciosPanel },
      { value: "servicios-qr", label: "QR / WhatsApp", icon: QrCode, Panel: QrWhatsappPanel },
    ],
  },
]

function esGrupo(item: ItemMenu | GrupoMenu): item is GrupoMenu {
  return "children" in item
}

const TABS: ItemMenu[] = [...MENU, ...MENU_PIE].flatMap((item) =>
  esGrupo(item) ? item.children : [item]
)

function tabInicial() {
  try {
    const guardada = localStorage.getItem(TAB_STORAGE_KEY)
    if (guardada && TABS.some((t) => t.value === guardada)) return guardada
  } catch {
    // localStorage no disponible
  }
  return "hoy"
}

function MenuLateral({
  items,
  tab,
  onTabChange,
}: {
  items: (ItemMenu | GrupoMenu)[]
  tab: string
  onTabChange: (value: string) => void
}) {
  const { state, isMobile } = useSidebar()
  const colapsado = state === "collapsed" && !isMobile

  const [gruposAbiertos, setGruposAbiertos] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(
      items.filter(esGrupo).map((grupo) => [
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
      {items.map((item) =>
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
                  className="relative h-9"
                  onClick={() => {
                    if (colapsado) irAlGrupo(item)
                  }}
                >
                  <item.icon />
                  {item.Adorno && <item.Adorno />}
                  <span>{item.label}</span>
                  <ChevronRight className="ml-auto transition-transform group-data-[state=open]/collapsible:rotate-90" />
                </SidebarMenuButton>
              </CollapsibleTrigger>
              <CollapsibleContent>
                <SidebarMenuSub>
                  {item.children.map((child) => (
                    <SidebarMenuSubItem key={child.value}>
                      {/* asChild + <button>: el <a> sin href de shadcn no entra
                          en el orden de tabulación ni se anuncia como control. */}
                      <SidebarMenuSubButton asChild isActive={tab === child.value}>
                        <button
                          type="button"
                          className="w-full text-left"
                          aria-current={tab === child.value ? "page" : undefined}
                          onClick={() => onTabChange(child.value)}
                        >
                          <child.icon />
                          <span>{child.label}</span>
                        </button>
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
              className="h-9"
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
                <div className="flex items-center justify-between gap-2 px-2 py-1.5 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0">
                  <div className="group-data-[collapsible=icon]:hidden">
                    <p className="text-sm font-semibold">LACS Reservas Bot</p>
                    <p className="text-xs text-muted-foreground">Panel de monitoreo</p>
                  </div>
                  <SidebarTrigger variant="outline" className="shrink-0" />
                </div>
              </SidebarHeader>
              <SidebarContent>
                <MenuLateral items={MENU} tab={tab} onTabChange={handleTabChange} />
              </SidebarContent>
              {/* px-0: el menú del pie se alinea con el de arriba (SidebarContent
                  no tiene padding lateral); solo el conmutador conserva el suyo. */}
              <SidebarFooter className="items-start px-0 group-data-[collapsible=icon]:items-center">
                <MenuLateral items={MENU_PIE} tab={tab} onTabChange={handleTabChange} />
                <div className="px-2">
                  <ThemeToggle />
                </div>
              </SidebarFooter>
            </Sidebar>

            <SidebarInset>
              {/* En móvil el sidebar es un panel flotante: sin este botón no habría cómo abrirlo. */}
              <SidebarTrigger variant="outline" className="m-4 mb-0 md:hidden" />

              <div className="mx-auto w-full max-w-6xl p-6 2xl:max-w-7xl min-[1800px]:max-w-none">
                <activa.Panel />
              </div>
            </SidebarInset>
          </TooltipProvider>
        </SidebarProvider>
      </ActivosProvider>
      <Toaster />
    </ThemeProvider>
  )
}

export default App
