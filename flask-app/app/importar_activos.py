"""
Plantillas y parseo para importar activos desde .xlsx o .csv; relaciones se
escriben como `placa_padre` (placa del equipo padre existente o en el mismo
archivo) y `lugar` (nombre del espacio existente o en el archivo). La
inserción en BD vive en routes/dashboard.py (api_importar_activos).
"""

import csv
import io

from app.textutils import normalizar

# (clave, obligatoria, descripción, ejemplo). El orden es el de las columnas de la plantilla.
COLUMNAS = [
    ("tipo", "no", "equipo (activo) o espacio (lugar). Vacío = equipo.", "equipo"),
    ("nombre", "sí", "Nombre del activo o lugar.", "Cámara Canon EOS R"),
    ("descripcion", "no", "Texto libre.", "Cuerpo con batería y cargador"),
    ("ubicacion", "no", "Texto libre de ubicación (distinto de lugar).", "Estante 2"),
    ("placa", "sí, para equipos", "Padre: 8 dígitos. Hijo: 7 a 10 letras o números en mayúsculas.", "12345670"),
    ("placa_padre", "no", "Placa del equipo padre, ya existente o en este mismo archivo. Vacío = no es hijo.", ""),
    ("lugar", "no", "Nombre del lugar donde está, ya existente o en este archivo (ver hoja Lugares). Vacío = el del padre.", "Sala 4"),
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
    if extension.endswith(".xlsx"):
        extension = ".xlsx"
    elif extension.endswith(".csv"):
        extension = ".csv"
    else:
        raise ErrorArchivo("El archivo debe ser .xlsx o .csv.")

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


def plantilla_xlsx(lugares: list[str]) -> bytes:
    """Genera una plantilla de Excel con las instrucciones e hoja de lugares.

    lugares: lista de nombres de espacios existentes (para referencia).
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Activos"

    # Encabezado
    for col_idx, clave in enumerate(CLAVES, start=1):
        cell = ws.cell(row=1, column=col_idx, value=clave)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="171717", end_color="171717", fill_type="solid")

    # Congelar encabezado
    ws.freeze_panes = "A2"

    # Anchos de columna
    for col_idx, clave in enumerate(CLAVES, start=1):
        ancho = max(len(clave) + 4, 14)
        ws.column_dimensions[get_column_letter(col_idx)].width = ancho

    # Formato de placas: texto (@ = text format)
    for row in range(2, 1001):
        ws.cell(row=row, column=CLAVES.index("placa") + 1).number_format = "@"
        ws.cell(row=row, column=CLAVES.index("placa_padre") + 1).number_format = "@"

    # Data validations
    dv_tipo = DataValidation(type="list", formula1='"equipo,espacio"', allow_blank=True)
    ws.add_data_validation(dv_tipo)
    dv_tipo.add(f"A2:A1000")

    dv_req_conf = DataValidation(type="list", formula1='"si,no"', allow_blank=True)
    ws.add_data_validation(dv_req_conf)
    dv_req_conf.add(f"{get_column_letter(CLAVES.index('requiere_confirmacion') + 1)}2:{get_column_letter(CLAVES.index('requiere_confirmacion') + 1)}1000")

    dv_estado = DataValidation(type="list", formula1='"disponible,en_mantenimiento,no_disponible"', allow_blank=True)
    ws.add_data_validation(dv_estado)
    dv_estado.add(f"{get_column_letter(CLAVES.index('estado') + 1)}2:{get_column_letter(CLAVES.index('estado') + 1)}1000")

    # Hoja "Instrucciones"
    ws_inst = wb.create_sheet("Instrucciones", 0)
    notas = [
        "Solo agrega activos nuevos: no modifica ni borra los que ya existen.",
        "Si alguna fila tiene un error, no se importa ninguna: corrige y vuelve a subir el archivo.",
        "Llena la hoja Activos, una fila por activo o lugar. Un hijo se relaciona con su padre por placa_padre; un activo con su lugar por lugar.",
        "",
    ]
    for row_idx, nota in enumerate(notas, start=1):
        ws_inst.cell(row=row_idx, column=1, value=nota)

    # Tabla de columnas en Instrucciones
    header_row = len(notas) + 1
    headers_tabla = ["columna", "obligatoria", "descripción", "ejemplo"]
    for col_idx, header in enumerate(headers_tabla, start=1):
        cell = ws_inst.cell(row=header_row, column=col_idx, value=header)
        cell.font = Font(bold=True)

    for col_idx, (clave, oblig, desc, ejem) in enumerate(COLUMNAS, start=header_row + 1):
        ws_inst.cell(row=col_idx, column=1, value=clave)
        ws_inst.cell(row=col_idx, column=2, value=oblig)
        ws_inst.cell(row=col_idx, column=3, value=desc)
        ws_inst.cell(row=col_idx, column=4, value=ejem)

    ws_inst.column_dimensions["A"].width = 20
    ws_inst.column_dimensions["B"].width = 18
    ws_inst.column_dimensions["C"].width = 50
    ws_inst.column_dimensions["D"].width = 20

    # Hoja "Lugares"
    ws_lugares = wb.create_sheet("Lugares")
    ws_lugares.cell(row=1, column=1, value="lugar").font = Font(bold=True)
    for row_idx, lugar in enumerate(lugares, start=2):
        ws_lugares.cell(row=row_idx, column=1, value=lugar)
    ws_lugares.column_dimensions["A"].width = 30

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

    # Test 7: plantilla_xlsx se puede leer de vuelta
    try:
        contenido = plantilla_xlsx(["Sala 4"])
        filas = leer_filas("p.xlsx", contenido)
        assert False, "Debería haber levantado ErrorArchivo (sin filas)"
    except ErrorArchivo as e:
        assert "no tiene filas" in str(e), f"Mensaje inesperado: {e}"

    print("ok")
