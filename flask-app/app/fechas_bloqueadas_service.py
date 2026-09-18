"""
Lista negra de fechas: días en que el bot NO acepta reservas (pestaña
"Excepciones" del dashboard). Prioridad:
1. Excepción manual del día (`fechas_excepcion`): 'bloqueo' / 'desbloqueo'.
2. Festivos de Colombia (paquete `holidays`) si `calendario_reglas.festivos_co`.
3. Días de la semana en `calendario_reglas.dias_semana_bloqueados` (0=lunes).
Todo el que necesite saber si una fecha se puede reservar pasa por aquí.
Ver docs/ARQUITECTURA.md, sección "Excepciones: lista negra de fechas".
"""
from datetime import date, timedelta

import holidays

from app.models import CalendarioReglas, FechaExcepcion

FESTIVOS_CO = holidays.country_holidays("CO")  # se expande solo a cada año consultado
DIAS_SEMANA_POR_DEFECTO = [5, 6]
MAX_DIAS_RANGO = 400
TIPOS_EXCEPCION = ("bloqueo", "desbloqueo")
_DIAS_PLURAL = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábados", "domingos")


def obtener_reglas() -> CalendarioReglas:
    # La fila la siembra 02-schema.sql. Si alguien la borró, se usa un objeto
    # transitorio con los valores por defecto SIN escribir: esto lo llama el bot
    # en medio de su propia transacción.
    return CalendarioReglas.query.get(1) or CalendarioReglas(
        id=1, dias_semana_bloqueados=list(DIAS_SEMANA_POR_DEFECTO), festivos_co=True)


def serializar_reglas(r: CalendarioReglas) -> dict:
    return {"dias_semana": sorted(r.dias_semana_bloqueados or []),
            "festivos_co": bool(r.festivos_co),
            "actualizado_en": r.actualizado_en.isoformat() if r.actualizado_en else None}


def _describir(d: date, reglas, exc) -> dict:
    festivo = FESTIVOS_CO.get(d)
    por_semana = d.weekday() in set(reglas.dias_semana_bloqueados or [])
    por_festivo = bool(festivo) and bool(reglas.festivos_co)
    por_regla = por_semana or por_festivo
    if exc is not None:
        bloqueada = exc.tipo == "bloqueo"
        origen = "manual" if bloqueada else "desbloqueada"
    else:
        bloqueada = por_regla
        origen = "festivo" if por_festivo else ("dia_semana" if por_semana else None)
    return {
        "fecha": d.isoformat(), "dia_semana": d.weekday(),
        "bloqueada": bloqueada, "origen": origen,
        "festivo": festivo or None,          # nombre aunque festivos_co esté apagado (informativo)
        "bloqueada_por_regla": por_regla,
        "excepcion": None if exc is None else {
            "tipo": exc.tipo, "motivo": exc.motivo,
            "actualizado_en": exc.actualizado_en.isoformat() if exc.actualizado_en else None},
    }


def dias_en_rango(desde: date, hasta: date) -> list[dict]:
    """Solo los días "relevantes" del rango: con regla de día de semana,
    festivo (activo o no) o excepción. Los días normales no se devuelven."""
    reglas = obtener_reglas()
    excepciones = {e.fecha: e for e in FechaExcepcion.query.filter(
        FechaExcepcion.fecha >= desde, FechaExcepcion.fecha <= hasta).all()}
    bloq = set(reglas.dias_semana_bloqueados or [])
    dias, d = [], desde
    while d <= hasta:
        if d.weekday() in bloq or d in FESTIVOS_CO or d in excepciones:
            dias.append(_describir(d, reglas, excepciones.get(d)))
        d += timedelta(days=1)
    return dias


def describir_fecha(d: date) -> dict:
    return _describir(d, obtener_reglas(), FechaExcepcion.query.get(d))


def es_fecha_bloqueada(d: date) -> bool:
    return describir_fecha(d)["bloqueada"]


def motivo_bloqueo(d: date) -> str | None:
    """Cláusula lista para WhatsApp ("es festivo (Día de la Raza)"); None si se puede reservar."""
    info = describir_fecha(d)
    if not info["bloqueada"]:
        return None
    if info["origen"] == "manual":
        motivo = (info["excepcion"] or {}).get("motivo")
        return f"la CPA no presta servicio ese día ({motivo})" if motivo else "la CPA no presta servicio ese día"
    if info["origen"] == "festivo":
        return f"es festivo ({info['festivo']})"
    return f"la CPA no presta servicio los {_DIAS_PLURAL[d.weekday()]}"


def siguiente_fecha_habilitada(desde: date, limite: int = 366) -> date | None:
    """Primer día >= `desde` que NO está bloqueado (una sola consulta a la base)."""
    bloqueadas = {x["fecha"] for x in dias_en_rango(desde, desde + timedelta(days=limite)) if x["bloqueada"]}
    for i in range(limite + 1):
        d = desde + timedelta(days=i)
        if d.isoformat() not in bloqueadas:
            return d
    return None


def texto_reglas() -> str | None:
    """"No se reciben reservas los sábados, domingos ni festivos" -- None si no hay reglas."""
    r = obtener_reglas()
    partes = [_DIAS_PLURAL[i] for i in sorted(r.dias_semana_bloqueados or [])]
    if r.festivos_co:
        partes.append("festivos")
    if not partes:
        return None
    lista = partes[0] if len(partes) == 1 else ", ".join(partes[:-1]) + " ni " + partes[-1]
    return f"No se reciben reservas los {lista}"
