"""Métricas para medir el OCR: tasa de error de caracteres (CER) e intersección dividida entre unión (IoU)."""
from __future__ import annotations

import re
import unicodedata

from rapidfuzz.distance import Levenshtein


def _limpiar(texto: str) -> str:
    """Unifica la escritura de las tildes, junta los espacios y recorta los extremos."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", texto)).strip()


def cer(referencia: str, hipotesis: str) -> float:
    """Tasa de error de caracteres: ediciones necesarias / largo de la referencia.

    Args:
        referencia: texto correcto.
        hipotesis: texto que leyó el OCR.

    Returns:
        0.0 si son iguales; puede superar 1.0 si el OCR inventa mucho texto.
        Con referencia vacía: 0.0 si la hipótesis también es vacía y 1.0 si no.
    """
    referencia, hipotesis = _limpiar(referencia), _limpiar(hipotesis)
    if not referencia:
        return 0.0 if not hipotesis else 1.0
    return Levenshtein.distance(referencia, hipotesis) / len(referencia)