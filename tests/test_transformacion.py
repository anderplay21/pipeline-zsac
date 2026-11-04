from datetime import date, datetime
from decimal import Decimal

import pandas as pd
import pytest

from conftest import fila_valida
from zsac_etl.extraccion import leer_archivo
from zsac_etl.transformacion import (
    ValorInvalido,
    calcular_row_hash,
    factura_real,
    limpiar_id,
    limpiar_texto,
    parsear_decimal,
    parsear_entero,
    parsear_fecha,
    transformar,
)


# --- 4.1 texto e identificadores ------------------------------------------------------

@pytest.mark.parametrize("crudo, esperado", [
    ("8520777971\r", "8520777971"),
    ("   ", None),
    ("", None),
    (None, None),
    ("  ICOLTRANS  ", "ICOLTRANS"),
    (2214618.0, "2214618"),
])
def test_limpiar_texto(crudo, esperado):
    assert limpiar_texto(crudo) == esperado


@pytest.mark.parametrize("crudo, esperado", [
    ("00000000002104607371", "2104607371"),
    ("0003009178", "3009178"),
    ("CNS-000123", "CNS-000123"),
    ("0000", "0"),
    ("0085444124\r", "85444124"),
    ("PMP0012", "PMP0012"),
])
def test_limpiar_id(crudo, esperado):
    assert limpiar_id(crudo) == esperado


# --- 4.2 fechas -----------------------------------------------------------------------

def test_fecha_texto_con_hora_dia_primero():
    assert parsear_fecha("05.01.2026-10:00", solo_fecha=False) == datetime(2026, 1, 5, 10, 0)


def test_fecha_texto_con_segundos():
    assert parsear_fecha("20.01.2026-08:49:00", solo_fecha=False) == datetime(2026, 1, 20, 8, 49)


def test_fecha_nativa_y_date():
    assert parsear_fecha(datetime(2026, 1, 7, 0, 0), solo_fecha=True) == date(2026, 1, 7)
    assert parsear_fecha(date(2026, 1, 7), solo_fecha=True) == date(2026, 1, 7)


def test_fecha_serial_excel():
    assert parsear_fecha(46029, solo_fecha=True) == date(2026, 1, 7)


def test_fecha_vacia_es_none():
    assert parsear_fecha("  ", solo_fecha=True) is None
    assert parsear_fecha("00.00.0000-00:00", solo_fecha=False) is None
    assert parsear_fecha("00.00.0000", solo_fecha=True) is None
    assert parsear_fecha("693596-", solo_fecha=True) is None


@pytest.mark.parametrize("basura", ["693597-", "31.02.2026", "2026/01/05", "ABC", 693596])
def test_fecha_invalida(basura):
    with pytest.raises(ValorInvalido, match="fecha inválida"):
        parsear_fecha(basura, solo_fecha=True)


# --- 4.3 números ----------------------------------------------------------------------

@pytest.mark.parametrize("crudo, ambiguo, esperado, marca", [
    ("3.048,65", "miles", Decimal("3048.65"), False),
    ("243.91", "miles", Decimal("243.91"), False),
    ("5.673", "miles", Decimal("5673"), True),
    ("68.864", "decimal", Decimal("68.864"), True),
    ("0,000012", "decimal", Decimal("0.000012"), False),
    ("1.234.567", "miles", Decimal("1234567"), False),
    ("15", "miles", Decimal("15"), False),
    ("0.0003", "decimal", Decimal("0.0003"), False),
    (58619604, "miles", Decimal("58619604"), False),
])
def test_parsear_decimal(crudo, ambiguo, esperado, marca):
    assert parsear_decimal(crudo, ambiguo) == (esperado, marca)


@pytest.mark.parametrize("basura", ["1,2,3", "abc", "12-5"])
def test_decimal_invalido(basura):
    with pytest.raises(ValorInvalido):
        parsear_decimal(basura)


def test_cajas_entero_sin_ceros():
    assert parsear_entero("0000001500") == 1500
    assert parsear_entero(739.0) == 739
    with pytest.raises(ValorInvalido):
        parsear_entero("12,5")


# --- 4.4 factura real -----------------------------------------------------------------

@pytest.mark.parametrize("factura, real", [
    ("LEGALIZACION", False), ("1", False), ("F", False), ("VACIO CALI", False), (None, False),
    ("PMP12345", True), ("9961583720", True), ("CNS-1234", True), ("ICI/123", False),
])
def test_factura_real(factura, real):
    assert factura_real(factura, 4) is real


# --- 4.5 transformación completa ------------------------------------------------------

def _transformar_filas(crear_xlsx, fuente_enero, columnas, filas):
    extraido = leer_archivo(crear_xlsx(filas=filas), fuente_enero, columnas)
    return transformar(extraido.datos, columnas, factura_min_digitos=4)


def test_fila_valida_tipada(crear_xlsx, fuente_enero, columnas):
    r = _transformar_filas(crear_xlsx, fuente_enero, columnas, [fila_valida(Kilos="5.673")])
    f = r.validas.iloc[0]
    assert f["remision"] == "2104613011"
    assert f["bp_centro_origen"] == "3009174"
    assert f["orden_pedido"] == "85444124"
    assert f["fecha_entrega"] == datetime(2026, 1, 5, 10, 0)
    assert f["fecha_factura"] == date(2026, 1, 10)
    assert f["cajas"] == 8
    assert f["kilos"] == Decimal("5673.000") and f["kilos_txt"] == "5.673"
    assert f["volumen"] == Decimal("0.120000") and f["volumen_txt"] == "0,12"
    assert f["numero_ambiguo"] == 1
    assert f["valor_declarado"] == Decimal("58619604.00")
    assert f["fila_origen"] == 2 and f["periodo"] == date(2026, 1, 1)
    assert isinstance(f["row_hash"], bytes) and len(f["row_hash"]) == 32
    assert r.rechazos == [] and r.sin_factura == 0


def test_sin_factura_real_se_descarta_sin_rechazo(crear_xlsx, fuente_enero, columnas):
    filas = [fila_valida(Factura="LEGALIZACION"), fila_valida(Factura=None), fila_valida()]
    r = _transformar_filas(crear_xlsx, fuente_enero, columnas, filas)
    assert len(r.validas) == 1 and r.sin_factura == 2 and r.rechazos == []


def test_obligatoria_invalida_es_error_y_saca_la_fila(crear_xlsx, fuente_enero, columnas):
    filas = [fila_valida(**{"Fecha de Factura": "31.02.2026"}), fila_valida(Factura="9961583721")]
    r = _transformar_filas(crear_xlsx, fuente_enero, columnas, filas)
    assert list(r.validas["factura"]) == ["9961583721"]
    assert r.rechazos == [{"archivo": "ZSAC_ENERO_2026.xlsx", "fila_origen": 2, "tipo": "ERROR",
                           "columna": "fecha_factura", "valor": "31.02.2026", "motivo": "fecha inválida"}]
    assert r.contar("ERROR") == 1


def test_obligatoria_vacia_es_error(crear_xlsx, fuente_enero, columnas):
    r = _transformar_filas(crear_xlsx, fuente_enero, columnas, [fila_valida(**{"Cl Doc FWO": " "})])
    assert r.validas.empty
    assert r.rechazos[0]["tipo"] == "ERROR" and r.rechazos[0]["motivo"] == "valor obligatorio vacío"


def test_opcional_invalida_es_advertencia_y_null(crear_xlsx, fuente_enero, columnas):
    r = _transformar_filas(crear_xlsx, fuente_enero, columnas, [fila_valida(**{"Fec. Compromiso": "14/25/2026"})])
    assert len(r.validas) == 1 and r.validas.iloc[0]["fecha_compromiso"] is None
    assert r.rechazos[0]["tipo"] == "ADVERTENCIA" and r.rechazos[0]["columna"] == "fecha_compromiso"


def test_duplicado_en_archivo_primera_gana(crear_xlsx, fuente_enero, columnas):
    filas = [fila_valida(Remision="1"), fila_valida(Factura="9961583721"), fila_valida(Remision="3")]
    r = _transformar_filas(crear_xlsx, fuente_enero, columnas, filas)
    assert list(r.validas["fila_origen"]) == [2, 3]
    assert r.validas.iloc[0]["remision"] == "1"
    dup = [x for x in r.rechazos if x["tipo"] == "DUPLICADO"]
    assert len(dup) == 1 and dup[0]["fila_origen"] == 4 and "prevalece la fila 2" in dup[0]["motivo"]


def test_fecha_vacia_sap_no_genera_advertencia(crear_xlsx, fuente_enero, columnas):
    filas = [fila_valida(**{"Fec. Compromiso": "693596-", "Fecha de Entrega Pla": "00.00.0000-00:00"})]
    r = _transformar_filas(crear_xlsx, fuente_enero, columnas, filas)
    assert r.rechazos == [] and r.fechas_vacias_sap == 2
    assert r.validas.iloc[0]["fecha_compromiso"] is None


def test_duplicado_ignora_mayusculas_como_sql_server(crear_xlsx, fuente_enero, columnas):
    filas = [fila_valida(Factura="ICI/ct-005161"), fila_valida(Factura="ICI/CT-005161")]
    r = _transformar_filas(crear_xlsx, fuente_enero, columnas, filas)
    assert list(r.validas["factura"]) == ["ICI/ct-005161"]
    assert [x["tipo"] for x in r.rechazos] == ["DUPLICADO"]


def test_row_hash_estable_e_independiente_de_la_traza(crear_xlsx, fuente_enero, columnas):
    a = _transformar_filas(crear_xlsx, fuente_enero, columnas, [fila_valida()])
    b = _transformar_filas(crear_xlsx, fuente_enero, columnas, [{}, fila_valida()])  # otra fila de origen
    assert a.validas.iloc[0]["row_hash"] == b.validas.iloc[0]["row_hash"]
    c = _transformar_filas(crear_xlsx, fuente_enero, columnas, [fila_valida(Placa="ABC123")])
    assert a.validas.iloc[0]["row_hash"] != c.validas.iloc[0]["row_hash"]


def test_hash_canonico():
    assert calcular_row_hash([None, Decimal("1.50"), date(2026, 1, 1)]) == \
        calcular_row_hash([None, Decimal("1.50"), date(2026, 1, 1)])
    assert calcular_row_hash(["a", None]) != calcular_row_hash([None, "a"])
