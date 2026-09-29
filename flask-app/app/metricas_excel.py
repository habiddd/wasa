"""Excel de métricas con formato, colores y gráficos (openpyxl).

Recibe el mismo JSON que ven las pestañas General y Por uso del dashboard
(`api_metricas` y `api_metricas_uso`), así que los números del archivo son
los de la pantalla. Los colores siguen DESIGN.md: grafito para la estructura
y las barras, y verde / ámbar / rojo solo para el estado de una reserva.
"""

import io
import math
from datetime import date, datetime, timedelta

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.chart.marker import DataPoint
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.drawing.line import LineProperties
from openpyxl.formatting.rule import DataBarRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# Paleta de DESIGN.md en hex (Excel no entiende oklch).
GRAFITO = "171717"
BARRA = "737373"  # grafito al ~60 %, el de los histogramas del panel
LAPIZ = "737373"
NIEBLA = "F5F5F5"
TRAZO = "E5E5E5"
VERDE, VERDE_TINTE, VERDE_TEXTO = "3F9A4F", "E8F3EA", "2F7A3C"
AMBAR, AMBAR_TINTE, AMBAR_TEXTO = "D9A02B", "FBF2DF", "8A5A00"
ROJO, ROJO_TINTE, ROJO_TEXTO = "E7000B", "FDE6E6", "C10007"

ETIQUETA_ESTADO = {
    "pendiente": "Pendiente", "confirmada": "Confirmada", "denegada": "Denegada", "cancelada": "Cancelada",
    "entregada": "Entregada", "completada": "Completada", "devuelta_tarde": "Devuelta tarde",
    "no_show": "No se presentó", "no_devuelta": "No devuelta",
}
# (color de la barra, tinte de la fila, color del texto) por estado, como los chips del calendario.
COLOR_ESTADO = {
    "confirmada": (VERDE, VERDE_TINTE, VERDE_TEXTO),
    "pendiente": (AMBAR, AMBAR_TINTE, AMBAR_TEXTO),
    "no_show": (AMBAR, AMBAR_TINTE, AMBAR_TEXTO),
    "entregada": (GRAFITO, NIEBLA, GRAFITO),
    "cancelada": (ROJO, ROJO_TINTE, ROJO_TEXTO),
    "denegada": (ROJO, ROJO_TINTE, ROJO_TEXTO),
    "no_devuelta": (ROJO, ROJO_TINTE, ROJO_TEXTO),
    "completada": ("A3A3A3", None, None),
    "devuelta_tarde": ("A3A3A3", AMBAR_TINTE, AMBAR_TEXTO),
}
ETIQUETA_INTENT = {
    "reservar": "Reservar", "registro": "Registro", "consultar_disponibilidad": "Consulta de disponibilidad",
    "cancelar": "Cancelar reserva", "confirmar_reserva": "Confirmar reserva", "menu_fallback": "No entendido (menú)",
    "saludo": "Saludo", "ayuda": "Ayuda", "otro": "Otro", "reserva_dashboard": "Reserva creada en el panel",
    "confirmacion_lacs": "Confirmación de la LACS", "confirmacion_cpa": "Confirmación de la LACS",
    "denegacion_lacs": "Denegación de la LACS", "denegacion_cpa": "Denegación de la LACS",
    "aviso_previo": "Aviso previo", "aviso_hora": "Aviso de hora", "aviso_no_show": "Aviso de no presentación",
    "aviso_devolucion": "Aviso de devolución", "oferta_reserva": "Oferta de reserva",
}
ETIQUETA_AVISO = {"previo": "Previo", "hora": "De la hora", "no_show": "No presentación", "devolucion": "Devolución"}
ETIQUETA_CANAL = {"whatsapp": "WhatsApp", "dashboard": "Panel"}
ETIQUETA_ACTIVO = {
    "disponible": "Disponible", "no_disponible": "No disponible", "en_mantenimiento": "En mantenimiento",
    "no_devuelto": "No devuelto",
}
DIAS = ["Domingo", "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado"]
ORDEN_SEMANA = [1, 2, 3, 4, 5, 6, 0]
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sept", "oct", "nov", "dic"]
TELEFONO_PRUEBA = "000000000000"

_ENCABEZADO = Font(bold=True, color="FFFFFF")
_FONDO_ENCABEZADO = PatternFill("solid", fgColor=GRAFITO)
_CEBRA = PatternFill("solid", fgColor=NIEBLA)
_LINEA = Border(bottom=Side(style="thin", color=TRAZO))


def legible(clave, mapa):
    if not clave:
        return "-"
    return mapa.get(clave) or clave.replace("_", " ").capitalize()


def duracion(seg):
    """5400 -> "1 h 30 min"; igual que duracion() del frontend."""
    if seg is None:
        return "—"
    m = round(seg / 60)
    if m < 60:
        return f"{m} min"
    h = m // 60
    if h < 24:
        return f"{h} h {m % 60} min" if m % 60 else f"{h} h"
    return f"{h // 24} d {h % 24} h" if h % 24 else f"{h // 24} d"


def fecha_corta(d: date) -> str:
    return f"{d.day} {MESES[d.month - 1]}"


def telefono(t):
    return "Prueba del bot" if t == TELEFONO_PRUEBA else (t or "-")


def etiqueta_periodo(m: dict) -> str:
    if m["periodo"] == "rango":
        d, h = date.fromisoformat(m["desde"]), date.fromisoformat(m["hasta"])
        return f"del {fecha_corta(d)} {d.year} al {fecha_corta(h)} {h.year}"
    return {"30": "últimos 30 días", "90": "últimos 90 días", "todo": "todo el tiempo"}[m["periodo"]]


def con_dias_vacios(por_dia: list[dict]) -> list[tuple[date, int]]:
    """El backend solo manda días con reservas: se rellenan los huecos con 0 (como en el panel)."""
    if not por_dia:
        return []
    totales = {date.fromisoformat(f["dia"]): f["total"] for f in por_dia}
    d, fin, dias = min(totales), max(totales), []
    while d <= fin:
        dias.append((d, totales.get(d, 0)))
        d += timedelta(days=1)
    return dias


def sumar_por_etiqueta(pares):
    """confirmacion_cpa y confirmacion_lacs comparten etiqueta: una sola fila."""
    totales = {}
    for etiqueta, total in pares:
        totales[etiqueta] = totales.get(etiqueta, 0) + total
    return sorted(totales.items(), key=lambda kv: -kv[1])


class Hoja:
    """Una hoja con título, subtítulo y tablas apiladas; `fila` es la próxima fila libre."""

    def __init__(self, wb: Workbook, titulo: str, subtitulo: str, anchos: list[int]):
        self.ws = wb.create_sheet(titulo[:31])
        self.ws.sheet_view.showGridLines = False
        self.ws["A1"] = titulo
        self.ws["A1"].font = Font(bold=True, size=14, color=GRAFITO)
        self.ws["A2"] = subtitulo
        self.ws["A2"].font = Font(color=LAPIZ)
        for i, ancho in enumerate(anchos, start=1):
            self.ws.column_dimensions[get_column_letter(i)].width = ancho
        self.fila = 4

    def seccion(self, texto: str):
        self.ws.cell(self.fila, 1, texto).font = Font(bold=True, size=12, color=GRAFITO)
        self.fila += 1

    def nota(self, texto: str):
        self.ws.cell(self.fila, 1, texto).font = Font(italic=True, color=LAPIZ)
        self.fila += 1

    def tabla(self, encabezados, filas, formatos=None, barras=(), color_fila=None, rojo_si_positivo=()):
        """Escribe la tabla y devuelve (fila del encabezado, primera, última fila de datos).
        `formatos`: {col: formato numérico}; `barras`: columnas (1-based) con barras de datos;
        `color_fila(i)`: (tinte, color de texto) para pintar la fila i; `rojo_si_positivo`: columnas
        cuyo valor > 0 va en rojo (fallidos, no devueltos)."""
        ws, cab = self.ws, self.fila
        for c, texto in enumerate(encabezados, start=1):
            celda = ws.cell(cab, c, texto)
            celda.font, celda.fill = _ENCABEZADO, _FONDO_ENCABEZADO
            celda.alignment = Alignment(horizontal="left" if c == 1 else "right", vertical="center", wrap_text=True)
        for i, valores in enumerate(filas):
            r = cab + 1 + i
            tinte = color_fila(i) if color_fila else None
            for c, v in enumerate(valores, start=1):
                celda = ws.cell(r, c, v)
                celda.border = _LINEA
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    celda.alignment = Alignment(horizontal="right")
                if formatos and c in formatos:
                    celda.number_format = formatos[c]
                if tinte and tinte[0]:
                    celda.fill = PatternFill("solid", fgColor=tinte[0])
                elif i % 2:
                    celda.fill = _CEBRA
                if tinte and tinte[1] and c == 1:
                    celda.font = Font(bold=True, color=tinte[1])
                if c in rojo_si_positivo and isinstance(v, (int, float)) and v > 0:
                    celda.font = Font(bold=True, color=ROJO_TEXTO)
        ultima = cab + max(len(filas), 1)
        if not filas:
            ws.cell(cab + 1, 1, "Sin datos en este periodo.").font = Font(italic=True, color=LAPIZ)
        for c in barras:
            if filas:
                col = get_column_letter(c)
                ws.conditional_formatting.add(
                    f"{col}{cab + 1}:{col}{ultima}",
                    DataBarRule(start_type="num", start_value=0, end_type="max", color="A3A3A3", showValue=True),
                )
        self.fila = ultima + 2
        # Sin filas, la última es el encabezado: grafico() lo ve y no dibuja nada.
        return cab, cab + 1, ultima if filas else cab

    def grafico(self, titulo, col_cat, col_val, cab, primera, ultima, ancla, *, horizontal=False,
                colores=None, resaltar_max=False, ancho=18, alto=8, etiquetas=True, saltar_eje=1):
        """Barras de una sola serie. `colores`: un color por barra; `resaltar_max`: la más alta en grafito pleno."""
        if ultima < primera:
            return
        ws = self.ws
        ch = BarChart()
        ch.type = "bar" if horizontal else "col"
        ch.title = titulo
        ch.title.overlay = False
        ch.legend = None
        ch.gapWidth = 40
        ch.width, ch.height = ancho, alto
        ch.add_data(Reference(ws, min_col=col_val, min_row=cab, max_row=ultima), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=col_cat, min_row=primera, max_row=ultima))
        serie = ch.series[0]
        serie.graphicalProperties.solidFill = BARRA
        serie.graphicalProperties.line.noFill = True
        serie.invertIfNegative = False
        valores = [ws.cell(r, col_val).value or 0 for r in range(primera, ultima + 1)]
        for i, v in enumerate(valores):
            color = colores[i] if colores else (GRAFITO if resaltar_max and v == max(valores) and v > 0 else None)
            if color:
                punto = DataPoint(idx=i)
                punto.graphicalProperties.solidFill = color
                punto.graphicalProperties.line.noFill = True
                serie.dPt.append(punto)
        if etiquetas:
            # Solo el valor (sin serie, categoría ni clave), y los ceros no se rotulan.
            ch.dataLabels = DataLabelList(showVal=True, showSerName=False, showCatName=False,
                                          showLegendKey=False, showPercent=False, numFmt="0;;;")
        # openpyxl 3.1 oculta los ejes si no se dice lo contrario.
        ch.x_axis.delete = False
        ch.y_axis.delete = False
        ch.y_axis.majorGridlines.spPr = GraphicalProperties(ln=LineProperties(solidFill=TRAZO))
        ch.y_axis.spPr = GraphicalProperties(ln=LineProperties(noFill=True))
        if saltar_eje > 1:
            ch.x_axis.tickLblSkip = saltar_eje
        if horizontal:
            ch.x_axis.scaling.orientation = "maxMin"  # el primero de la tabla arriba
            ch.y_axis.crosses = "max"  # y el eje de valores abajo
        ws.add_chart(ch, ancla)


def _resumen(wb, m, sub):
    h = Hoja(wb, "Resumen", sub, [34, 14, 22])
    r = m["resumen"]
    pct = lambda n: n / r["reservas"] if r["reservas"] else None  # noqa: E731
    h.seccion("Reservas")
    h.tabla(
        ["Métrica", "Valor", "% de las reservas"],
        [
            ["Reservas", r["reservas"], None],
            ["Préstamos", r["prestamos"], pct(r["prestamos"])],
            ["Denegadas", r["denegadas"], pct(r["denegadas"])],
            ["Canceladas", r["canceladas"], pct(r["canceladas"])],
            ["No show", r["no_show"], pct(r["no_show"])],
            ["Devueltas tarde o no devueltas", r["devoluciones_problema"], pct(r["devoluciones_problema"])],
            ["Activos sin devolver hoy", r["no_devueltos_hoy"], None],
            ["Reservas con varios activos", r["grupos_agrupados"], None],
        ],
        formatos={3: "0 %"},
    )
    h.seccion("Puntualidad")
    filas = []
    for nombre, clave in (("Entregas", "entrega"), ("Devoluciones", "devolucion")):
        d = m["puntualidad"].get(clave)
        if d:
            filas.append([nombre, d["a_tiempo"], d["total"], d["a_tiempo"] / d["total"] if d["total"] else None,
                          duracion(d["retraso_seg"]) if d["retraso_seg"] is not None else "ninguna tardía"])
    h.ws.column_dimensions["D"].width = 12
    h.ws.column_dimensions["E"].width = 26
    h.tabla(["Evento", "A tiempo", "Total", "% a tiempo", "Retraso prom. de las tardías"], filas, formatos={4: "0 %"})
    h.seccion("Hábitos")
    h.tabla(
        ["Métrica", "Valor"],
        [["Duración promedio", duracion(m["habitos"]["duracion_seg"])],
         ["Anticipación promedio", duracion(m["habitos"]["anticipacion_seg"])]],
    )
    h.ws.freeze_panes = "A4"


def _estados(wb, m, sub):
    h = Hoja(wb, "Estados", sub, [22, 12, 12])
    total = sum(e["total"] for e in m["estados"]) or 1
    filas = [[legible(e["estado"], ETIQUETA_ESTADO), e["total"], e["total"] / total] for e in m["estados"]]
    colores = [COLOR_ESTADO.get(e["estado"], (BARRA, None, None)) for e in m["estados"]]
    cab, a, b = h.tabla(["Estado", "Reservas", "%"], filas, formatos={3: "0 %"},
                        color_fila=lambda i: (colores[i][1], colores[i][2]))
    h.nota("Colores como en el panel: verde confirmada, ámbar pendiente o no se presentó, rojo cancelada, denegada o no devuelta.")
    h.grafico("Reservas por estado", 1, 2, cab, a, b, "E4", horizontal=True, colores=[c[0] for c in colores])


def _cuando(wb, m, sub):
    h = Hoja(wb, "Cuándo se reserva", sub, [16, 12, 4, 14, 12, 4, 10, 12])
    dias = con_dias_vacios(m["reservas_por_dia"])
    total = sum(t for _, t in dias)
    if dias:
        pico = max(dias, key=lambda x: x[1])
        h.nota(f"{total} reservas · {total / len(dias):.1f} por día en promedio · pico de {pico[1]} el {fecha_corta(pico[0])}".replace(".", ","))
    ws = h.ws
    # Tres tablas lado a lado (A-B día, D-E semana, G-H hora) y los gráficos debajo.
    fila0 = h.fila
    h.tabla(["Día", "Reservas"], [[d, t] for d, t in dias], formatos={1: "d mmm"})
    fin_dia = h.fila
    semana = [[DIAS[k], m["por_dia_semana"][k]] for k in ORDEN_SEMANA]
    horas = [[f"{hh}:00", t] for hh, t in enumerate(m["por_hora"])]
    for col0, encab, filas in ((4, ["Día de la semana", "Reservas"], semana), (7, ["Hora", "Reservas"], horas)):
        for c, texto in enumerate(encab):
            celda = ws.cell(fila0, col0 + c, texto)
            celda.font, celda.fill = _ENCABEZADO, _FONDO_ENCABEZADO
        for i, (e, t) in enumerate(filas):
            for c, v in enumerate((e, t)):
                celda = ws.cell(fila0 + 1 + i, col0 + c, v)
                celda.border = _LINEA
                if i % 2:
                    celda.fill = _CEBRA
    ultima_dia = fila0 + len(dias)  # sin días, grafico() no dibuja nada
    ancla = max(fin_dia, fila0 + 26)
    h.grafico("Reservas por día", 1, 2, fila0, fila0 + 1, ultima_dia, f"J{fila0}", resaltar_max=True,
              ancho=26, alto=8, etiquetas=len(dias) <= 31, saltar_eje=max(1, math.ceil(len(dias) / 12)))
    h.grafico("Por día de la semana", 4, 5, fila0, fila0 + 1, fila0 + 7, f"J{fila0 + 17}", resaltar_max=True, ancho=13)
    h.grafico("Por hora de inicio", 7, 8, fila0, fila0 + 1, fila0 + 24, f"R{fila0 + 17}", resaltar_max=True, ancho=13,
              saltar_eje=2)
    h.fila = ancla


def _tops(wb, m, sub):
    h = Hoja(wb, "Top activos y usuarios", sub, [34, 12, 12, 12, 12, 12, 12, 14])
    for titulo, clave in (("Top 10 equipos", "equipo"), ("Top 10 espacios", "espacio")):
        h.seccion(titulo)
        h.tabla(
            ["Nombre", "Reservas", "Préstamos", "Denegadas", "Canceladas", "No show"],
            [[a["nombre"], a["reservas"], a["prestamos"], a["denegadas"], a["canceladas"], a["no_show"]]
             for a in m["activos"][clave]],
            barras=(2, 3),
        )
    h.seccion("Top 10 usuarios")
    h.tabla(
        ["Nombre", "Teléfono", "Reservas", "Préstamos", "Devueltas tarde", "No show", "No devueltas", "Retraso prom."],
        [[u["nombre"] or "-", telefono(u["telefono_wa"]), u["reservas"], u["prestamos"], u["devueltas_tarde"],
          u["no_show"], u["no_devueltas"], duracion(u["retraso_devolucion_seg"])] for u in m["usuarios"]],
        barras=(3, 4),
        rojo_si_positivo=(7,),
    )
    h.ws.column_dimensions["B"].width = 16


def _inventario(wb, m, sub):
    h = Hoja(wb, "Inventario y seguro", sub, [36, 12])
    inv, seg = m["inventario"], m["seguro"]
    h.seccion("Inventario")
    h.tabla(["Métrica", "Valor"], [
        ["Equipos adicionados", inv["adicionados"]["equipo"]],
        ["Espacios adicionados", inv["adicionados"]["espacio"]],
        ["Bajas en el periodo", inv["dados_de_baja"]["periodo"]],
        ["No disponibles hoy", inv["dados_de_baja"]["hoy"]],
        ["Entradas a mantenimiento", inv["mantenimiento"]["periodo"]],
        ["En mantenimiento hoy", inv["mantenimiento"]["hoy"]],
    ])
    h.seccion("Activos hoy por estado")
    cab, a, b = h.tabla(["Estado", "Activos"],
                        [[legible(k, ETIQUETA_ACTIVO), v] for k, v in inv["total_hoy"].items()], barras=(2,))
    h.grafico("Activos hoy por estado", 1, 2, cab, a, b, "D4", horizontal=True, ancho=14, alto=6)
    h.seccion("Seguro (reservas por grupo)")
    h.tabla(["Métrica", "Valor"], [
        ["Grupos que salen de Barranquilla", seg["salen"]],
        ["Activos en esos grupos", seg["lineas_salen"]],
        ["Grupos que no salen", seg["no_salen"]],
        ["Sin dato (reservas viejas)", seg["sin_dato"]],
    ])


def _bot(wb, m, sub):
    h = Hoja(wb, "Bot y hábitos", sub, [30, 12, 12])
    bot = m["bot"]
    h.seccion("Mensajes")
    h.tabla(["Métrica", "Valor"], [["Mensajes registrados", bot["recibidos"]], ["Bloqueados por guardrails", bot["bloqueados"]]])
    h.seccion("Tipos de mensaje más frecuentes")
    cab, a, b = h.tabla(["Tipo", "Mensajes"],
                        [list(p) for p in sumar_por_etiqueta((legible(i["intent"], ETIQUETA_INTENT), i["total"]) for i in bot["intents"])],
                        barras=(2,))
    h.grafico("Mensajes por tipo", 1, 2, cab, a, b, "E4", horizontal=True, ancho=16, alto=9)
    h.seccion("Avisos automáticos")
    h.tabla(["Tipo", "Enviados", "Fallidos"],
            [[legible(x["tipo"], ETIQUETA_AVISO), x["enviados"], x["fallidos"]] for x in bot["avisos"]],
            rojo_si_positivo=(3,))
    h.seccion("Canal de origen de las reservas")
    cab, a, b = h.tabla(["Canal", "Reservas"],
                        [[legible(c["canal"], ETIQUETA_CANAL), c["total"]] for c in m["habitos"]["canales"]], barras=(2,))


def _uso(wb, uso, sub):
    h = Hoja(wb, "Por uso (NRC y particulares)", sub, [30, 12, 12, 12, 12, 80])
    for titulo, clave, nombre_col, nombre in (
        ("Para asignaturas (con NRC)", "asignatura", "NRC", lambda f: f"NRC {f['nrc']}"),
        ("Particulares (sin NRC)", "particular", "Estudiante",
         lambda f: f"{f['nombre']} · {telefono(f['telefono_wa'])}" if f["nombre"] else telefono(f["telefono_wa"])),
    ):
        s = uso[clave]
        h.seccion(titulo)
        h.nota(f"{s['reservas']} reservas · {s['equipos']} equipos pedidos · {s['prestamos']} prestados · "
               f"{s['claves']} {'NRC distintos' if clave == 'asignatura' else 'estudiantes distintos'}. "
               "Cuenta cada equipo pedido, en cualquier estado.")
        inicio = h.fila
        cab, a, b = h.tabla(
            [nombre_col, "Equipos pedidos", "Reservas", "Prestados", "Estudiantes", "Equipos que más pide"],
            [[nombre(f), f["equipos"], f["reservas"], f["prestamos"], f["usuarios"],
              " · ".join(f"{e['nombre']} ×{e['total']}" for e in f["equipos_top"])] for f in s["top"]],
            barras=(2,),
        )
        alto = max(5, 0.6 * len(s["top"]) + 2)  # cm; una fila mide ~0,5 cm
        h.grafico(f"{nombre_col}: equipos pedidos", 1, 2, cab, a, b, f"H{inicio}", horizontal=True, ancho=14, alto=alto)
        h.seccion(f"Equipos más pedidos · {titulo.split(' (')[0].lower()}")
        cab, a, b = h.tabla(["Equipo", "Veces pedido"], [[e["nombre"], e["total"]] for e in s["equipos_top"]], barras=(2,))
        h.fila = max(h.fila, inicio + math.ceil(alto / 0.5) + 2)


def libro_metricas(m: dict, uso: dict, generado: datetime) -> bytes:
    """Arma el .xlsx de métricas a partir del JSON de General (`m`) y de Por uso (`uso`)."""
    wb = Workbook()
    wb.remove(wb.active)
    sub = f"Periodo: {etiqueta_periodo(m)} · generado el {generado.strftime('%d/%m/%Y %H:%M')}"
    _resumen(wb, m, sub)
    _estados(wb, m, sub)
    _cuando(wb, m, sub)
    _tops(wb, m, sub)
    _uso(wb, uso, sub)
    _inventario(wb, m, sub)
    _bot(wb, m, sub)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
