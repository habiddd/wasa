"""
Acta de entrega y devolución de activos (PDF), la hoja que se imprime y se
firma a mano cuando alguien retira material de la LACS y cuando lo regresa.

Módulo puro a propósito: recibe objetos del modelo (`Reserva`) y devuelve
bytes, sin tocar `flask.request`/`jsonify` ni el blueprint de dashboard --
así se puede generar o probar un acta sin un contexto de aplicación armado, y
`routes/dashboard.py` se limita a resolver el `grupo_id` y envolver el PDF en
una respuesta HTTP.

Un acta es por `grupo_id` (la reserva completa), no por línea de activo: es
lo que se firma en una sola sesión de entrega/devolución, aunque el carrito
tenga varios equipos.
"""

from datetime import datetime, timezone
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.models import Reserva
from app.recursos_service import DIAS_MINIMOS_TRAMITE_SEGURO, ZONA_HORARIA_LACS

# Márgenes para imprimir y archivar en papel, ajustados (no los 1.5cm+ que
# se ven en una hoja de oficina normal) porque el bloque de firmas necesita
# su propio espacio y una reserva de 1-3 activos debe caber en una página.
_MARGEN = 1.1 * cm
_ANCHO_UTIL = letter[0] - 2 * _MARGEN

_MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

_ESTADOS_LEGIBLES = {
    "confirmada": "Confirmada",
    "pendiente": "Pendiente",
    "denegada": "Denegada",
    "cancelada": "Cancelada",
    "completada": "Completada",
    "no_show": "No show",
}

_CANALES_LEGIBLES = {"whatsapp": "WhatsApp", "dashboard": "Dashboard"}

# --- Estilos -----------------------------------------------------------------
# Helvetica (fuente core de reportlab, sin embeber nada) ya cubre Latin-1 --
# tildes y eñe del español salen bien sin tocar fuentes ni encoding.

_TITULO = ParagraphStyle("ActaTitulo", fontName="Helvetica-Bold", fontSize=15, leading=18)
_SUBTITULO = ParagraphStyle(
    "ActaSubtitulo", fontName="Helvetica", fontSize=10.5, leading=13,
    textColor=colors.HexColor("#555555"), spaceAfter=6,
)
_SECCION = ParagraphStyle(
    "ActaSeccion", fontName="Helvetica-Bold", fontSize=11, leading=13,
    spaceBefore=5, spaceAfter=2, textColor=colors.HexColor("#1a1a1a"),
)
_NORMAL = ParagraphStyle("ActaNormal", fontName="Helvetica", fontSize=9.5, leading=12.5)
_CELDA = ParagraphStyle("ActaCelda", fontName="Helvetica", fontSize=8, leading=10)
_CELDA_ENCABEZADO = ParagraphStyle(
    "ActaCeldaEncabezado", fontName="Helvetica-Bold", fontSize=8, leading=10,
    textColor=colors.white,
)
_FIRMA_TITULO_FILA = ParagraphStyle(
    "ActaFirmaFila", fontName="Helvetica-Bold", fontSize=10, leading=12,
    textColor=colors.HexColor("#1a1a1a"),
)
_FIRMA_ROL = ParagraphStyle("ActaFirmaRol", fontName="Helvetica-Bold", fontSize=9, leading=11, spaceAfter=2)
_FIRMA_TITULAR = ParagraphStyle(
    "ActaFirmaTitular", fontName="Helvetica-Oblique", fontSize=8, leading=10,
    textColor=colors.HexColor("#444444"),
)
_FIRMA_PIE = ParagraphStyle("ActaFirmaPie", fontName="Helvetica", fontSize=8.5, leading=11)
_FIRMA_PIE_CENTRADO = ParagraphStyle(
    "ActaFirmaPieCentrado", parent=_FIRMA_PIE, alignment=1,  # 1 = TA_CENTER
)


def _esc(valor) -> str:
    """Escapa para meter texto libre (nombre, nota...) dentro de un Paragraph
    -- si trae '<', '&' o similar (copiado y pegado de otro lado) rompería el
    parser de reportlab en vez de mostrarse tal cual."""
    return escape(str(valor)) if valor not in (None, "") else "–"


def _normalizar_utc(dt: datetime) -> datetime:
    """Los `datetime` que vienen de la BD pueden llegar naive -- ver el mismo
    ajuste en `_serializar_reserva` (routes/dashboard.py)."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _local(dt: datetime) -> datetime:
    return _normalizar_utc(dt).astimezone(ZONA_HORARIA_LACS)


def _fecha_larga(dt: datetime) -> str:
    """"22 de septiembre de 2026, 9:00 a. m." -- mismo formato que usa el
    dashboard en JS (Intl es-CO), a mano porque reportlab corre en Python."""
    local = _local(dt)
    hora12 = local.hour % 12 or 12
    meridiano = "a. m." if local.hour < 12 else "p. m."
    return f"{local.day} de {_MESES[local.month - 1]} de {local.year}, {hora12}:{local.minute:02d} {meridiano}"


def _tabla_datos(pares: list[tuple[str, str]]) -> Table:
    """Lista de propiedades "etiqueta: valor" sin bordes, como las tarjetas
    `Dato` de ReservaDetalle.tsx pero en PDF."""
    filas = [
        [Paragraph(f"<b>{_esc(etiqueta)}</b>", _NORMAL), Paragraph(_esc(valor), _NORMAL)]
        for etiqueta, valor in pares
    ]
    tabla = Table(filas, colWidths=[4.2 * cm, _ANCHO_UTIL - 4.2 * cm])
    tabla.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 1.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.2),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return tabla


def _celda_firma(titulo_fila: str, rol: str, subtitulo: str | None) -> list:
    """Contenido de un cuadro de firma: título (ENTREGA/DEVOLUCIÓN), rol,
    titular preimpreso si aplica, espacio para firmar a mano y los campos que
    se llenan en el momento. La cédula SIEMPRE queda en blanco -- aunque ya
    la tengamos en `usuario.cedula`, escribirla a mano junto a la firma es lo
    que le da valor probatorio al acta."""
    contenido = [
        Paragraph(_esc(titulo_fila), _FIRMA_TITULO_FILA),
        Paragraph(_esc(rol), _FIRMA_ROL),
    ]
    if subtitulo:
        contenido.append(Paragraph(_esc(subtitulo), _FIRMA_TITULAR))
    contenido += [
        # Alto para firmar a mano -- lo pide el flujo físico del acta, no es
        # relleno visual.
        Spacer(1, 2 * cm),
        HRFlowable(width="85%", thickness=0.75, color=colors.black, hAlign="CENTER"),
        Paragraph("Firma", _FIRMA_PIE_CENTRADO),
        Spacer(1, 0.15 * cm),
        Paragraph("C.C. " + "_" * 22, _FIRMA_PIE),
        Spacer(1, 0.1 * cm),
        Paragraph("Fecha ____ / ____ / ________", _FIRMA_PIE),
    ]
    return contenido


def generar_acta(reservas: list[Reserva]) -> bytes:
    """PDF del acta de entrega y devolución para todas las líneas de un mismo
    `grupo_id` (ya ordenadas por quien llama, ver `_reservas_del_grupo`)."""
    primera = reservas[0]
    grupo = primera.grupo
    usuario = primera.usuario

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        topMargin=_MARGEN,
        bottomMargin=_MARGEN,
        leftMargin=_MARGEN,
        rightMargin=_MARGEN,
        title=f"Acta de entrega y devolución - Reserva {primera.grupo_id}",
    )
    historia = []

    # --- Encabezado ----------------------------------------------------------
    historia.append(Paragraph("Acta de entrega y devolución de activos", _TITULO))
    # Nombre oficial del laboratorio: es el membrete del documento que se
    # firma, así que va completo y tal cual, no la sigla con la que se le
    # nombra en el resto del sistema.
    historia.append(
        Paragraph("Laboratorio de Producción Transmedia Álvaro Cepeda Samudio (LACS)", _SUBTITULO)
    )
    historia.append(Paragraph(
        f"<b>N.º de reserva:</b> {primera.grupo_id}"
        f"&nbsp;&nbsp;·&nbsp;&nbsp;<b>Generada:</b> {_esc(_fecha_larga(datetime.now(timezone.utc)))}",
        _NORMAL,
    ))
    historia.append(Spacer(1, 0.15 * cm))

    # --- Solicitante -----------------------------------------------------------
    historia.append(Paragraph("Solicitante", _SECCION))
    historia.append(_tabla_datos([
        ("Nombre", usuario.nombre if usuario else None),
        ("WhatsApp", usuario.telefono_wa if usuario else None),
        ("Código Uninorte", usuario.codigo_uninorte if usuario else None),
        ("Cédula", usuario.cedula if usuario else None),
        ("Rol", (usuario.rol.capitalize() if usuario and usuario.rol else None)),
    ]))

    # --- Reserva -----------------------------------------------------------
    if grupo and grupo.tipo_uso == "asignatura":
        uso_texto = f"Asignatura · NRC {grupo.nrc}"
    else:
        uso_texto = "Particular"

    if grupo and grupo.modalidad == "grupo":
        modalidad_texto = f"Grupo de {grupo.tamano_grupo} personas"
    else:
        modalidad_texto = "Individual"

    if grupo and grupo.sale_de_barranquilla is True:
        seguro_texto = (
            f"Sí -- requiere trámite de seguro (mínimo {DIAS_MINIMOS_TRAMITE_SEGURO} días hábiles)"
        )
    elif grupo and grupo.sale_de_barranquilla is False:
        seguro_texto = "No"
    else:
        seguro_texto = "Sin dato"

    pares_reserva = [
        # En dash, no una flecha Unicode: Helvetica (fuente core, WinAnsi) no
        # tiene el glifo de "→" y lo dibuja como un cuadro relleno en vez del
        # carácter -- "–" sí está en WinAnsi y ya se usa en el resto del acta.
        ("Horario", f"{_fecha_larga(primera.inicio)} – {_fecha_larga(primera.fin)}"),
        # Estado de la reserva como conjunto -- si una línea quedó distinta
        # (ej. un activo denegado), la tabla de activos más abajo lo muestra
        # fila por fila.
        ("Estado", _ESTADOS_LEGIBLES.get(primera.estado, primera.estado)),
        ("Tipo de uso", uso_texto),
        ("Modalidad", modalidad_texto),
    ]

    # Los códigos de integrantes solo existen cuando modalidad='grupo' (ver
    # recursos_service.crear_reservas_grupo) -- integrantes[0] es quien reserva.
    if grupo and grupo.modalidad == "grupo" and grupo.integrantes:
        integrantes_texto = ", ".join(
            f"{i.codigo_uninorte}{' (quien reserva)' if orden == 0 else ''}"
            for orden, i in enumerate(grupo.integrantes)
        )
        pares_reserva.append(("Integrantes", integrantes_texto))

    pares_reserva.append(("Sale de Barranquilla", seguro_texto))
    pares_reserva.append(("Canal de origen", _CANALES_LEGIBLES.get(primera.canal_origen, primera.canal_origen)))
    if primera.creado_en:
        pares_reserva.append(("Fecha de creación", _fecha_larga(primera.creado_en)))

    historia.append(Paragraph("Reserva", _SECCION))
    historia.append(_tabla_datos(pares_reserva))

    if grupo and grupo.nota:
        historia.append(Spacer(1, 0.15 * cm))
        historia.append(Paragraph(f"<b>Nota:</b> {_esc(grupo.nota)}", _NORMAL))

    # --- Activos -----------------------------------------------------------
    historia.append(Paragraph(
        f"Activos ({len(reservas)})" if len(reservas) > 1 else "Activo", _SECCION,
    ))

    # "Entr." en vez de un símbolo de check/casilla: igual que la flecha de
    # arriba, "✓"/"☐" están fuera de WinAnsi y Helvetica los pinta como un
    # cuadro relleno en vez del glifo -- texto plano se ve bien siempre.
    encabezados = ["Entr.", "N.º", "Activo", "Placa", "Marca/Modelo", "Serie/IMEI", "Ubicación", "Estado"]
    filas = [[Paragraph(h, _CELDA_ENCABEZADO) for h in encabezados]]
    for indice, r in enumerate(reservas, start=1):
        recurso = r.recurso
        nombre = recurso.nombre if recurso else f"Recurso #{r.recurso_id}"
        # Un `espacio` no tiene placa ni marca/modelo (lo impide el esquema,
        # ver recursos_placa_requerida_equipo en 02-schema.sql) -- "–" en vez
        # de una celda vacía, para que quede claro que no aplica y no que se
        # olvidó llenar.
        placa = recurso.placa if recurso and recurso.placa else "–"
        marca_modelo = (
            " ".join(p for p in (recurso.marca, recurso.modelo) if p) if recurso else ""
        ) or "–"
        identificador = recurso.identificador if recurso and recurso.identificador else "–"
        ubicacion = recurso.ubicacion if recurso and recurso.ubicacion else "–"
        filas.append([
            Paragraph("[&nbsp;&nbsp;]", _CELDA),
            Paragraph(str(indice), _CELDA),
            Paragraph(_esc(nombre), _CELDA),
            Paragraph(_esc(placa), _CELDA),
            Paragraph(_esc(marca_modelo), _CELDA),
            Paragraph(_esc(identificador), _CELDA),
            Paragraph(_esc(ubicacion), _CELDA),
            Paragraph(_esc(_ESTADOS_LEGIBLES.get(r.estado, r.estado)), _CELDA),
        ])

    anchos = [1.3 * cm, 0.8 * cm, 4.3 * cm, 1.9 * cm, 3.0 * cm, 2.7 * cm, 2.5 * cm, 2.2 * cm]
    # El total debe calzar en _ANCHO_UTIL; se ajusta la última columna con lo
    # que sobre/falte para no depender de que la suma de arriba sea exacta.
    anchos[-1] += _ANCHO_UTIL - sum(anchos)
    tabla_activos = Table(filas, colWidths=anchos, repeatRows=1)
    tabla_activos.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2f2f2f")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#999999")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f4f4")]),
    ]))
    historia.append(tabla_activos)
    historia.append(Spacer(1, 0.2 * cm))

    # --- Firmas --------------------------------------------------------------
    # Rejilla 2x2: fila de ENTREGA arriba, fila de DEVOLUCIÓN abajo, columna
    # Usuario / columna Administrador. Es una sola Table (no dos tablas
    # separadas) para que KeepTogether la trate como un único bloque que no
    # se reparte entre páginas -- los 4 cuadros son un solo documento legal,
    # partirlos entre hojas les quita valor.
    nombre_titular = (usuario.nombre if usuario and usuario.nombre else None) or (
        usuario.telefono_wa if usuario else None
    )
    subtitulo_usuario = f"Titular: {nombre_titular}" if nombre_titular else None

    datos_firmas = [
        [
            _celda_firma("ENTREGA", "Usuario", subtitulo_usuario),
            _celda_firma("ENTREGA", "Administrador (LACS)", None),
        ],
        [
            _celda_firma("DEVOLUCIÓN", "Usuario", subtitulo_usuario),
            _celda_firma("DEVOLUCIÓN", "Administrador (LACS)", None),
        ],
    ]
    tabla_firmas = Table(datos_firmas, colWidths=[_ANCHO_UTIL / 2, _ANCHO_UTIL / 2])
    tabla_firmas.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1.2, colors.black),
        ("INNERGRID", (0, 0), (-1, -1), 1.2, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))

    historia.append(KeepTogether([
        Paragraph("Entrega y devolución", _SECCION),
        tabla_firmas,
    ]))

    doc.build(historia)
    return buffer.getvalue()
