"""
Prueba del Excel raw (GET /dashboard/api/datos/excel): que traiga la base completa
y que una tabla más grande que el límite de Excel se parta en varias hojas.

  1. Con el límite real (1.048.575 filas por hoja): cada tabla y vista tiene una
     sola hoja con exactamente count(*) filas, y el Índice dice lo mismo.
  2. Con el límite bajado a 100: auditoria_bot se parte en "auditoria_bot",
     "auditoria_bot (2)"...; ninguna hoja pasa de 100 filas, y entre todas están
     todos los id de la tabla, sin repetir ni perder ninguno.
  3. Los nombres de hoja partidos no pasan de los 31 caracteres de Excel.

No escribe nada en la base.

    docker exec -i lacs_flask_app python - < entregables/OE3/pruebas/verificar_excel_raw.py
"""

import base64
import io
import math
import os

from openpyxl import load_workbook
from sqlalchemy import text

from app import create_app
from app.extensions import db
from app.routes import dashboard

app = create_app()
auth = base64.b64encode(f"{os.environ['DASHBOARD_USER']}:{os.environ['DASHBOARD_PASSWORD']}".encode()).decode()


def descargar():
    r = app.test_client().get("/dashboard/api/datos/excel", headers={"Authorization": f"Basic {auth}"})
    assert r.status_code == 200, r.status_code
    return load_workbook(io.BytesIO(r.data), read_only=True)


def filas_de(ws):
    return [f for f in ws.iter_rows(min_row=2, values_only=True)]


with app.app_context():
    objetos = db.session.execute(text(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    )).scalars().all()
    conteo = {t: db.session.execute(text(f'SELECT count(*) FROM "{t}"')).scalar() for t in objetos}

    # 1. Límite real: una hoja por tabla, completa.
    wb = descargar()
    assert wb.sheetnames[0] == "Índice", wb.sheetnames[:2]
    indice = {f[0]: f for f in filas_de(wb["Índice"])}
    for t, n in conteo.items():
        assert len(filas_de(wb[t[:31]])) == n, (t, n)
        assert indice[t][2] == n and indice[t][4] == t[:31], indice[t]
    print(f"1. OK: {len(conteo)} tablas y vistas completas, {sum(conteo.values())} filas en total")

    # 2. Límite en 100: auditoria_bot se parte sin perder ni repetir filas.
    dashboard._MAX_FILAS_HOJA = 100
    try:
        wb = descargar()
    finally:
        dashboard._MAX_FILAS_HOJA = 1_048_575
    n = conteo["auditoria_bot"]
    partes = [h for h in wb.sheetnames if h == "auditoria_bot" or h.startswith("auditoria_bot (")]
    assert len(partes) == math.ceil(n / 100), (partes, n)
    ids = []
    for h in partes:
        filas = filas_de(wb[h])
        assert len(filas) <= 100, (h, len(filas))
        ids += [f[0] for f in filas]
    esperados = db.session.execute(text("SELECT id FROM auditoria_bot ORDER BY id")).scalars().all()
    assert ids == esperados, "faltan, sobran o se desordenaron filas"
    fila_indice = next(f for f in filas_de(wb["Índice"]) if f[0] == "auditoria_bot")
    assert fila_indice[2] == n and fila_indice[4] == ", ".join(partes), fila_indice
    print(f"2. OK: auditoria_bot ({n} filas) en {len(partes)} hojas de hasta 100: {partes[0]} … {partes[-1]}")

    # 3. Nombres largos.
    nombre = dashboard._nombre_parte("reserva_grupo_integrantes_con_nombre_largo", 12)
    assert len(nombre) <= 31 and nombre.endswith(" (12)"), nombre
    print(f"3. OK: nombre partido de tabla larga = {nombre!r}")
