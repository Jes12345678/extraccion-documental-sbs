"""Pipeline de OCR de mi_pipeline: PDF o imagen -> imagen enderezada -> receta -> lectura -> puntaje -> decisión."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pytesseract
import yaml
from PIL import Image

# P7: importaciones
from .ocr.preprocesar import aplicar_receta, corregir_inclinacion
from .ocr.renderizar import pdf_a_imagen

RUTA_CONFIG = Path(__file__).parent / "config" / "pipeline.yaml"


def cargar_configuracion(ruta: str | Path = RUTA_CONFIG) -> dict[str, Any]:
    """Lee el archivo YAML de configuración y lo entrega como diccionario."""
    with open(ruta, encoding="utf-8") as archivo:
        return yaml.safe_load(archivo)


def configurar_tesseract(cfg: dict[str, Any]) -> str:
    """Indica a pytesseract dónde está el programa tesseract.

    Orden de búsqueda: variable de entorno ``TESSERACT_CMD``, clave ``ocr.tesseract_cmd`` del YAML,
    el PATH y la carpeta estándar de instalación en Windows.

    Raises:
        RuntimeError: si no se encuentra Tesseract.
    """
    comando = os.environ.get("TESSERACT_CMD") or cfg["ocr"].get("tesseract_cmd") or shutil.which("tesseract")
    if not comando:
        estandar = Path(os.environ.get("ProgramFiles", "")) / "Tesseract-OCR" / "tesseract.exe"
        comando = str(estandar) if estandar.is_file() else None
    if not comando:
        raise RuntimeError("No se encontró Tesseract: indicar ocr.tesseract_cmd en el YAML o la variable TESSERACT_CMD")
    pytesseract.pytesseract.tesseract_cmd = comando
    return comando


def ejecutar(
    ruta: str | Path,
    cfg: dict[str, Any] | None = None,
    pagina: int = 0,
    enderezar: bool = True,
    receta: str | None = None,
) -> dict[str, Any]:
    """Procesa un PDF escaneado o una imagen de punta a punta.

    Args:
        ruta: archivo PDF, PNG o JPG.
        cfg: configuración (por defecto la del YAML).
        pagina: página del PDF, desde 0.
        enderezar: si es False no corrige la inclinación (útil con fotos con perspectiva).
        receta: reemplaza a ``ocr.receta`` del YAML (crudo, gris, otsu, adaptativa o mediana).

    Returns:
        Diccionario con ``archivo``, ``receta``, ``inclinacion`` e ``imagen`` (y, desde el paso 7,
        ``texto``, ``confianza``, ``puntaje`` y ``decision``).
    """
    cfg = cfg or cargar_configuracion()
    ocr = cfg["ocr"]
    configurar_tesseract(cfg)
    ruta = Path(ruta)
    receta = receta or ocr["receta"]
    if ruta.suffix.lower() == ".pdf":
        imagen = pdf_a_imagen(ruta, pagina, ocr["dpi"])
    else:
        imagen = np.array(Image.open(ruta).convert("RGB"))
    inclinacion = 0.0
    if enderezar:
        imagen, inclinacion = corregir_inclinacion(imagen)
    imagen = aplicar_receta(imagen, receta)
    resultado = {"archivo": ruta.name, "receta": receta, "inclinacion": inclinacion, "imagen": imagen}
    # P7: lectura y puntaje
    return resultado