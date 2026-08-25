import unicodedata


def normalizar(texto: str) -> str:
    """minúsculas y sin tildes, para comparar texto de usuario contra
    palabras clave sin que un acento (p. ej. "resérvame" vs "reserv")
    rompa el match."""
    texto = texto.lower()
    return "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )
