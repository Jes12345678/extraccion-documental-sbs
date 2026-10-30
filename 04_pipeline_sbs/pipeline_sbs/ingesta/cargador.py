"""Cargador de lotes: abre cada archivo según su contenido, tipifica los fallos y calcula estadísticas."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import statistics
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd
import pymupdf

from pipeline_sbs.ingesta.errores import (
    ErrorArchivoVacio,
    ErrorDocxCorrupto,
    ErrorExtensionFalsa,
    ErrorFormatoNoSoportado,
    ErrorIngesta,
    ErrorPdfCifrado,
    ErrorPdfDanado,
    traducir_excepcion,
)
from pipeline_sbs.ingesta.extractores import extraer_texto
from pipeline_sbs.preprocesamiento.normalizacion import normalizar_texto

registro = logging.getLogger(__name__)

EXTENSIONES_POR_FORMATO = {
    "pdf": (".pdf",), "png": (".png",), "jpg": (".jpg", ".jpeg"), "tif": (".tif", ".tiff"),
    "docx": (".docx",), "eml": (".eml",), "html": (".html", ".htm"),
}
TIPO_POR_EXTENSION = {
    ".pdf": "pdf", ".png": "imagen", ".jpg": "imagen", ".jpeg": "imagen", ".tif": "imagen", ".tiff": "imagen",
    ".docx": "word", ".eml": "correo", ".html": "html", ".htm": "html",
}
FORMATOS_IMAGEN = ("png", "jpg", "tif")


def calcular_sha256(ruta):
    """Huella SHA-256 del contenido de un archivo, leído por bloques."""
    resumen = hashlib.sha256()
    with open(ruta, "rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            resumen.update(bloque)
    return resumen.hexdigest()


def detectar_formato(ruta):
    """Detecta el formato por la firma del contenido, no por la extensión."""
    ruta = Path(ruta)
    if ruta.stat().st_size == 0:
        return "vacio"
    with ruta.open("rb") as archivo:
        cabecera = archivo.read(2048)
    if b"%PDF-" in cabecera[:1024]:
        return "pdf"
    if cabecera.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if cabecera.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if cabecera.startswith((b"II*\x00", b"MM\x00*")):
        return "tif"
    if cabecera.startswith(b"PK\x03\x04"):
        return "docx" if ruta.suffix.lower() == ".docx" else "zip"
    texto = cabecera.decode("utf-8", errors="ignore").lstrip("﻿ \t\r\n").lower()
    if texto.startswith("<!doctype html") or "<html" in texto[:512]:
        return "html"
    cabeceras = re.findall(r"^(from|to|subject|date|mime-version|content-type|message-id):", texto, re.MULTILINE)
    if re.match(r"[a-z][\w-]*:", texto) and len(set(cabeceras)) >= 2:
        return "eml"
    return "desconocido"


def validar_formato_y_extension(ruta, formato):
    """Rechaza archivos vacíos, de formato desconocido o con extensión falsa."""
    ruta = Path(ruta)
    if formato == "vacio":
        raise ErrorArchivoVacio("el archivo tiene 0 bytes")
    if formato not in EXTENSIONES_POR_FORMATO:
        raise ErrorFormatoNoSoportado(f"formato '{formato}' no soportado")
    if ruta.suffix.lower() not in EXTENSIONES_POR_FORMATO[formato]:
        raise ErrorExtensionFalsa(f"la extensión '{ruta.suffix.lower()}' no corresponde al contenido ({formato})")


def verificar_apertura(ruta, formato):
    """Comprueba que el archivo se puede abrir y lanza el error tipificado que corresponde."""
    ruta = Path(ruta)
    if formato == "pdf":
        try:
            doc = pymupdf.open(str(ruta))
        except (pymupdf.FileDataError, pymupdf.EmptyFileError) as error:
            raise ErrorPdfDanado(f"{type(error).__name__}: no se pudo abrir el PDF") from error
        with doc:
            if doc.needs_pass:
                raise ErrorPdfCifrado("el PDF pide contraseña")
            if doc.is_repaired:
                raise ErrorPdfDanado("el PDF estaba dañado y solo se abrió tras una reparación automática")
    elif formato == "docx":
        if not zipfile.is_zipfile(ruta):
            raise ErrorDocxCorrupto("el archivo no es un ZIP válido")


def enviar_a_cuarentena(ruta, cuarentena, motivo, detalle=""):
    """Copia (no mueve) un archivo defectuoso a la cuarentena y deja junto a él su motivo."""
    ruta, cuarentena = Path(ruta), Path(cuarentena)
    cuarentena.mkdir(parents=True, exist_ok=True)
    destino = cuarentena / ruta.name
    temporal = destino.with_name(destino.name + ".tmp")
    shutil.copy2(ruta, temporal)
    temporal.replace(destino)
    nota = {"archivo": ruta.name, "motivo": motivo, "detalle": detalle[:300]}
    destino.with_name(destino.name + ".motivo.json").write_text(json.dumps(nota, ensure_ascii=False, indent=2), encoding="utf-8")
    return destino
