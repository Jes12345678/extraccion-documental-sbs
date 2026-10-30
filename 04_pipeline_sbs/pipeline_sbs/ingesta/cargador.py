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


@dataclass
class ResultadoCarga:
    """Registro de un archivo procesado (estado: ok, fallo o duplicado)."""

    archivo: str
    tipo: str
    formato: str
    estado: str
    motivo: str = ""
    detalle: str = ""
    paginas: int = 0
    caracteres: int = 0
    segundos: float = 0.0
    requiere_ocr: bool = False
    sha256: str = ""
    duplicado_de: str = ""
    texto: str = field(default="", repr=False)

    def como_dict(self) -> dict:
        """Devuelve el registro sin el texto, listo para una tabla."""
        datos = asdict(self)
        datos.pop("texto")
        return datos


class CargadorDocumentos:
    """Carga un lote de archivos mixtos sin que un archivo defectuoso detenga el proceso."""

    def __init__(self, carpeta, cuarentena, umbral_ocr: int = 20) -> None:
        self.carpeta = Path(carpeta)
        self.cuarentena = Path(cuarentena)
        self.umbral_ocr = umbral_ocr
        self._vistos: dict = {}      # huella -> nombre del primer archivo con ese contenido

    def cargar_uno(self, ruta) -> ResultadoCarga:
        """Procesa un archivo y devuelve su ResultadoCarga. Nunca lanza una excepción por el contenido del archivo."""
        ruta = Path(ruta)
        inicio = time.perf_counter()
        tipo = TIPO_POR_EXTENSION.get(ruta.suffix.lower(), "otro")
        sha, formato = "", "desconocido"
        resultado = None
        try:
            sha = calcular_sha256(ruta)
            formato = detectar_formato(ruta)
            validar_formato_y_extension(ruta, formato)
            verificar_apertura(ruta, formato)
            original = self._vistos.get(sha)
            if original is not None:
                resultado = ResultadoCarga(archivo=ruta.name, tipo=tipo, formato=formato, estado="duplicado",
                                           motivo="duplicado", detalle=f"mismo contenido que {original}",
                                           sha256=sha, duplicado_de=original)
            else:
                extraccion = extraer_texto(ruta, formato)
                texto = normalizar_texto(extraccion.texto)
                por_pagina = len(texto) / max(extraccion.paginas, 1)
                resultado = ResultadoCarga(
                    archivo=ruta.name, tipo=tipo, formato=formato, estado="ok", paginas=extraccion.paginas,
                    caracteres=len(texto), sha256=sha, texto=texto,
                    requiere_ocr=(formato in FORMATOS_IMAGEN or (formato == "pdf" and por_pagina < self.umbral_ocr)),
                )
                self._vistos[sha] = ruta.name
        except Exception as error:  # noqa: BLE001 - un archivo malo no debe detener el lote
            tipificado = error if isinstance(error, ErrorIngesta) else traducir_excepcion(error)
            detalle = (tipificado.detalle or str(tipificado)).replace(str(ruta), ruta.name)
            resultado = ResultadoCarga(archivo=ruta.name, tipo=tipo, formato=formato, estado="fallo",
                                       motivo=tipificado.motivo, detalle=detalle, sha256=sha)
            try:
                enviar_a_cuarentena(ruta, self.cuarentena, tipificado.motivo, detalle)
            except OSError:
                pass     # si la cuarentena tampoco se puede escribir, el lote igual continúa
        resultado.segundos = time.perf_counter() - inicio
        return resultado

    def cargar_lote(self) -> list:
        """Procesa todos los archivos de la carpeta, en orden alfabético, y devuelve un ResultadoCarga por archivo."""
        self._vistos = {}
        archivos = sorted(p for p in self.carpeta.iterdir() if p.is_file() and not p.name.startswith("."))
        return [self.cargar_uno(p) for p in archivos]


def tabla_resultados(resultados: list) -> pd.DataFrame:
    """Tabla con un registro por archivo (sin el texto)."""
    return pd.DataFrame([r.como_dict() for r in resultados])


def estadisticas(resultados: list) -> pd.DataFrame:
    """Resumen del lote por tipo de archivo, con una fila final TOTAL.

    El porcentaje que requiere OCR se calcula solo sobre los archivos ok.
    """

    def resumir(grupo: pd.DataFrame) -> dict:
        n = len(grupo)
        ok = int((grupo["estado"] == "ok").sum())
        fallo = int((grupo["estado"] == "fallo").sum())
        duplicado = int((grupo["estado"] == "duplicado").sum())
        con_ocr = int(((grupo["estado"] == "ok") & grupo["requiere_ocr"]).sum())
        return {
            "n": n, "ok": ok, "fallo": fallo, "duplicado": duplicado,
            "pct_ok": round(100 * ok / n, 1), "pct_fallo": round(100 * fallo / n, 1),
            "pct_duplicado": round(100 * duplicado / n, 1),
            "paginas": int(grupo["paginas"].sum()), "caracteres": int(grupo["caracteres"].sum()),
            "segundos_total": round(float(grupo["segundos"].sum()), 6),
            "segundos_mediana": round(float(grupo["segundos"].median()), 6),
            "requiere_ocr": con_ocr, "pct_requiere_ocr": round(100 * con_ocr / ok, 1) if ok else 0.0,
        }

    tabla = tabla_resultados(resultados)
    filas = {tipo: resumir(grupo) for tipo, grupo in tabla.groupby("tipo", sort=True)}
    filas["TOTAL"] = resumir(tabla)
    return pd.DataFrame.from_dict(filas, orient="index")
