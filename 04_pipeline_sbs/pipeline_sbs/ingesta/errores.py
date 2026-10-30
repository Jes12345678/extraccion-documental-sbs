"""Errores tipificados de la ingesta: cada modo de fallo tiene su clase y un motivo estable."""
from __future__ import annotations

import zipfile


class ErrorIngesta(Exception):
    """Base de los errores de ingesta. `motivo` es el código estable que se registra."""

    motivo = "error_inesperado"

    def __init__(self, detalle: str = "") -> None:
        super().__init__(detalle or self.motivo)
        self.detalle = detalle


class ErrorArchivoVacio(ErrorIngesta):
    """El archivo tiene 0 bytes."""

    motivo = "archivo_vacio"


class ErrorFormatoNoSoportado(ErrorIngesta):
    """El contenido no corresponde a ningún formato que el pipeline procese."""

    motivo = "formato_no_soportado"


class ErrorExtensionFalsa(ErrorIngesta):
    """La extensión declara un formato y la firma del contenido indica otro."""

    motivo = "extension_falsa"


class ErrorPdfCifrado(ErrorIngesta):
    """El PDF pide contraseña."""

    motivo = "pdf_cifrado"


class ErrorPdfDanado(ErrorIngesta):
    """El PDF está truncado o dañado (o solo se abre tras una reparación automática)."""

    motivo = "pdf_danado"


class ErrorDocxCorrupto(ErrorIngesta):
    """El .docx no es un ZIP válido o no contiene un documento Word."""

    motivo = "docx_corrupto"


class ErrorImagenInvalida(ErrorIngesta):
    """La imagen no se puede identificar."""

    motivo = "imagen_invalida"


class ErrorArchivoInaccesible(ErrorIngesta):
    """El sistema operativo no dejó leer el archivo."""

    motivo = "archivo_inaccesible"


def traducir_excepcion(error: BaseException) -> ErrorIngesta:
    """Convierte una excepción de biblioteca en un error tipificado."""
    if isinstance(error, ErrorIngesta):
        return error
    nombre = type(error).__name__
    detalle = f"{nombre}: {error}"
    if isinstance(error, zipfile.BadZipFile) or nombre in ("BadZipFile", "PackageNotFoundError"):
        return ErrorDocxCorrupto(detalle)
    if nombre in ("UnidentifiedImageError", "DecompressionBombError"):
        return ErrorImagenInvalida(detalle)
    if nombre in ("FileNotDecryptedError", "WrongPasswordError"):
        return ErrorPdfCifrado(detalle)
    if nombre in ("FileDataError", "EmptyFileError", "PdfReadError", "PdfStreamError", "PdfminerException"):
        return ErrorPdfDanado(detalle)
    if isinstance(error, OSError):
        return ErrorArchivoInaccesible(detalle)
    return ErrorIngesta(detalle)
