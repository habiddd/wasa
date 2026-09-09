import { useState } from "react"

import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

// Centinelas para las dos opciones especiales del Select: crear un valor
// nuevo (modo texto libre) o, cuando el campo es opcional, dejarlo vacío.
const VALOR_NUEVA = "__nueva__"
const VALOR_SIN = "__sin__"

// Combina un Select (con los valores ya usados en el catálogo, para no
// duplicar categorías/subcategorías por un typo) con un modo de texto libre
// para dar de alta un valor que todavía no existe. Se usa para Categoría y
// Subcategoría en los forms de alta/edición de activos.
export function CampoCategoria({
  id,
  valor,
  opciones,
  onChange,
  placeholder,
  textoNueva,
  textoSinValor,
  required,
}: {
  id: string
  valor: string
  opciones: string[]
  onChange: (valor: string) => void
  placeholder: string
  textoNueva: string
  // Si se pasa, el Select ofrece una opción para dejar el campo vacío
  // (ej. "— Sin subcategoría —"). Si no se pasa, el campo es obligatorio
  // y no hay forma de dejarlo en blanco desde el Select.
  textoSinValor?: string
  required?: boolean
}) {
  const yaExiste = valor === "" || opciones.includes(valor)
  // Si el valor guardado no está entre los conocidos (categoría/subcategoría
  // recién escrita a mano), el campo arranca directo en modo texto libre.
  const [modoLibre, setModoLibre] = useState(!yaExiste)

  if (modoLibre) {
    return (
      <div className="flex flex-col gap-1">
        <Input
          id={id}
          autoFocus
          value={valor}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          required={required}
        />
        <button
          type="button"
          className="self-start text-xs text-muted-foreground underline-offset-2 hover:underline"
          onClick={() => setModoLibre(false)}
        >
          Elegir una existente
        </button>
      </div>
    )
  }

  const valorSelect = valor === "" ? (textoSinValor ? VALOR_SIN : "") : valor

  function handleChange(v: string) {
    if (v === VALOR_NUEVA) {
      setModoLibre(true)
      onChange("")
      return
    }
    if (v === VALOR_SIN) {
      onChange("")
      return
    }
    onChange(v)
  }

  return (
    <Select value={valorSelect} onValueChange={handleChange}>
      <SelectTrigger id={id} className="w-full">
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {textoSinValor && <SelectItem value={VALOR_SIN}>{textoSinValor}</SelectItem>}
        {opciones.map((o) => (
          <SelectItem key={o} value={o}>
            {o}
          </SelectItem>
        ))}
        <SelectItem value={VALOR_NUEVA}>{textoNueva}</SelectItem>
      </SelectContent>
    </Select>
  )
}
