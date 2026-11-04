import logging
from datetime import date, datetime

import pytest

from conftest import fila_valida
from zsac_etl import extraccion
from zsac_etl.extraccion import (
    ArchivoBloqueado,
    ArchivoRechazado,
    copiar_con_reintentos,
    descubrir_archivos,
    leer_archivo,
)


# --- 3.1 descubrimiento ---------------------------------------------------------------

def test_descubre_solo_nombres_validos_y_calcula_periodo(tmp_path, caplog):
    for n in ["ZSAC_ENERO_2026.xlsx", "ZSAC_DICIEMBRE_2025.xlsx", "~$ZSAC_ENERO_2026.xlsx",
              "notas.xlsx", "ZSAC_ENERITO_2026.xlsx"]:
        (tmp_path / n).write_bytes(b"x")

    with caplog.at_level(logging.INFO, logger="zsac_etl"):
        fuentes = descubrir_archivos(tmp_path)

    assert [(f.nombre, f.periodo) for f in fuentes] == [
        ("ZSAC_DICIEMBRE_2025.xlsx", date(2025, 12, 1)),
        ("ZSAC_ENERO_2026.xlsx", date(2026, 1, 1)),
    ]
    mensajes = {(r.levelname, r.getMessage()) for r in caplog.records}
    assert any(lvl == "INFO" and "~$ZSAC_ENERO_2026.xlsx" in m for lvl, m in mensajes)
    assert any(lvl == "INFO" and "notas.xlsx" in m for lvl, m in mensajes)
    assert any(lvl == "WARNING" and "ENERITO" in m for lvl, m in mensajes)


# --- 3.2 copia con reintentos ---------------------------------------------------------

def test_copia_reintenta_y_luego_funciona(tmp_path, monkeypatch):
    origen = tmp_path / "ZSAC_ENERO_2026.xlsx"
    origen.write_bytes(b"contenido")
    llamadas = {"n": 0}
    copia_real = extraccion.shutil.copy2

    def copia_bloqueada_una_vez(src, dst):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            raise PermissionError("bloqueado")
        return copia_real(src, dst)

    monkeypatch.setattr(extraccion.shutil, "copy2", copia_bloqueada_una_vez)
    esperas = []
    destino = copiar_con_reintentos(origen, tmp_path / "tmp", reintentos=3, espera_segundos=7, dormir=esperas.append)

    assert destino.read_bytes() == b"contenido"
    assert esperas == [7]


def test_copia_bloqueada_siempre_lanza_archivo_bloqueado(tmp_path, monkeypatch):
    origen = tmp_path / "ZSAC_ENERO_2026.xlsx"
    origen.write_bytes(b"x")

    def siempre_bloqueado(src, dst):
        raise PermissionError("bloqueado")

    monkeypatch.setattr(extraccion.shutil, "copy2", siempre_bloqueado)
    esperas = []
    with pytest.raises(ArchivoBloqueado):
        copiar_con_reintentos(origen, tmp_path / "tmp", reintentos=3, espera_segundos=1, dormir=esperas.append)
    assert len(esperas) == 2


def test_copia_no_modifica_el_original(tmp_path):
    origen = tmp_path / "ZSAC_ENERO_2026.xlsx"
    origen.write_bytes(b"x")
    mtime = origen.stat().st_mtime
    copiar_con_reintentos(origen, tmp_path / "tmp")
    assert origen.read_bytes() == b"x" and origen.stat().st_mtime == mtime


# --- 3.3 lectura ----------------------------------------------------------------------

def test_lee_todas_las_filas_incluidas_ocultas_con_trazabilidad(crear_xlsx, fuente_enero, columnas):
    ruta = crear_xlsx(filas=[fila_valida(Factura=str(1000 + i)) for i in range(5)], ocultar=(3, 4))
    r = leer_archivo(ruta, fuente_enero, columnas)

    assert len(r.datos) == 5
    assert r.hoja == "BD"
    assert list(r.datos["fila_origen"]) == [2, 3, 4, 5, 6]
    assert set(r.datos["archivo_origen"]) == {"ZSAC_ENERO_2026.xlsx"}
    assert set(r.datos["periodo"]) == {date(2026, 1, 1)}
    assert r.datos.loc[0, "remision"] == "00000000002104613011"
    assert r.datos.loc[0, "fecha_factura"] == date(2026, 1, 10)
    assert r.advertencias == []


def test_hoja_db_como_alternativa(crear_xlsx, fuente_enero, columnas):
    r = leer_archivo(crear_xlsx(hoja="DB"), fuente_enero, columnas)
    assert r.hoja == "DB" and len(r.datos) == 1


def test_sin_hoja_bd_ni_db_rechaza(crear_xlsx, fuente_enero, columnas):
    with pytest.raises(ArchivoRechazado, match="hoja BD/DB no encontrada"):
        leer_archivo(crear_xlsx(hoja="Hoja1"), fuente_enero, columnas)


def test_columnas_en_otro_orden(crear_xlsx, fuente_enero, columnas, encabezado_estandar):
    ruta = crear_xlsx(encabezado=list(reversed(encabezado_estandar)))
    r = leer_archivo(ruta, fuente_enero, columnas)
    assert r.datos.loc[0, "factura"] == "9961583720"
    assert r.datos.loc[0, "cliente"] == "NESTLE PURINA DE COLOMBIA S.A"
    assert r.advertencias == []


def test_falta_columna_opcional_queda_null(crear_xlsx, fuente_enero, columnas, encabezado_estandar):
    ruta = crear_xlsx(encabezado=[n for n in encabezado_estandar if n != "Kilos"])
    r = leer_archivo(ruta, fuente_enero, columnas)
    assert r.datos.loc[0, "kilos"] is None
    assert any("Kilos" in a for a in r.advertencias)


def test_falta_columna_obligatoria_rechaza(crear_xlsx, fuente_enero, columnas, encabezado_estandar):
    ruta = crear_xlsx(encabezado=[n for n in encabezado_estandar if n != "Cl Doc FWO"])
    with pytest.raises(ArchivoRechazado, match="Cl Doc FWO"):
        leer_archivo(ruta, fuente_enero, columnas)


def test_columna_extra_se_omite(crear_xlsx, fuente_enero, columnas, encabezado_estandar):
    ruta = crear_xlsx(encabezado=encabezado_estandar + ["Observaciones"],
                      filas=[fila_valida(Observaciones="algo")])
    r = leer_archivo(ruta, fuente_enero, columnas)
    assert "Observaciones" not in r.datos.columns
    assert any("Observaciones" in a for a in r.advertencias)


def test_fecha_factura_duplicada_usa_la_que_tiene_mas_datos(crear_xlsx, fuente_enero, columnas, encabezado_estandar):
    encabezado = encabezado_estandar + ["Fecha de Factura"]   # tercera copia, como en SEPTIEMBRE_2026
    filas = [fila_valida(), fila_valida(Factura="9961583721")]
    for f in filas:
        f["Fecha de Factura"] = datetime(2026, 1, 10)
    # openpyxl escribe por nombre: ambas "Fecha de Factura" reciben el mismo valor;
    # se vacía una celda de la primera copia para que la segunda tenga más datos.
    ruta = crear_xlsx(encabezado=encabezado, filas=filas)
    from openpyxl import load_workbook
    libro = load_workbook(ruta)
    ws = libro["BD"]
    col_primera = encabezado.index("Fecha de Factura") + 1
    ws.cell(row=3, column=col_primera).value = None
    libro.save(ruta)

    r = leer_archivo(ruta, fuente_enero, columnas)
    assert list(r.datos["fecha_factura"]) == [date(2026, 1, 10)] * 2
    assert any("Fecha de Factura" in a for a in r.advertencias)


def test_filas_completamente_vacias_se_ignoran(crear_xlsx, fuente_enero, columnas):
    ruta = crear_xlsx(filas=[fila_valida(), {}, fila_valida(Factura="9961583721")])
    r = leer_archivo(ruta, fuente_enero, columnas)
    assert list(r.datos["fila_origen"]) == [2, 4]
