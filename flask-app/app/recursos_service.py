"""
Disponibilidad de activos (recursos) contra datos reales de la base de
datos. Nada de esto debe delegarse al LLM: el LLM interpreta lenguaje
natural para llegar al intent, pero la respuesta sobre qué existe y qué
está disponible sale siempre de aquí (ver docs/ARQUITECTURA.md, sección
"Por qué separar el LLM de la lógica de reservas").
"""

import unicodedata
from datetime import datetime, timezone

from app.models import Recurso, Reserva

LONGITUD_MINIMA_PALABRA_CLAVE = 4


def _normalizar(texto: str) -> str:
    texto = texto.lower()
    return "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )


def buscar_recursos_en_texto(texto: str) -> tuple[list[Recurso], bool]:
    """Devuelve (recursos que mejor coinciden con el texto, es_match_debil).

    Usa un score de palabras coincidentes (no solo boolean) para que una
    palabra genérica compartida entre varios recursos (ej. "cámara" en dos
    modelos distintos) no produzca ambigüedad falsa cuando el mensaje en
    realidad menciona una palabra distintiva (ej. "sony") que solo coincide
    con uno.

    `es_match_debil` es True cuando el mejor score viene de una sola
    palabra genérica (ej. "micrófono", "cámara") sin mención del nombre
    completo ni de una segunda palabra distintiva -- casos donde el match
    puede ser un falso positivo (ej. "grúa de cámara" coincide con
    "Cámara Sony" solo por la palabra "cámara", pero es un activo distinto
    que no tenemos). El llamador debe usarlo para agregar una salvedad en
    vez de responder con confianza total.

    Si en el futuro hace falta más precisión, este es el punto de
    extensión para pasarle la lista de nombres al LLM y que elija.
    """
    texto_norm = _normalizar(texto)
    puntuados: list[tuple[int, Recurso]] = []

    for recurso in Recurso.query.filter_by(activo=True).all():
        nombre_norm = _normalizar(recurso.nombre)
        palabras = [p for p in nombre_norm.split() if len(p) >= LONGITUD_MINIMA_PALABRA_CLAVE]
        score = sum(1 for p in palabras if p in texto_norm)
        if nombre_norm in texto_norm:
            score += 10  # nombre completo mencionado: gana sobre cualquier match parcial
        if score > 0:
            puntuados.append((score, recurso))

    if not puntuados:
        return [], False

    mejor_score = max(score for score, _ in puntuados)
    candidatos = [recurso for score, recurso in puntuados if score == mejor_score]
    return candidatos, mejor_score == 1


def listar_nombres_disponibles() -> list[str]:
    recursos = Recurso.query.filter_by(activo=True).order_by(Recurso.tipo, Recurso.nombre).all()
    return [r.nombre for r in recursos]


def _reserva_en_curso(recurso_id: int, ahora: datetime) -> Reserva | None:
    return (
        Reserva.query.filter(
            Reserva.recurso_id == recurso_id,
            Reserva.estado == "confirmada",
            Reserva.inicio <= ahora,
            Reserva.fin > ahora,
        )
        .first()
    )


def _proxima_reserva(recurso_id: int, ahora: datetime) -> Reserva | None:
    return (
        Reserva.query.filter(
            Reserva.recurso_id == recurso_id,
            Reserva.estado == "confirmada",
            Reserva.inicio > ahora,
        )
        .order_by(Reserva.inicio.asc())
        .first()
    )


def evaluar_disponibilidad(texto_usuario: str) -> str:
    """Punto de entrada usado por el webhook para los intents 'reservar' y
    'consultar_disponibilidad'. Devuelve una respuesta lista para enviar,
    basada solo en datos reales de recursos/reservas -- nunca inventada."""
    candidatos, es_match_debil = buscar_recursos_en_texto(texto_usuario)

    if not candidatos:
        nombres = listar_nombres_disponibles()
        if not nombres:
            return "Por ahora no tengo activos cargados en el sistema. Contacta al equipo de la CPA."
        lista = "\n".join(f"- {n}" for n in nombres)
        return (
            "No identifiqué qué activo necesitas, o no lo tenemos disponible. "
            f"Esto es lo que sí tenemos:\n{lista}"
        )

    if len(candidatos) > 1:
        nombres = ", ".join(r.nombre for r in candidatos)
        return (
            f"Encontré varias coincidencias: {nombres}. ¿Cuál de estos necesitas? "
            "Si ninguno es lo que buscas, dime el nombre completo del activo."
        )

    recurso = candidatos[0]
    salvedad = (
        f" (si no te refieres a '{recurso.nombre}', dime el nombre completo del activo)"
        if es_match_debil
        else ""
    )
    ahora = datetime.now(timezone.utc)

    en_curso = _reserva_en_curso(recurso.id, ahora)
    if en_curso:
        hora_libre = en_curso.fin.strftime("%H:%M")
        return f"'{recurso.nombre}' está en uso ahora mismo. Queda libre a las {hora_libre}.{salvedad}"

    proxima = _proxima_reserva(recurso.id, ahora)
    if proxima:
        inicio = proxima.inicio.strftime("%d/%m %H:%M")
        fin = proxima.fin.strftime("%H:%M")
        return (
            f"'{recurso.nombre}' está libre en este momento, pero ya tiene una reserva "
            f"del {inicio} a las {fin}. Si la necesitas antes de esa hora, dime para cuándo."
            f"{salvedad}"
        )

    return (
        f"'{recurso.nombre}' está libre ahora mismo y no tiene reservas próximas. "
        f"Dime para cuándo la necesitas.{salvedad}"
    )
