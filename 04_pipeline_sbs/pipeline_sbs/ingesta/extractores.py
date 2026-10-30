"""Extractores de texto por formato: PDF (tres bibliotecas), DOCX, correo EML, HTML e imagen."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from pathlib import Path

import pdfplumber
import pymupdf
import pypdf
from bs4 import BeautifulSoup
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from PIL import Image

from pipeline_sbs.ingesta.errores import ErrorFormatoNoSoportado

pymupdf.TOOLS.mupdf_display_errors(False)


@dataclass(frozen=True)
class Extraccion:
    """Resultado de extraer un archivo: texto, páginas y caracteres de cada página."""

    texto: str
    paginas: int
    caracteres_por_pagina: tuple = ()
    metadatos: dict = field(default_factory=dict)

    @property
    def caracteres(self) -> int:
        return len(self.texto.strip())


def extraccion_desde_paginas(textos_por_pagina):
    """Arma una Extraccion a partir del texto de cada página."""
    return Extraccion(
        texto="\n".join(textos_por_pagina),
        paginas=len(textos_por_pagina),
        caracteres_por_pagina=tuple(len(t.strip()) for t in textos_por_pagina),
    )


def extraer_pdf_pypdf(ruta):
    """Texto de un PDF con pypdf (BSD-3), una entrada por página."""
    lector = pypdf.PdfReader(str(ruta))
    return extraccion_desde_paginas([pagina.extract_text() or "" for pagina in lector.pages])


def extraer_pdf_pdfplumber(ruta):
    """Texto de un PDF con pdfplumber (MIT), una entrada por página."""
    with pdfplumber.open(str(ruta)) as pdf:
        textos = [pagina.extract_text() or "" for pagina in pdf.pages]
    return extraccion_desde_paginas(textos)


def extraer_pdf_pymupdf(ruta):
    """Texto de un PDF con PyMuPDF (AGPL o comercial), una entrada por página."""
    with pymupdf.open(str(ruta)) as doc:
        textos = [pagina.get_text() for pagina in doc]
    return extraccion_desde_paginas(textos)


def filas_de_tabla(tabla):
    """Filas de una tabla de Word como texto 'celda | celda', sin repetir las celdas combinadas."""
    filas = []
    for fila in tabla.rows:
        vistas, celdas = set(), []
        for celda in fila.cells:
            if id(celda._tc) in vistas:
                continue
            vistas.add(id(celda._tc))
            celdas.append(celda.text.strip())
        filas.append(" | ".join(celdas))
    return filas


def extraer_docx(ruta):
    """Párrafos y tablas de nivel superior de un .docx, en el orden del documento."""
    documento = Document(str(ruta))
    bloques = []
    for hijo in documento.element.body.iterchildren():      # recorre el cuerpo en el orden del documento
        if hijo.tag.endswith("}p"):
            bloques.append(Paragraph(hijo, documento).text)
        elif hijo.tag.endswith("}tbl"):
            bloques.extend(filas_de_tabla(Table(hijo, documento)))
    texto = "\n".join(bloques)
    return Extraccion(texto=texto, paginas=1, caracteres_por_pagina=(len(texto.strip()),),
                      metadatos={"tablas": len(documento.tables)})


def extraer_eml(ruta):
    """Cuerpo de un correo .eml y nombres de sus adjuntos."""
    with open(ruta, "rb") as archivo:
        mensaje = BytesParser(policy=policy.default).parse(archivo)
    cuerpo = mensaje.get_body(preferencelist=("plain", "html"))
    texto = cuerpo.get_content() if cuerpo is not None else ""
    adjuntos = [Path(p.get_filename() or "sin_nombre").name for p in mensaje.iter_attachments()]
    return Extraccion(texto=texto, paginas=1, caracteres_por_pagina=(len(texto.strip()),),
                      metadatos={"asunto": str(mensaje.get("Subject", "")), "adjuntos": adjuntos})


def decodificar_html(contenido: bytes) -> str:
    """Convierte bytes de HTML en texto: BOM, charset declarado, UTF-8 y, al final, cp1252."""
    if contenido.startswith(b"\xef\xbb\xbf"):
        return contenido.decode("utf-8-sig")
    declarado = re.search(rb"charset\s*=\s*[\"']?\s*([A-Za-z0-9_\-]+)", contenido[:2048], re.IGNORECASE)
    if declarado:
        try:
            return contenido.decode(declarado.group(1).decode("ascii"))
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return contenido.decode("utf-8")
    except UnicodeDecodeError:
        return contenido.decode("cp1252", errors="replace")


def extraer_html(ruta):
    """Texto visible de un HTML, sin script ni estilo."""
    sopa = BeautifulSoup(decodificar_html(Path(ruta).read_bytes()), "html.parser")
    for etiqueta in sopa(["script", "style", "template"]):
        etiqueta.decompose()
    texto = sopa.get_text("\n", strip=True)
    return Extraccion(texto=texto, paginas=1, caracteres_por_pagina=(len(texto.strip()),))


def extraer_imagen(ruta):
    """Una imagen no tiene texto seleccionable: texto vacío, una página y su tamaño."""
    with Image.open(ruta) as imagen:
        ancho, alto = imagen.size
    return Extraccion(texto="", paginas=1, caracteres_por_pagina=(0,), metadatos={"ancho": ancho, "alto": alto})


def extraer_texto(ruta, formato):
    """Elige el extractor de E1 según el formato detectado."""
    if formato == "pdf":
        return extraer_pdf_pymupdf(ruta)
    if formato == "docx":
        return extraer_docx(ruta)
    if formato == "eml":
        return extraer_eml(ruta)
    if formato == "html":
        return extraer_html(ruta)
    if formato in ("png", "jpg", "tif"):
        return extraer_imagen(ruta)
    raise ErrorFormatoNoSoportado(f"no hay extractor para el formato '{formato}'")
