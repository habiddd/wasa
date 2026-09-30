"""Agrega un diagrama Mermaid a cada nota de la bóveda de Obsidian que exporta graphify.

- Nota de nodo: el nodo y hasta MAX_VECINOS vecinos directos (primero los EXTRACTED), con la
  relación en la línea. Línea continua = EXTRACTED, punteada = INFERRED/AMBIGUOUS.
- Nota _COMMUNITY_: los MAX_MIEMBROS miembros más conectados y las aristas entre ellos.

Lee las propias notas (sección "## Connections" / "## Members"), así que los nombres de
archivo son los que graphify ya escribió. Es idempotente: reemplaza el bloque anterior.
Cada caja del diagrama es clicable en Obsidian (class internal-link).

Uso:  python scripts/graphify_mermaid.py C:/Users/WinterOS/Desktop/Nueva
"""
import re
import sys
from pathlib import Path

MAX_VECINOS = 12
MAX_MIEMBROS = 15
INICIO, FIN = "%% graphify-mermaid:inicio %%", "%% graphify-mermaid:fin %%"
BLOQUE = re.compile(re.escape(INICIO) + r".*?" + re.escape(FIN) + r"\n*", re.S)
CONEXION = re.compile(r"^- \[\[(.+?)\]\] - `([^`]*)` \[(\w+)\]", re.M)
MIEMBRO = re.compile(r"^- \[\[(.+?)\]\]", re.M)


def _texto(nombre: str) -> str:
    return nombre.replace('"', "#quot;").replace("`", "'")


def _diagrama(nodos: list[str], aristas: list[tuple[str, str, str, str]], centro: str | None) -> str:
    ids = {n: f"n{i}" for i, n in enumerate(nodos)}
    lineas = [INICIO, "```mermaid", "graph LR"]
    lineas += [f'  {ids[n]}["{_texto(n)}"]' for n in nodos]
    for a, b, rel, conf in aristas:
        # Sin punta de flecha: el grafo de graphify no tiene dirección. Punteada = INFERRED/AMBIGUOUS.
        rel = rel or "rel"
        enlace = f"---|{rel}|" if conf == "EXTRACTED" else f"-. {rel} .-"
        lineas.append(f"  {ids[a]} {enlace} {ids[b]}")
    lineas.append(f"  class {','.join(ids.values())} internal-link;")
    if centro:
        lineas.append(f"  style {ids[centro]} stroke-width:3px")
    lineas += ["```", FIN, ""]
    return "\n".join(lineas)


def _insertar(texto: str, bloque: str) -> str:
    texto = BLOQUE.sub("", texto)
    m = re.search(r"^# .*\n\n?", texto, re.M)
    if not m:
        return texto
    return texto[: m.end()] + bloque + "\n" + texto[m.end():]


def main(boveda: Path) -> None:
    notas = {p.stem: p for p in boveda.glob("*.md")}
    textos = {n: p.read_text(encoding="utf-8") for n, p in notas.items()}
    conexiones = {
        n: [(dst, rel, conf) for dst, rel, conf in CONEXION.findall(t) if dst != n]
        for n, t in textos.items()
        if not n.startswith("_COMMUNITY_")
    }
    grado = {n: len(c) for n, c in conexiones.items()}
    # ponytail: el orden de vecinos es solo por grado; si se quieren los "más relevantes", ponderar por confianza.
    for nombre, texto in textos.items():
        if nombre.startswith("_COMMUNITY_"):
            miembros = [m for m in MIEMBRO.findall(texto.split("## Members", 1)[-1]) if m in conexiones]
            top = sorted(dict.fromkeys(miembros), key=lambda m: -grado[m])[:MAX_MIEMBROS]
            dentro = set(top)
            vistos, aristas = set(), []
            for a in top:
                for b, rel, conf in conexiones[a]:
                    if b in dentro and frozenset((a, b)) not in vistos:
                        vistos.add(frozenset((a, b)))
                        aristas.append((a, b, rel, conf))
            if not top:
                continue
            bloque = _diagrama(top, aristas, None)
        else:
            vecinos = sorted(conexiones[nombre], key=lambda c: (c[2] != "EXTRACTED", -grado.get(c[0], 0)))
            vecinos = list({c[0]: c for c in vecinos}.values())[:MAX_VECINOS]
            if not vecinos:
                continue
            bloque = _diagrama([nombre] + [v[0] for v in vecinos],
                               [(nombre, dst, rel, conf) for dst, rel, conf in vecinos], nombre)
        nuevo = _insertar(texto, bloque)
        if nuevo != texto:
            notas[nombre].write_text(nuevo, encoding="utf-8")
    print(f"Diagramas Mermaid aplicados en {boveda}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "graphify-out/obsidian"))
