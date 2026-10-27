"""Utilitários de texto do projeto de exemplo do E-9."""
import unicodedata


def sem_acentos(texto: str) -> str:
    """Remove acentos e cedilha: 'Ação é útil' -> 'Acao e util'."""
    normal = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in normal if not unicodedata.combining(c))
