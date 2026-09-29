import { useState } from "react"
import { cn } from "cn"
import { Popover as PopoverPrimitive } from "radix-ui"
import { ChevronDownIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"

type Opcion = { valor: string; etiqueta: string }

function normalizar(texto: string) {
  return texto.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase()
}

export function FiltroMultiple({
  opciones,
  seleccion,
  onChange,
  textoTodos,
  resumen,
  className,
}: {
  opciones: Opcion[]
  seleccion: string[]
  onChange: (seleccion: string[]) => void
  textoTodos: string
  resumen?: string
  className?: string
}) {
  const [busqueda, setBusqueda] = useState("")

  const visibles = opciones.filter((o) => normalizar(o.etiqueta).includes(normalizar(busqueda)))

  const primera = opciones.find((o) => seleccion.includes(o.valor))
  const etiqueta =
    resumen ??
    (!seleccion.length || seleccion.length === opciones.length
      ? textoTodos
      : seleccion.length === 1
        ? (primera?.etiqueta ?? seleccion[0])
        : `${primera?.etiqueta ?? seleccion[0]} +${seleccion.length - 1}`)

  // Se emite en el orden de `opciones` para que la etiqueta "X +n" sea estable.
  function emitir(valores: Set<string>) {
    onChange(opciones.filter((o) => valores.has(o.valor)).map((o) => o.valor))
  }

  function alternar(valor: string) {
    const nuevos = new Set(seleccion)
    if (!nuevos.delete(valor)) nuevos.add(valor)
    emitir(nuevos)
  }

  function marcarVisibles(marcar: boolean) {
    const nuevos = new Set(seleccion)
    for (const o of visibles) {
      if (marcar) nuevos.add(o.valor)
      else nuevos.delete(o.valor)
    }
    emitir(nuevos)
  }

  return (
    <PopoverPrimitive.Root onOpenChange={(abierto) => !abierto && setBusqueda("")}>
      <PopoverPrimitive.Trigger asChild>
        <button
          type="button"
          className={cn(
            "flex h-7 w-fit items-center justify-between gap-1.5 rounded-[min(var(--radius-md),10px)] border border-input bg-transparent py-2 pr-2 pl-2.5 text-sm whitespace-nowrap transition-colors outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30 dark:hover:bg-input/50 [&_svg]:pointer-events-none [&_svg]:shrink-0",
            className
          )}
        >
          <span className="truncate">{etiqueta}</span>
          <ChevronDownIcon className="size-4 text-muted-foreground" />
        </button>
      </PopoverPrimitive.Trigger>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Content
          align="start"
          sideOffset={4}
          className="z-50 w-64 space-y-2 rounded-lg bg-popover p-2 text-popover-foreground shadow-md ring-1 ring-foreground/10 outline-none"
        >
          <Input
            autoFocus
            placeholder="Buscar…"
            value={busqueda}
            onChange={(e) => setBusqueda(e.target.value)}
          />
          <div className="max-h-64 overflow-y-auto">
            {visibles.length ? (
              visibles.map((o) => (
                <label
                  key={o.valor}
                  className="flex cursor-pointer items-center gap-2 rounded-md px-1.5 py-1 text-sm hover:bg-accent hover:text-accent-foreground"
                >
                  {/* after:hidden: el área de clic ampliada del checkbox desborda
                      la lista y saca scroll; la fila entera ya es clicable. */}
                  <Checkbox
                    className="after:hidden"
                    checked={seleccion.includes(o.valor)}
                    onCheckedChange={() => alternar(o.valor)}
                  />
                  <span className="truncate">{o.etiqueta}</span>
                </label>
              ))
            ) : (
              <p className="px-1.5 py-1 text-sm text-muted-foreground">Sin resultados</p>
            )}
          </div>
          <div className="flex justify-end gap-1">
            <Button variant="ghost" size="sm" onClick={() => marcarVisibles(true)}>
              Todos
            </Button>
            <Button variant="ghost" size="sm" onClick={() => marcarVisibles(false)}>
              Ninguno
            </Button>
          </div>
        </PopoverPrimitive.Content>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  )
}
