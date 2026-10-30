"""Primer pipeline de punta a punta (S02): leer, normalizar, registrar errores y calcular estadísticas.

Cada etapa es una función con una sola responsabilidad. ``ejecutar_pipeline`` las encadena y devuelve
una tabla con un registro por archivo. Un archivo defectuoso nunca detiene el lote y repetir la corrida
sobre la misma carpeta produce el mismo resultado.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import pandas as pd

from pipeline_sbs.config.configuracion import cargar_configuracion
from pipeline_sbs.ingesta.cargador import (
    TIPO_POR_EXTENSION,
    calcular_sha256,
    detectar_formato,
    enviar_a_cuarentena,
    validar_formato_y_extension,
    verificar_apertura,
)
from pipeline_sbs.ingesta.errores import ErrorIngesta, traducir_excepcion
from pipeline_sbs.ingesta.extractores import extraer_texto
from pipeline_sbs.preprocesamiento.normalizacion import normalizar_texto

registro = logging.getLogger(__name__)

FORMATOS_IMAGEN = ("png", "jpg", "tif")
COLUMNAS = ["archivo", "tipo", "formato", "estado", "motivo", "detalle", "paginas", "caracteres", "segundos",
            "requiere_ocr", "sha256", "duplicado_de"]


def _parametros(config: dict[str, Any] | None) -> dict[str, Any]:
    """Une la configuración recibida con la sección ``ingesta`` de ``pipeline.yaml``.

    Args:
        config: valores propios (``cuarentena``, ``umbral_ocr``) que reemplazan a los del YAML.

    Returns:
        Diccionario con ``cuarentena`` y ``umbral_ocr``.
    """
    ingesta = cargar_configuracion()["ingesta"]
    parametros = {
        "cuarentena": ingesta.get("carpeta_cuarentena", "cuarentena"),
        "umbral_ocr": ingesta.get("umbral_ocr_caracteres_por_pagina", 20),
    }
    parametros.update(config or {})
    return parametros


def leer_lote(carpeta: str | Path) -> list[dict[str, Any]]:
    """Etapa 1: abre cada archivo de la carpeta y extrae su texto sin detener el lote.

    Calcula la huella SHA-256, detecta el formato por la firma, valida contra la extensión, comprueba
    que el archivo abre y detecta duplicados por huella. El texto queda sin normalizar (``texto_crudo``).

    Args:
        carpeta: carpeta con los archivos recibidos (sin subcarpetas), leída en orden alfabético.

    Returns:
        Un diccionario por archivo con las columnas de ``COLUMNAS`` y ``texto_crudo``; ``ruta`` conserva
        el archivo original para la cuarentena.

    Raises:
        NotADirectoryError: si la carpeta no existe.
    """
    carpeta = Path(carpeta)
    if not carpeta.is_dir():
        raise NotADirectoryError(f"No existe la carpeta: {carpeta}")
    archivos = sorted(p for p in carpeta.iterdir() if p.is_file() and not p.name.startswith("."))
    vistos: dict[str, str] = {}        # huella -> nombre del primer archivo con ese contenido
    registros: list[dict[str, Any]] = []
    for ruta in archivos:
        inicio = time.perf_counter()
        fila: dict[str, Any] = {
            "archivo": ruta.name, "tipo": TIPO_POR_EXTENSION.get(ruta.suffix.lower(), "otro"), "formato": "desconocido",
            "estado": "ok", "motivo": "", "detalle": "", "paginas": 0, "caracteres": 0, "segundos": 0.0,
            "requiere_ocr": False, "sha256": "", "duplicado_de": "", "texto_crudo": "", "ruta": ruta,
        }
        try:
            fila["sha256"] = calcular_sha256(ruta)
            fila["formato"] = detectar_formato(ruta)
            validar_formato_y_extension(ruta, fila["formato"])
            verificar_apertura(ruta, fila["formato"])
            if fila["sha256"] in vistos:
                fila.update(estado="duplicado", motivo="duplicado", duplicado_de=vistos[fila["sha256"]],
                            detalle=f"mismo contenido que {vistos[fila['sha256']]}")
            else:
                extraccion = extraer_texto(ruta, fila["formato"])
                fila.update(texto_crudo=extraccion.texto, paginas=extraccion.paginas)
                vistos[fila["sha256"]] = ruta.name
        except Exception as error:  # noqa: BLE001 - un archivo malo no debe detener el lote
            tipificado = error if isinstance(error, ErrorIngesta) else traducir_excepcion(error)
            detalle = (tipificado.detalle or str(tipificado)).replace(str(ruta), ruta.name)
            fila.update(estado="fallo", motivo=tipificado.motivo, detalle=detalle)
        fila["segundos"] = time.perf_counter() - inicio
        registros.append(fila)
    return registros
