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
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    Flowable,
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
    "entregada": "Entregada",
    "devuelta_tarde": "Devuelta tarde",
    "no_devuelta": "No devuelta",
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
# Para las celdas "[  ]" de las columnas Entr./Dev.: a diferencia de las
# demás columnas de la tabla, acá SÍ importa que el texto quede centrado
# dentro de la celda (no solo alineado a la izquierda), para que el ancla del
# chulito (ver _Ancla más abajo y generar_acta_con_anclas) quede centrada
# sobre la caja "[  ]" en vez de pegada a su borde izquierdo.
_CELDA_CENTRADA = ParagraphStyle("ActaCeldaCentrada", parent=_CELDA, alignment=1)
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


class _Ancla(Flowable):
    """Marca invisible que anota su posición absoluta en el momento del
    `build`: reportlab coloca los flowables al construir el documento, así
    que la posición solo se conoce aquí dentro (no se puede calcular antes a
    mano -- depende de cuánto texto trajo cada sección previa). Alto y ancho
    en 0 para no ocupar espacio real en el layout: es puramente un punto de
    referencia. `canv.absolutePosition(0, 0)` devuelve la posición en puntos
    desde la esquina inferior izquierda de la PÁGINA -- el mismo sistema de
    coordenadas que usa pdf-lib en el navegador (ver
    dashboard-frontend/src/features/reservas/actas-pdf.ts), así que las
    coordenadas que se guardan acá se usan tal cual del otro lado, sin
    invertir ejes ni convertir orígenes.

    `despl_x` corre el ancla hacia la derecha antes de guardarla -- lo usan
    las anclas de fecha y de C.C. para apuntar ya al primer hueco escribible
    de la línea (después de la etiqueta "Fecha y hora " / "C.C. "), no al
    inicio del párrafo. Antes ese desplazamiento lo calculaba el NAVEGADOR
    midiendo el ancho de la etiqueta con la misma fuente/tamaño -- obligaba a
    mantener la cadena literal de la etiqueta igual en dos archivos y en dos
    lenguajes (reportlab acá, pdf-lib en actas-pdf.ts), y eso ya causó un bug
    por desalineación en una iteración anterior. Calculando el
    desplazamiento acá, con `stringWidth`, el backend entrega la coordenada
    final y el navegador solo dibuja en `ancla.x` sin saber nada de la
    etiqueta."""

    def __init__(self, clave: str, destino: dict, ancho: float, alto: float, *, despl_x: float = 0.0):
        super().__init__()
        self.width = 0
        self.height = 0
        self._clave, self._destino = clave, destino
        self._ancho, self._alto = ancho, alto
        self._despl_x = despl_x

    def draw(self):
        x, y = self.canv.absolutePosition(0, 0)
        x += self._despl_x
        self._destino[self._clave] = {
            "pagina": self.canv.getPageNumber(),
            "x": round(x, 2), "y": round(y, 2),
            "ancho": round(self._ancho, 2), "alto": round(self._alto, 2),
        }


# Ancho real del hueco de firma dentro de una celda de `tabla_firmas`: la
# celda mide _ANCHO_UTIL/2, pero LEFTPADDING/RIGHTPADDING de 9pt (ver
# TableStyle de `tabla_firmas` más abajo) le restan 18pt, y el HRFlowable de
# la raya ocupa el 85% de lo que queda -- este cálculo tiene que replicar
# exactamente esos dos números o la caja que reciba pdf-lib no coincidiría
# con la raya que se ve en el PDF. Verificado a ojo con un script suelto que
# genera un acta real, estampa un rectángulo en cada ancla y mira el PDF
# resultante (no está en el repo -- ver la verificación de esta tarea): con
# este ancho, el rectángulo queda centrado sobre la raya de "Firma".
_ANCHO_FIRMA = 0.85 * (_ANCHO_UTIL / 2 - 18)
# Alto del hueco en blanco reservado para firmar a mano -- lo pide el flujo
# físico del acta, no es relleno visual. La zona de firma que se reporta en
# `anclas[clave]` es más alta que esto: además de este hueco incluye la raya
# y la etiqueta "Firma" (ver el bloque `__top`/`__bottom` más abajo), para
# que el recorte de fondo que ve quien firma (recorte-acta.ts) traiga la
# raya sobre la que escribir en vez de salir en blanco.
_ALTO_FIRMA = 2 * cm
# Ancho útil de la línea de C.C./Fecha dentro de la celda: mismo ancho de
# celda que _ANCHO_FIRMA pero sin el recorte del 85% (esas líneas de texto no
# necesitan ese margen), y el alto de una línea de _FIRMA_PIE (fontSize 8.5,
# leading 11).
_ANCHO_FECHA = _ANCHO_UTIL / 2 - 18
_ALTO_FECHA = 11
# Alto del hueco reservado para la nota del administrador (entrega y
# devolución tienen el suyo, ver más abajo): 2 líneas de 9pt de interlineado
# a lo ANCHO de toda la página -- de sobra para los 300 del VARCHAR de la
# columna (a ese ancho entran ~300 caracteres en 2 líneas). Bajado de 3 a 2
# líneas: con 3 una reserva de varios activos (tabla larga) se corría a una
# segunda página (verificado generando el acta real, ver
# verificar_nota_acta.py). Lo dibuja pdf-lib al firmar en pantalla (ver
# dashboard-frontend/.../actas-pdf.ts), no reportlab -- acá solo se reserva
# el espacio en blanco y se ancla su posición.
_ALTO_NOTA = 2 * 9
# Cuántos guiones lleva "Fecha y hora ____...": se probaron varios valores
# con `stringWidth` y se generó el PDF real para elegir uno que deje la línea
# con un margen cómodo dentro de _ANCHO_FECHA (con 30, sobran ~63pt de 257pt
# -- ni se corta ni queda una raya ridículamente corta) -- verificado
# mirando el PDF, no a ojo de cálculo. Si se cambia la etiqueta o el tamaño
# de letra, hay que volver a comprobarlo.
_GUIONES_FECHA = 30

# Ancho de las etiquetas "Fecha y hora " y "C.C. " con la fuente/tamaño real
# de la línea (_FIRMA_PIE) -- lo que antes medía el NAVEGADOR con
# `widthOfTextAtSize` para correr la fecha a la derecha de su etiqueta (ver
# el historial de actas-pdf.ts). Migrado acá: el backend entrega la ancla ya
# desplazada al primer hueco escribible (ver `_Ancla.despl_x`) y el navegador
# deja de necesitar la cadena literal de la etiqueta -- ya hubo un bug por
# mantener esa cadena sincronizada en dos archivos y dos lenguajes a la vez.
_ANCHO_ETIQUETA_FECHA = stringWidth("Fecha y hora ", _FIRMA_PIE.fontName, _FIRMA_PIE.fontSize)
_ANCHO_ETIQUETA_CC = stringWidth("C.C. ", _FIRMA_PIE.fontName, _FIRMA_PIE.fontSize)


def _celda_firma(
    titulo_fila: str, rol: str, subtitulo: str | None, clave: str, destino: dict, cedula: str | None = None
) -> list:
    """Contenido de un cuadro de firma: título (ENTREGA/DEVOLUCIÓN), rol,
    titular preimpreso si aplica, espacio para firmar a mano y los campos que
    se llenan en el momento.

    `cedula`, cuando viene con valor, se imprime en la línea de C.C. (petición
    explícita del usuario: antes esta línea quedaba SIEMPRE en blanco a
    propósito, para que se llenara a mano junto con la firma -- esa decisión
    quedó revocada, ahora el servidor la imprime porque ya la tiene en
    `usuario.cedula` y así también sale impresa en el acta que se archiva en
    papel). Solo la reciben los dos cuadros del USUARIO -- los del
    administrador siguen con la línea en blanco, porque esa cédula la teclea
    quien firma en el diálogo del dashboard y la estampa el navegador (ver
    `..._cc` más abajo y actas-pdf.ts). Si `cedula` es `None` o vacía (hay
    usuarios sin cédula cargada), se conserva la línea con guiones.

    `clave` es el nombre con el que esta caja de firma queda en `destino` (el
    dict de anclas que arma `generar_acta_con_anclas`): `clave` para la zona
    de firma, `clave + "_fecha"` para el hueco de la fecha y `clave + "_cc"`
    para el hueco de la C.C. (siempre se genera, aunque el cuadro del usuario
    ya venga impreso -- el administrador sí la necesita al firmar).

    La zona de firma (`clave`) se arma con dos anclas AUXILIARES (`clave +
    "__top"` y `clave + "__bottom"`) que `generar_acta_con_anclas` combina en
    una sola después del `build()`, y que se borran del diccionario final:
    son un cálculo intermedio, no algo que el navegador deba conocer. Esa
    zona incluye a propósito la raya de "Firma" y su etiqueta -- no solo el
    hueco en blanco de encima -- porque si el recorte de fondo que ve quien
    firma (recorte-acta.ts) terminara justo en el borde del hueco, saldría
    completamente en blanco: no traería ninguna raya sobre la que firmar, y
    pdf.js no aportaría nada frente a simplemente no mostrar nada."""
    if cedula:
        linea_cc = f"C.C. {_esc(cedula)}"
    else:
        linea_cc = "C.C. " + "_" * 22

    contenido = [
        Paragraph(_esc(titulo_fila), _FIRMA_TITULO_FILA),
        Paragraph(_esc(rol), _FIRMA_ROL),
    ]
    if subtitulo:
        contenido.append(Paragraph(_esc(subtitulo), _FIRMA_TITULAR))
    contenido += [
        # Alto para firmar a mano -- lo pide el flujo físico del acta, no es
        # relleno visual.
        Spacer(1, _ALTO_FIRMA),
        # Marca el borde superior de la zona de firma reportada: su "y" es la
        # raya (justo antes del HRFlowable); `generar_acta_con_anclas` le suma
        # _ALTO_FIRMA para que el borde superior real incluya también el
        # hueco en blanco de encima (ver el docstring de esta función).
        _Ancla(f"{clave}__top", destino, 0, 0),
        HRFlowable(width="85%", thickness=0.75, color=colors.black, hAlign="CENTER"),
        Paragraph("Firma", _FIRMA_PIE_CENTRADO),
        # Marca el borde inferior de la zona de firma: justo DESPUÉS del
        # párrafo "Firma", así la zona reportada cubre la raya y la etiqueta
        # completas (ver A5 del plan) en vez de cortarlas por la mitad.
        _Ancla(f"{clave}__bottom", destino, 0, 0),
        Spacer(1, 0.15 * cm),
        Paragraph(linea_cc, _FIRMA_PIE),
        # despl_x corre el ancla hasta el primer hueco escribible después de
        # "C.C. " -- ver el docstring de _Ancla. Ancho: lo que le queda a la
        # línea después de la etiqueta.
        _Ancla(f"{clave}_cc", destino, _ANCHO_FECHA - _ANCHO_ETIQUETA_CC, _ALTO_FECHA, despl_x=_ANCHO_ETIQUETA_CC),
        Spacer(1, 0.1 * cm),
        Paragraph("Fecha y hora " + "_" * _GUIONES_FECHA, _FIRMA_PIE),
        # Mismo criterio que la ancla de C.C., con la etiqueta "Fecha y
        # hora " -- ver el docstring de _Ancla sobre por qué el desplazamiento
        # se calcula acá y no en el navegador.
        _Ancla(
            f"{clave}_fecha",
            destino,
            _ANCHO_FECHA - _ANCHO_ETIQUETA_FECHA,
            _ALTO_FECHA,
            despl_x=_ANCHO_ETIQUETA_FECHA,
        ),
    ]
    return contenido


def generar_acta_con_anclas(reservas: list[Reserva]) -> tuple[bytes, dict]:
    """PDF del acta de entrega y devolución para todas las líneas de un mismo
    `grupo_id` (ya ordenadas por quien llama, ver `_reservas_del_grupo`), más
    el diccionario de anclas de firma (ver `_Ancla`) que el navegador usa con
    pdf-lib para saber dónde estampar cada firma (y, solo para el
    administrador, su cédula) y con pdf.js para recortar el fondo de la zona
    de firma (ver dashboard-frontend/src/features/reservas/recorte-acta.ts).
    Claves: `entrega_usuario`, `entrega_admin`, `devolucion_usuario`,
    `devolucion_admin`, cada una con `_fecha` (el hueco de la línea "Fecha y
    hora", ya desplazado más allá de la etiqueta) y `_cc` (el hueco de la
    línea "C.C.", igual de desplazado), más `entrega_nota` y
    `devolucion_nota` (el hueco de observaciones libres del administrador,
    ver `reserva_actas.nota`). A mano solo queda la firma: la cédula del
    usuario ya viene impresa (ver `_celda_firma`) y la fecha la estampa el
    navegador (ver actas-pdf.ts), así que `_fecha`, `_cc` y `_nota` son
    huecos que el navegador rellena sin necesitar medir nada del lado del
    PDF. Además, `entrega_linea_<id>` y `devolucion_linea_<id>` (id =
    `Reserva.id` de la fila) marcan la caja "[  ]" de cada fila -- el
    navegador dibuja ahí un chulito (entregado/devuelto) o una X (no
    entregado), ver FirmarActaDialog.tsx y actas-pdf.ts. Por id y no por
    número de fila: la lista de líneas que tiene el navegador puede venir
    filtrada (el calendario oculta las canceladas) y un índice posicional
    corría las marcas a la fila equivocada."""
    anclas: dict = {}
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

    # "Entr."/"Dev." en vez de un símbolo de check/casilla: igual que la
    # flecha de arriba, "✓"/"☐" están fuera de WinAnsi y Helvetica los pinta
    # como un cuadro relleno en vez del glifo -- texto plano se ve bien
    # siempre. Anchos definidos ACÁ (antes de armar las filas, no después)
    # porque cada fila necesita anchos[0]/anchos[1] para dimensionar el
    # ancla del chulito de su propia celda -- ver el bucle de abajo.
    encabezados = ["Entr.", "Dev.", "N.º", "Activo", "Placa", "Marca/Modelo", "Serie/IMEI", "Estado"]
    anchos = [
        1.1 * cm, 1.1 * cm, 0.8 * cm, 6.1 * cm, 1.9 * cm, 2.7 * cm, 2.7 * cm, 2.2 * cm,
    ]
    # El total debe calzar en _ANCHO_UTIL; se ajusta la última columna con lo
    # que sobre/falte para no depender de que la suma de arriba sea exacta.
    anchos[-1] += _ANCHO_UTIL - sum(anchos)
    # LEFTPADDING + RIGHTPADDING de la tabla (ver TableStyle más abajo): lo
    # que le queda de ancho útil a cada ancla de chulito dentro de su celda.
    _PADDING_CELDA = 3 + 3

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
        filas.append([
            # El ancla va DESPUÉS del Paragraph en la misma celda -- así su
            # "y" es la base de la línea de la caja "[  ]" (mismo truco que
            # `_celda_firma` con `__top`/`__bottom`), no la parte de arriba
            # de la celda. El navegador la usa para estampar el chulito de
            # este activo cuando el administrador lo marcó como
            # entregado/devuelto (ver actas-pdf.ts, `estamparFirmas`).
            [
                Paragraph("[&nbsp;&nbsp;]", _CELDA_CENTRADA),
                _Ancla(f"entrega_linea_{r.id}", anclas, anchos[0] - _PADDING_CELDA, 10),
            ],
            [
                Paragraph("[&nbsp;&nbsp;]", _CELDA_CENTRADA),
                _Ancla(f"devolucion_linea_{r.id}", anclas, anchos[1] - _PADDING_CELDA, 10),
            ],
            Paragraph(str(indice), _CELDA),
            Paragraph(_esc(nombre), _CELDA),
            Paragraph(_esc(placa), _CELDA),
            Paragraph(_esc(marca_modelo), _CELDA),
            Paragraph(_esc(identificador), _CELDA),
            Paragraph(_esc(_ESTADOS_LEGIBLES.get(r.estado, r.estado)), _CELDA),
        ])

    tabla_activos = Table(filas, colWidths=anchos, repeatRows=1)
    tabla_activos.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2f2f2f")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#999999")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        # OJO: NO incluir las columnas 0/1 (Entr./Dev.) acá aunque tengan
        # contenido "centrado" -- ese centrado ya lo hace _CELDA_CENTRADA
        # (alignment en el propio ParagraphStyle, ver arriba). Esta línea de
        # ALIGN es del Table/TableStyle: solo reposiciona un flowable cuyo
        # `wrap()` reporte un ancho MENOR al de la columna, y el `_Ancla` que
        # va junto al Paragraph en esas dos celdas reporta ancho 0 (ver
        # `_Ancla.__init__`, `self.width = 0` es fijo, el parámetro `ancho`
        # es solo metadata) -- si esta columna estuviera en el rango CENTER,
        # el TABLE correría el ancla hasta el CENTRO de la celda en vez de
        # dejarla en el borde izquierdo, y el ancho reportado (`ancho_col -
        # padding`) ya no significaría nada coherente para quien la usa
        # (verificado generando el acta real y comparando `ancla.x` con y sin
        # esta columna en el rango -- con ella adentro, `ancla.x` salía en el
        # CENTRO de la celda, no en su borde izquierdo, y el chulito quedaba
        # corrido hacia la columna siguiente). La columna N.º sigue acá
        # porque su celda es un Paragraph sin ancla -- inofensivo.
        ("ALIGN", (2, 0), (2, -1), "CENTER"),
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

    # La cédula solo se imprime en los cuadros del USUARIO -- la del
    # administrador se teclea en el diálogo de firma del dashboard y la
    # estampa el navegador sobre el hueco `..._admin_cc` (ver _celda_firma).
    cedula_usuario = usuario.cedula if usuario else None
    datos_firmas = [
        [
            _celda_firma(
                "ENTREGA", "Usuario", subtitulo_usuario, "entrega_usuario", anclas, cedula=cedula_usuario
            ),
            _celda_firma("ENTREGA", "Administrador (LACS)", None, "entrega_admin", anclas),
        ],
        [
            _celda_firma(
                "DEVOLUCIÓN", "Usuario", subtitulo_usuario, "devolucion_usuario", anclas, cedula=cedula_usuario
            ),
            _celda_firma("DEVOLUCIÓN", "Administrador (LACS)", None, "devolucion_admin", anclas),
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
        # Huecos reservados para la nota que el administrador deja al firmar
        # cada momento (opcional, reserva_actas.nota) -- el navegador la
        # dibuja con pdf-lib al estampar, acá solo se reserva el espacio y
        # se ancla su posición (ver _ALTO_NOTA y actas-pdf.ts).
        Spacer(1, 0.15 * cm),
        Paragraph("<b>Observaciones de la entrega (LACS):</b>", _FIRMA_PIE),
        Spacer(1, _ALTO_NOTA),
        _Ancla("entrega_nota", anclas, _ANCHO_UTIL, _ALTO_NOTA),
        Paragraph("<b>Observaciones de la devolución (LACS):</b>", _FIRMA_PIE),
        Spacer(1, _ALTO_NOTA),
        _Ancla("devolucion_nota", anclas, _ANCHO_UTIL, _ALTO_NOTA),
    ]))

    doc.build(historia)

    # Las anclas __top/__bottom solo existen para calcular la zona de firma
    # completa de cada cuadro -- se combinan acá en `anclas[clave]` y se
    # borran del diccionario final para no filtrar un detalle interno (el
    # nombre exacto de esas claves temporales) al navegador.
    #
    # `arriba["y"]` es la raya de "Firma" (donde se colocó la ancla __top,
    # justo antes del HRFlowable) y `arriba["y"] + _ALTO_FIRMA` es el borde
    # superior real: el hueco en blanco reservado para firmar queda POR
    # ENCIMA de la raya en el flujo del documento. `abajo["y"]` (la ancla
    # __bottom, colocada justo después del Paragraph "Firma") es el borde
    # inferior -- así la zona reportada incluye la raya y su etiqueta, no
    # solo el hueco en blanco (ver el docstring de _celda_firma sobre por qué
    # importa: si no, el recorte de fondo saldría en blanco).
    for clave in ("entrega_usuario", "entrega_admin", "devolucion_usuario", "devolucion_admin"):
        arriba = anclas.pop(f"{clave}__top")
        abajo = anclas.pop(f"{clave}__bottom")
        anclas[clave] = {
            "pagina": arriba["pagina"],
            "x": arriba["x"],
            "y": abajo["y"],
            # Redondeado a 2 decimales igual que el resto de las anclas (ver
            # _Ancla.draw) -- sin esto, "ancho" saldría con el float crudo de
            # _ANCHO_FIRMA en vez de la misma representación que el resto del
            # JSON.
            "ancho": round(_ANCHO_FIRMA, 2),
            "alto": round((arriba["y"] + _ALTO_FIRMA) - abajo["y"], 2),
        }

    return buffer.getvalue(), anclas


def generar_acta(reservas: list[Reserva]) -> bytes:
    """Compatibilidad con los llamadores que solo necesitan el PDF, sin las
    anclas de firma (ej. la vista previa del acta base antes de firmar)."""
    pdf, _anclas = generar_acta_con_anclas(reservas)
    return pdf
