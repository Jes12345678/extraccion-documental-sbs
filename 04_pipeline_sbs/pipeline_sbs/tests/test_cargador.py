"""Pruebas mínimas de la ingesta de S02 con archivos sintéticos."""
import pymupdf
import pytest
from PIL import Image

from pipeline_sbs.ingesta.cargador import (
    CargadorDocumentos,
    calcular_sha256,
    detectar_formato,
    enviar_a_cuarentena,
    validar_formato_y_extension,
    verificar_apertura,
)
from pipeline_sbs.ingesta.errores import ErrorArchivoVacio, ErrorExtensionFalsa, ErrorPdfCifrado
from pipeline_sbs.ingesta.extractores import extraer_pdf_pymupdf


def _pdf(ruta, texto="Balance DEMO", **opciones):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), texto)
    doc.save(str(ruta), **opciones)
    doc.close()
    return ruta


def _lote(tmp_path):
    lote = tmp_path / "lote"
    lote.mkdir()
    _pdf(lote / "a_balance.pdf")
    (lote / "b_copia.pdf").write_bytes((lote / "a_balance.pdf").read_bytes())
    (lote / "c_vacio.pdf").write_bytes(b"")
    Image.new("RGB", (20, 20), "white").save(lote / "d_voucher.png")
    return lote


def test_extractor_pymupdf_lee_el_texto(tmp_path):
    assert "Balance DEMO" in extraer_pdf_pymupdf(_pdf(tmp_path / "a.pdf")).texto


def test_huella_igual_para_copias_exactas(tmp_path):
    lote = _lote(tmp_path)
    assert calcular_sha256(lote / "a_balance.pdf") == calcular_sha256(lote / "b_copia.pdf")
    assert calcular_sha256(lote / "a_balance.pdf") != calcular_sha256(lote / "d_voucher.png")


def test_formato_por_firma_y_errores_tipificados(tmp_path):
    lote = _lote(tmp_path)
    assert detectar_formato(lote / "a_balance.pdf") == "pdf" and detectar_formato(lote / "c_vacio.pdf") == "vacio"
    with pytest.raises(ErrorArchivoVacio):
        validar_formato_y_extension(lote / "c_vacio.pdf", "vacio")
    falso = lote / "e_falso.pdf"
    falso.write_bytes((lote / "d_voucher.png").read_bytes())
    with pytest.raises(ErrorExtensionFalsa):
        validar_formato_y_extension(falso, detectar_formato(falso))


def test_pdf_cifrado(tmp_path):
    cifrado = _pdf(tmp_path / "c.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
    with pytest.raises(ErrorPdfCifrado):
        verificar_apertura(cifrado, "pdf")


def test_cuarentena_copia_y_es_idempotente(tmp_path):
    origen = _pdf(tmp_path / "a.pdf")
    for _ in range(2):
        destino = enviar_a_cuarentena(origen, tmp_path / "q", "prueba")
    assert origen.exists() and destino.read_bytes() == origen.read_bytes()


def test_lote_un_registro_por_archivo_y_segunda_corrida_igual(tmp_path):
    cargador = CargadorDocumentos(_lote(tmp_path), tmp_path / "q")
    primera = cargador.cargar_lote()
    assert [r.estado for r in primera] == ["ok", "duplicado", "fallo", "ok"]
    assert primera[3].requiere_ocr and primera[2].motivo == "archivo_vacio"
    segunda = cargador.cargar_lote()
    assert [(r.archivo, r.estado, r.motivo) for r in primera] == [(r.archivo, r.estado, r.motivo) for r in segunda]
