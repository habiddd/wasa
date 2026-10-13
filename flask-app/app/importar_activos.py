"""
Plantillas y parseo para importar activos desde .xlsx, .xlsm o .csv; relaciones se
escriben como `placa_padre` (placa del equipo padre existente o en el mismo
archivo) y `lugar` (nombre del espacio existente o en el archivo). La
inserción en BD vive en routes/dashboard.py (api_importar_activos).

La plantilla de Excel es una sola, `plantilla_xlsm`
(con macros: la hoja Inicio trae un formulario con botones que copian filas a la
hoja Activos; el VBA viene en plantillas/base-activos.xlsm). Solo se lee la hoja
Activos, así que el formulario y las macros nunca llegan a la importación.
"""

import csv
import io
from pathlib import Path

from app.textutils import normalizar

# (clave, obligatoria, descripción, ejemplo). El orden es el de las columnas de la plantilla.
COLUMNAS = [
    ("tipo", "no", "equipo (activo) o espacio (lugar). Vacío = equipo.", "equipo"),
    ("nombre", "sí", "Nombre del activo o lugar.", "Cámara Canon EOS R"),
    ("descripcion", "no", "Texto libre.", "Cuerpo con batería y cargador"),
    ("ubicacion", "no", "Texto libre de ubicación (distinto de lugar).", "Estante 2"),
    ("placa", "sí, para equipos", "Padre: 8 dígitos. Hijo: 7 a 10 letras o números en mayúsculas.", "12345670"),
    ("placa_padre", "no", "Placa del equipo padre, ya existente o en este mismo archivo. Vacío = no es hijo.", ""),
    ("lugar", "no", "Nombre del lugar donde está, ya existente o en este archivo (ver hoja Listas). Vacío = el del padre.", "Sala 4"),
    ("categoria", "sí, para equipos", "Agrupación gruesa.", "grabación audiovisual"),
    ("subcategoria", "no", "Tipo concreto de equipo.", "cámara"),
    ("marca", "no", "", "Canon"),
    ("modelo", "no", "", "EOS R"),
    ("identificador", "no", "Número de serie o IMEI.", "SN123456"),
    ("requiere_confirmacion", "no", "si o no. Vacío = no.", "no"),
    ("estado", "no", "disponible, en_mantenimiento o no_disponible. Vacío = disponible.", "disponible"),
]
CLAVES = [c[0] for c in COLUMNAS]
MAX_FILAS = 2000
ESTADOS = ("disponible", "en_mantenimiento", "no_disponible")
BASE_XLSM = Path(__file__).parent / "plantillas" / "base-activos.xlsm"
ULTIMA_FILA = MAX_FILAS + 1  # última fila de datos de Activos (la 1 es el encabezado)
OBLIGATORIAS = {"nombre", "placa", "categoria"}  # placa/categoria solo para equipos

# Estilos de la plantilla de Excel
COLOR_OSCURO = "171717"
COLOR_OBLIGATORIA = "9A3412"
COLOR_CELDA = "F5F5F5"
COLOR_BORDE = "D4D4D4"
COLOR_ERROR = "FECACA"

# Desplegables con lista fija (se muestra error si el valor no está en la lista)
LISTAS_FIJAS = {
    "tipo": '"equipo,espacio"',
    "requiere_confirmacion": '"si,no"',
    "estado": '"disponible,en_mantenimiento,no_disponible"',
}
# Desplegables que leen de la hoja Listas; un valor nuevo también se acepta
RANGOS_LISTAS = {
    "lugar": "Listas!$A$2:$A$500",
    "categoria": "Listas!$C$2:$C$500",
    "subcategoria": "Listas!$E$2:$E$500",
}


class ErrorArchivo(ValueError):
    """El archivo entero no se puede leer (formato, encabezados, tamaño)."""


def _texto(valor) -> str:
    """Convierte un valor a str: None -> ""; float con .is_integer() -> str(int);
    int -> str; otro -> str(valor).strip()."""
    if valor is None:
        return ""
    if isinstance(valor, float):
        if valor.is_integer():
            return str(int(valor))
        return str(valor)
    if isinstance(valor, int):
        return str(valor)
    return str(valor).strip()


def _clave_encabezado(texto) -> str:
    """Normaliza un encabezado a clave: lowercase, sin acentos, espacios -> _."""
    return normalizar(_texto(texto)).strip().replace(" ", "_")


def leer_filas(nombre_archivo: str, contenido: bytes) -> list[tuple[int, dict]]:
    """Lee filas de un archivo .xlsx o .csv, devuelve list[(fila, {clave: valor})].

    fila es 1-indexado (la cabecera es fila 1).
    Levanta ErrorArchivo si hay problemas.
    """
    extension = nombre_archivo.lower()
    if extension.endswith((".xlsx", ".xlsm")):
        extension = ".xlsx"
    elif extension.endswith(".csv"):
        extension = ".csv"
    else:
        raise ErrorArchivo("El archivo debe ser .xlsx, .xlsm o .csv.")

    if extension == ".xlsx":
        from openpyxl import load_workbook
        try:
            wb = load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
        except Exception:
            raise ErrorArchivo("No se pudo leer el archivo de Excel.")

        sheet = wb["Activos"] if "Activos" in wb.sheetnames else wb.active
        rows = list(sheet.iter_rows(values_only=True))
    else:  # .csv
        try:
            texto = contenido.decode("utf-8-sig")
        except UnicodeDecodeError:
            texto = contenido.decode("cp1252")

        # Detectar delimitador
        lineas = texto.split("\n")
        primera = lineas[0] if lineas else ""
        delim = ";" if primera.count(";") > primera.count(",") else ","

        rows = list(csv.reader(io.StringIO(texto), delimiter=delim))

    if not rows:
        raise ErrorArchivo("El archivo está vacío.")

    # Procesar encabezado
    header_orig = rows[0]
    header = []
    claves_vistas = set()
    for col_idx, col_orig in enumerate(header_orig):
        clave = _clave_encabezado(col_orig)
        if not clave:  # Celda vacía: la columna se ignora
            continue
        if clave not in CLAVES:
            raise ErrorArchivo(f"Columna desconocida: «{_texto(col_orig)}». Usa la plantilla.")
        if clave in claves_vistas:
            raise ErrorArchivo(f"La columna «{clave}» está repetida.")
        claves_vistas.add(clave)
        # Se guarda la posición real: una columna sin encabezado no corre las demás.
        header.append((col_idx, clave))

    if "nombre" not in claves_vistas:
        raise ErrorArchivo("Falta la columna «nombre».")

    # Procesar filas
    filas_resultado = []
    for idx, row in enumerate(rows[1:], start=2):
        datos = {}
        for col_idx, clave in header:
            valor = row[col_idx] if col_idx < len(row) else None
            datos[clave] = _texto(valor)

        # Rellenar claves faltantes con ""
        for clave in CLAVES:
            if clave not in datos:
                datos[clave] = ""

        # Saltar fila vacía (todos los valores son "")
        if all(v == "" for v in datos.values()):
            continue

        filas_resultado.append((idx, datos))

    if not filas_resultado:
        raise ErrorArchivo("El archivo no tiene filas para importar.")

    if len(filas_resultado) > MAX_FILAS:
        raise ErrorArchivo(f"El archivo tiene más de {MAX_FILAS} filas; divídelo en partes.")

    return filas_resultado


def normalizar_fila(datos: dict) -> tuple[dict | None, str | None]:
    """Valida y normaliza una fila. Devuelve (limpio, None) o (None, mensaje_error).

    limpio es un dict con todas las claves de CLAVES, valores normalizados.
    """
    get = lambda k: datos.get(k, "").strip()

    # tipo
    t = normalizar(get("tipo"))
    if t in ("", "equipo", "activo"):
        tipo = "equipo"
    elif t in ("espacio", "lugar"):
        tipo = "espacio"
    else:
        return None, "Tipo inválido: usa equipo o espacio."

    # nombre (obligatorio)
    nombre = get("nombre")
    if not nombre:
        return None, "Falta el nombre."

    # placa y placa_padre: uppercase, sin espacios
    placa_raw = get("placa").upper().replace(" ", "")
    placa = placa_raw if placa_raw else None

    placa_padre_raw = get("placa_padre").upper().replace(" ", "")
    placa_padre = placa_padre_raw if placa_padre_raw else None

    # requiere_confirmacion
    v = normalizar(get("requiere_confirmacion"))
    if v in ("", "no", "n", "false", "0"):
        requiere_confirmacion = False
    elif v in ("si", "s", "true", "1", "x"):
        requiere_confirmacion = True
    else:
        return None, "requiere_confirmacion debe ser si o no."

    # estado
    v = normalizar(get("estado")).replace(" ", "_") or "disponible"
    if v not in ESTADOS:
        return None, "Estado inválido: usa disponible, en_mantenimiento o no_disponible."
    estado = v

    # Validación: espacio no puede tener padre ni lugar
    if tipo == "espacio" and (placa_padre or get("lugar")):
        return None, "Un lugar no puede tener padre ni estar dentro de otro lugar."

    # Campos de texto: stripped, "" -> None
    descripcion = get("descripcion") or None
    ubicacion = get("ubicacion") or None
    lugar = get("lugar") or None
    categoria = get("categoria") or None
    subcategoria = get("subcategoria") or None
    marca = get("marca") or None
    modelo = get("modelo") or None
    identificador = get("identificador") or None

    limpio = {
        "tipo": tipo,
        "nombre": nombre,
        "descripcion": descripcion,
        "ubicacion": ubicacion,
        "placa": placa,
        "placa_padre": placa_padre,
        "lugar": lugar,
        "categoria": categoria,
        "subcategoria": subcategoria,
        "marca": marca,
        "modelo": modelo,
        "identificador": identificador,
        "requiere_confirmacion": requiere_confirmacion,
        "estado": estado,
    }

    return limpio, None


def _relleno(color: str):
    from openpyxl.styles import PatternFill
    return PatternFill(start_color=color, end_color=color, fill_type="solid")


def _validacion(clave: str):
    """Lista desplegable (si aplica) + mensaje de ayuda al seleccionar la celda."""
    from openpyxl.worksheet.datavalidation import DataValidation

    descripcion = next(d for k, _o, d, _e in COLUMNAS if k == clave)
    ayuda = {
        "showInputMessage": True,
        "promptTitle": clave[:32],
        "prompt": (descripcion or clave)[:255],
    }
    if clave in LISTAS_FIJAS:
        return DataValidation(
            type="list", formula1=LISTAS_FIJAS[clave], allow_blank=True,
            showErrorMessage=True, errorTitle="Valor no válido",
            error="Elige un valor de la lista.", **ayuda,
        )
    if clave in RANGOS_LISTAS:
        # Un valor nuevo (todavía no está en Listas) también se acepta: sin error.
        return DataValidation(
            type="list", formula1=RANGOS_LISTAS[clave], allow_blank=True,
            showErrorMessage=False, **ayuda,
        )
    return DataValidation(**ayuda)


def _hoja_listas(ws, listas: dict):
    from openpyxl.styles import Font

    equipos = listas["equipos"]
    columnas = [
        ("A", "lugares", listas["lugares"], 30),
        ("C", "categorias", listas["categorias"], 30),
        ("E", "subcategorias", listas["subcategorias"], 30),
        ("G", "placa", [p for p, _ in equipos], 14),
        ("H", "equipo existente", [n for _, n in equipos], 40),
    ]
    for letra, encabezado, valores, ancho in columnas:
        ws[f"{letra}1"] = encabezado
        ws[f"{letra}1"].font = Font(bold=True)
        for fila, valor in enumerate(valores, start=2):
            ws[f"{letra}{fila}"] = valor
        ws.column_dimensions[letra].width = ancho
    ws["J1"] = ("Estas listas alimentan los desplegables. placa: úsala en placa_padre "
                "para colgar un hijo de un equipo que ya existe.")


def _hoja_activos(ws):
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    letra = {clave: get_column_letter(i) for i, clave in enumerate(CLAVES, start=1)}
    primera, ultima = letra[CLAVES[0]], letra[CLAVES[-1]]

    # Encabezado y anchos
    for col_idx, clave in enumerate(CLAVES, start=1):
        cell = ws.cell(row=1, column=col_idx, value=clave)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = _relleno(COLOR_OBLIGATORIA if clave in OBLIGATORIAS else COLOR_OSCURO)
        if clave in ("nombre", "descripcion"):
            ws.column_dimensions[letra[clave]].width = 32
        else:
            ws.column_dimensions[letra[clave]].width = max(len(clave) + 4, 16)

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{ultima}{ULTIMA_FILA}"

    # Placas como texto, para que Excel no quite los ceros de la izquierda
    for fila in range(2, ULTIMA_FILA + 1):
        ws.cell(row=fila, column=CLAVES.index("placa") + 1).number_format = "@"
        ws.cell(row=fila, column=CLAVES.index("placa_padre") + 1).number_format = "@"

    # Desplegables y ayuda de cada columna
    for clave in CLAVES:
        dv = _validacion(clave)
        ws.add_data_validation(dv)
        dv.add(f"{letra[clave]}2:{letra[clave]}{ULTIMA_FILA}")

    # Resaltar lo que falta o está repetido
    n, p, c, t = letra["nombre"], letra["placa"], letra["categoria"], letra["tipo"]
    error = _relleno(COLOR_ERROR)
    reglas = [
        (f"{n}2:{n}{ULTIMA_FILA}", f'AND(${n}2="",COUNTA(${primera}2:${ultima}2)>0)'),
        (f"{p}2:{p}{ULTIMA_FILA}", f'AND(${n}2<>"",${t}2<>"espacio",${t}2<>"lugar",${p}2="")'),
        (f"{c}2:{c}{ULTIMA_FILA}", f'AND(${n}2<>"",${t}2<>"espacio",${t}2<>"lugar",${c}2="")'),
        (f"{p}2:{p}{ULTIMA_FILA}", f'AND(${p}2<>"",COUNTIF(${p}$2:${p}${ULTIMA_FILA},${p}2)>1)'),
    ]
    for rango, formula in reglas:
        ws.conditional_formatting.add(rango, FormulaRule(formula=[formula], fill=error))


def _hoja_inicio(wb):
    from openpyxl.styles import Alignment, Border, Font, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.workbook.defined_name import DefinedName
    from openpyxl.worksheet.hyperlink import Hyperlink

    ws = wb["Inicio"]
    ws.sheet_view.showGridLines = False
    # B-D: formulario de activos. F-H: agregar categorías y subcategorías.
    for letra, ancho in zip("ABCDEFGH", (3, 26, 40, 70, 3, 22, 30, 26)):
        ws.column_dimensions[letra].width = ancho

    lado = Side(style="thin", color=COLOR_BORDE)
    borde = Border(left=lado, right=lado, top=lado, bottom=lado)
    letra_nombre = get_column_letter(CLAVES.index("nombre") + 1)

    def definir(nombre: str, celda):
        wb.defined_names[nombre] = DefinedName(
            nombre, attr_text=f"Inicio!${celda.column_letter}${celda.row}"
        )

    def encabezado(fila: int, columna: int, *textos: str):
        for col, texto in enumerate(textos, start=columna):
            celda = ws.cell(row=fila, column=col, value=texto)
            celda.font = Font(bold=True, color="FFFFFF")
            celda.fill = _relleno(COLOR_OSCURO)

    def entrada(fila: int, columna: int):
        celda = ws.cell(row=fila, column=columna)
        celda.fill = _relleno(COLOR_CELDA)
        celda.border = borde
        celda.alignment = Alignment(horizontal="left")
        return celda

    def boton(fila: int, columna: int, texto: str, destino, tooltip: str, principal: bool = True):
        """Celda con hipervínculo interno: el clic dispara la macro (ver
        ThisWorkbook.cls.txt) y deja el cursor en `destino`."""
        celda = ws.cell(row=fila, column=columna, value=texto)
        celda.hyperlink = Hyperlink(ref=celda.coordinate, location=f"Inicio!{destino.coordinate}", tooltip=tooltip)
        # El estilo va después del hipervínculo, que pone el suyo.
        celda.fill = _relleno(COLOR_OSCURO if principal else "E5E5E5")
        celda.font = Font(bold=True, color="FFFFFF" if principal else COLOR_OSCURO)
        celda.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[fila].height = 26
        return celda

    ws["B2"] = "Importar activos"
    ws["B2"].font = Font(bold=True, size=16)
    ws["B3"] = ("Solo agrega activos nuevos: no modifica ni borra los que ya existen. "
                "Si alguna fila tiene un error al importar, no se importa ninguna.")

    textos = [
        "1. Llena el formulario de abajo y pulsa «Agregar a la hoja Activos». Repite por cada activo o lugar.",
        "2. También puedes escribir o pegar filas directamente en la tabla de la hoja Activos.",
        "3. Guarda el archivo y súbelo en el panel: Activos > Importar.",
        None,
        "Hijo de otro equipo: escribe en placa_padre la placa del padre (ya existente, ver hoja Listas, o en este mismo archivo).",
        "Lugar: escribe el nombre del lugar donde está (ya existente o en este mismo archivo). Vacío = el del padre.",
        "Si los botones no responden, habilita las macros (barra amarilla «Habilitar contenido»). "
        "Si Windows bloqueó el archivo: clic derecho > Propiedades > Desbloquear. "
        "Sin macros igual puedes llenar la hoja Activos a mano.",
    ]
    fila = 5
    for texto in textos:
        ws.cell(row=fila, column=2, value=texto)
        fila += 1

    # --- Formulario de activos (B-D) ---
    fila += 1
    fila_encabezado = fila
    encabezado(fila, 2, "campo", "valor", "qué va aquí")
    campos = {}
    for clave, _oblig, desc, ejem in COLUMNAS:
        fila += 1
        ws.cell(row=fila, column=2, value=clave + (" *" if clave in OBLIGATORIAS else "")).font = Font(bold=True)
        celda = campos[clave] = entrada(fila, 3)
        if clave in ("placa", "placa_padre"):
            celda.number_format = "@"
        dv = _validacion(clave)
        ws.add_data_validation(dv)
        dv.add(celda.coordinate)
        definir(f"f_{clave}", celda)
        texto_ayuda = " ".join(x for x in (desc, f"Ej.: {ejem}" if ejem else "") if x)
        ws.cell(row=fila, column=4, value=texto_ayuda).alignment = Alignment(wrap_text=True, vertical="top")

    fila += 2
    definir("btn_limpiar", boton(fila, 2, "Limpiar formulario", campos["nombre"], "Vacía el formulario.", principal=False))
    definir("btn_agregar", boton(fila, 3, "Agregar a la hoja Activos", campos["nombre"],
                                 "Pasa lo llenado a una fila nueva de la hoja Activos."))
    fila += 1
    ws.cell(row=fila, column=3).font = Font(italic=True, color="525252")
    definir("msg_formulario", ws.cell(row=fila, column=3))
    fila += 1
    ws.cell(row=fila, column=3,
            value=f'="Filas listas en la hoja Activos: "&COUNTA(Activos!${letra_nombre}$2:${letra_nombre}${ULTIMA_FILA})'
            ).font = Font(bold=True)
    fila += 1
    ws.cell(row=fila, column=2, value="* obligatorio (placa y categoría solo para equipos)")

    # --- Categorías y subcategorías nuevas (F-H), al lado del formulario ---
    # La macro las agrega a la hoja Listas, de donde leen los desplegables.
    fila = fila_encabezado
    encabezado(fila, 6, "agregar a las listas", "valor", "")
    for clave, etiqueta in (("categoria", "categoría"), ("subcategoria", "subcategoría")):
        fila += 1
        ws.cell(row=fila, column=6, value=f"nueva {etiqueta}").font = Font(bold=True)
        celda = entrada(fila, 7)
        definir(f"n_{clave}", celda)
        definir(f"btn_{clave}", boton(fila, 8, f"Agregar {etiqueta}", celda,
                                      f"La agrega a la lista de la hoja Listas."))
    fila += 1
    ws.cell(row=fila, column=7).font = Font(italic=True, color="525252")
    definir("msg_listas", ws.cell(row=fila, column=7))
    fila += 1
    ws.cell(row=fila, column=6, value="Quedan en los desplegables de categoría y subcategoría,")
    ws.cell(row=fila + 1, column=6, value="aquí y en la hoja Activos. También puedes escribir")
    ws.cell(row=fila + 2, column=6, value="una nueva directamente en el campo.")


def plantilla_xlsm(listas: dict) -> bytes:
    """Plantilla de Excel: formulario con botones en Inicio, tabla en Activos y Listas.
    El VBA viene en base-activos.xlsm (ver scripts/generar_base_xlsm.ps1); acá solo se llenan las hojas."""
    from openpyxl import load_workbook

    wb = load_workbook(BASE_XLSM, keep_vba=True)
    _hoja_listas(wb["Listas"], listas)
    _hoja_activos(wb["Activos"])
    _hoja_inicio(wb)
    wb.active = 0
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def plantilla_csv() -> bytes:
    """Plantilla vacía en CSV. El delimitador es `;` para compatibilidad con
    Excel en español (Colombia) abre dividido en columnas con doble clic."""
    return ("﻿" + ";".join(CLAVES) + "\r\n").encode("utf-8")


if __name__ == "__main__":
    import sys

    # Test 1: plantilla_csv se puede leer de vuelta (pero sin filas)
    try:
        leer_filas("p.csv", plantilla_csv())
        assert False, "Debería haber levantado ErrorArchivo (sin filas)"
    except ErrorArchivo as e:
        assert "no tiene filas" in str(e), f"Mensaje inesperado: {e}"

    # Test 2: CSV con ";" y dos filas + una vacía
    csv_test = b"Tipo;Nombre;Placa;Placa padre;Lugar;Categor\xc3\xada;Requiere confirmaci\xc3\xb3n\r\nequipo;C\xc3\xa1mara;12345670;;Sala 4;audio;S\xc3\xad\r\nlugar;Sala 4;;;;;\r\n;;;;;;"
    filas = leer_filas("test.csv", csv_test)
    assert len(filas) == 2, f"Se esperaban 2 filas, se obtuvieron {len(filas)}"

    fila1, datos1 = filas[0]
    assert fila1 == 2, f"Primera fila debería ser 2, es {fila1}"
    assert datos1["nombre"] == "Cámara", f"Nombre esperado 'Cámara', obtenido '{datos1['nombre']}'"
    assert datos1["placa"] == "12345670"
    assert datos1["lugar"] == "Sala 4"

    limpio1, err1 = normalizar_fila(datos1)
    assert err1 is None, f"Error inesperado: {err1}"
    assert limpio1["placa"] == "12345670"
    assert limpio1["requiere_confirmacion"] is True, "requiere_confirmacion debería ser True"
    assert limpio1["lugar"] == "Sala 4"
    assert limpio1["estado"] == "disponible"

    fila2, datos2 = filas[1]
    assert fila2 == 3
    assert datos2["tipo"] == "lugar"
    limpio2, err2 = normalizar_fila(datos2)
    assert err2 is None, f"Error inesperado: {err2}"
    assert limpio2["tipo"] == "espacio"

    # Test 3: xlsx en memoria
    try:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(["nombre", "placa"])
        ws.append(["Lente", 12345671.0])
        buf = io.BytesIO()
        wb.save(buf)
        filas = leer_filas("test.xlsx", buf.getvalue())
        assert len(filas) == 1
        assert filas[0][1]["placa"] == "12345671", f"placa esperada '12345671', obtenida '{filas[0][1]['placa']}'"
    except Exception as e:
        print(f"Test 3 (xlsx) falló: {e}", file=sys.stderr)
        sys.exit(1)

    # Test 4: normalizar_fila con estado inválido
    limpio, error = normalizar_fila({"nombre": "X", "estado": "roto"})
    assert error is not None and "Estado inválido" in error, f"Error esperado, obtenido: {error}"

    # Test 5: espacio con lugar
    limpio, error = normalizar_fila({"nombre": "Sala", "tipo": "espacio", "lugar": "Otra"})
    assert error is not None and "lugar no puede" in error, f"Error esperado, obtenido: {error}"

    # Test 6: columna desconocida
    try:
        leer_filas("test.csv", b"color;nombre\r\nrojo;X")
        assert False, "Debería haber levantado ErrorArchivo"
    except ErrorArchivo as e:
        assert "color" in str(e).lower() or "desconocida" in str(e)

    # Test 6b: una columna sin encabezado en medio no corre las demás
    filas = leer_filas("t.csv", "nombre;;placa\r\nX;basura;12345670".encode())
    assert filas[0][1]["placa"] == "12345670", filas

    listas = {"lugares": ["Sala 4"], "categorias": ["audio"], "subcategorias": ["cámara"], "equipos": [("12345670", "Cámara")]}

    # Test 8: plantilla_xlsm con formulario (usa plantillas/base-activos.xlsm)
    import zipfile
    from openpyxl import load_workbook
    contenido = plantilla_xlsm(listas)
    assert "xl/vbaProject.bin" in zipfile.ZipFile(io.BytesIO(contenido)).namelist()
    wb = load_workbook(io.BytesIO(contenido), keep_vba=True)
    for nombre in ["f_" + c for c in CLAVES] + [
        "btn_agregar", "btn_limpiar", "msg_formulario",
        "n_categoria", "n_subcategoria", "btn_categoria", "btn_subcategoria", "msg_listas",
    ]:
        assert nombre in wb.defined_names, f"Falta el nombre definido {nombre}"
    assert wb.sheetnames[0] == "Inicio"
    try:
        leer_filas("p.xlsm", contenido)
        assert False, "Debería haber levantado ErrorArchivo (sin filas)"
    except ErrorArchivo as e:
        assert "no tiene filas" in str(e), f"Mensaje inesperado: {e}"
    wb["Activos"].cell(row=2, column=CLAVES.index("nombre") + 1, value="Lente")
    wb["Activos"].cell(row=2, column=CLAVES.index("placa") + 1, value="LEN0001")
    buf = io.BytesIO()
    wb.save(buf)
    filas = leer_filas("p.xlsm", buf.getvalue())
    assert len(filas) == 1 and filas[0][0] == 2, filas
    assert filas[0][1]["nombre"] == "Lente" and filas[0][1]["placa"] == "LEN0001", filas

    print("ok")
