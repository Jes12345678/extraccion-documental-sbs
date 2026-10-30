"""Normalización de texto extraído: Unicode, espacios, guiones de corte, fechas y montos en soles."""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal

ESPACIOS_ESPECIALES = "\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000"
INVISIBLES = "\u00ad\u200b\u200c\u200d\ufeff"          # guion blando, ancho cero, marca de orden de bytes
LIGADURAS = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl"}
FORMATOS_FECHA = ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d")


def normalizar_texto(texto):
    """NFC, caracteres invisibles fuera, espacios especiales cambiados por un espacio y espacios repetidos colapsados."""
    texto = unicodedata.normalize("NFC", texto)
    for invisible in INVISIBLES:
        texto = texto.replace(invisible, "")
    for ligadura, reemplazo in LIGADURAS.items():
        texto = texto.replace(ligadura, reemplazo)
    for especial in ESPACIOS_ESPECIALES:
        texto = texto.replace(especial, " ")
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    lineas = [re.sub(r"[ \t]+", " ", linea).strip() for linea in texto.split("\n")]
    return "\n".join(lineas).strip()


def unir_guiones(texto):
    """Une las palabras cortadas con guion al final de línea."""
    return re.sub(r"(?<=[a-záéíóúüñ])-\n(?=[a-záéíóúüñ])", "", texto)


def parsear_fecha(texto):
    """Fecha (date) o None si no es una fecha válida."""
    for formato in FORMATOS_FECHA:
        try:
            return datetime.strptime(texto.strip(), formato).date()
        except ValueError:
            continue
    return None


def parsear_monto(texto):
    """Monto en soles como Decimal, o None si no hay un número."""
    limpio = normalizar_texto(texto).replace("S/", "").replace(" ", "")
    if not re.search(r"\d", limpio):
        return None
    if "," in limpio and "." in limpio:
        decimal = "," if limpio.rfind(",") > limpio.rfind(".") else "."     # el separador que aparece último es el decimal
        miles = "." if decimal == "," else ","
        limpio = limpio.replace(miles, "").replace(decimal, ".")
    elif "," in limpio or "." in limpio:
        separador = "," if "," in limpio else "."
        partes = limpio.split(separador)
        limpio = "".join(partes) if len(partes) > 2 or len(partes[-1]) == 3 else ".".join(partes)
    return Decimal(limpio)
