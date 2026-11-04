"""Pruebas de integración de la carga (requieren SQL Server local). Usan el esquema zsac_test."""

import uuid
from datetime import date

import pytest

from conftest import fila_valida
from zsac_etl import carga
from zsac_etl.config import cargar_config
from zsac_etl.extraccion import ArchivoFuente, leer_archivo
from zsac_etl.transformacion import transformar

pytestmark = pytest.mark.sql

ESQUEMA = "zsac_test"
TABLAS = {
    "staging": f"{ESQUEMA}.ZSAC_Stage",
    "core": f"{ESQUEMA}.HechosZSAC",
    "rechazos": f"{ESQUEMA}.ZSAC_Rechazos",
    "archivos": f"{ESQUEMA}.ZSAC_Archivos",
}


@pytest.fixture(scope="module")
def conexion():
    pyodbc = pytest.importorskip("pyodbc")
    from zsac_etl.db import obtener_conexion
    try:
        cx = obtener_conexion(cargar_config())
    except pyodbc.Error as e:
        pytest.skip(f"SQL Server no disponible: {e}")
    cur = cx.cursor()
    cur.execute(f"IF SCHEMA_ID('{ESQUEMA}') IS NULL EXEC('CREATE SCHEMA {ESQUEMA}')")
    cx.commit()
    yield cx
    for t in TABLAS.values():
        cur.execute(f"DROP TABLE IF EXISTS {t}")
    cur.execute(f"DROP SCHEMA IF EXISTS {ESQUEMA}")
    cx.commit()
    cx.close()


@pytest.fixture
def bd(conexion):
    """Tablas limpias para cada prueba."""
    cur = conexion.cursor()
    for t in TABLAS.values():
        cur.execute(f"DROP TABLE IF EXISTS {t}")
    conexion.commit()
    carga.crear_estructuras(conexion, TABLAS)
    return conexion


@pytest.fixture
def cargar(bd, crear_xlsx, columnas):
    def _cargar(nombre, periodo, filas):
        fuente = ArchivoFuente(nombre, None, periodo)
        ruta = crear_xlsx(nombre=nombre, filas=filas)
        extraido = leer_archivo(ruta, fuente, columnas)
        transformado = transformar(extraido.datos, columnas, 4)
        return carga.cargar_archivo(
            bd, TABLAS, str(uuid.uuid4()), fuente, transformado, filas_leidas=len(extraido.datos),
            hoja=extraido.hoja, hash_sha256=b"\x01" * 32, tamano_bytes=1, fecha_modificacion=None,
        )
    return _cargar


def _uno(cx, sql, *params):
    return cx.cursor().execute(sql, *params).fetchone()


def _conteos(c):
    return c["insertadas"], c["actualizadas"], c["duplicadas"]


ENE, FEB, DIC = date(2026, 1, 1), date(2026, 2, 1), date(2025, 12, 1)
DOS = [fila_valida(), fila_valida(Factura="9961583721")]


def test_ddl_idempotente(bd):
    carga.crear_estructuras(bd, TABLAS)
    assert _uno(bd, f"SELECT COUNT(*) FROM {TABLAS['core']}")[0] == 0


def test_insercion_y_reproceso_identico(cargar, bd):
    assert _conteos(cargar("ZSAC_ENERO_2026.xlsx", ENE, DOS)) == (2, 0, 0)
    assert _conteos(cargar("ZSAC_ENERO_2026.xlsx", ENE, DOS)) == (0, 0, 0)
    fila = _uno(bd, f"SELECT remision, kilos, fecha_factura, archivo_origen, fila_origen FROM {TABLAS['core']} "
                    "WHERE factura = '9961583720'")
    assert tuple(fila)[0] == "2104613011" and str(fila[1]) == "50.160" and fila[2] == date(2026, 1, 10)
    assert tuple(fila)[3:] == ("ZSAC_ENERO_2026.xlsx", 2)
    estado = _uno(bd, f"SELECT estado, filas_insertadas, hash_sha256 FROM {TABLAS['archivos']}")
    assert estado[0] == "OK" and estado[1] == 0 and bytes(estado[2]) == b"\x01" * 32


def test_cambio_de_valor_actualiza(cargar, bd):
    cargar("ZSAC_ENERO_2026.xlsx", ENE, DOS)
    cambiadas = [fila_valida(Placa="ABC123"), fila_valida(Factura="9961583721")]
    assert _conteos(cargar("ZSAC_ENERO_2026.xlsx", ENE, cambiadas)) == (0, 1, 0)
    fila = _uno(bd, f"SELECT placa, fecha_ultima_actualizacion FROM {TABLAS['core']} WHERE factura = '9961583720'")
    assert fila[0] == "ABC123" and fila[1] is not None


def test_periodo_posterior_es_duplicado(cargar, bd):
    cargar("ZSAC_ENERO_2026.xlsx", ENE, DOS)
    assert _conteos(cargar("ZSAC_FEBRERO_2026.xlsx", FEB, [fila_valida(Placa="FEB")])) == (0, 0, 1)
    assert _uno(bd, f"SELECT placa, archivo_origen FROM {TABLAS['core']} WHERE factura = '9961583720'")[1] \
        == "ZSAC_ENERO_2026.xlsx"
    dup = _uno(bd, f"SELECT archivo, fila_origen, motivo FROM {TABLAS['rechazos']} WHERE tipo = 'DUPLICADO'")
    assert dup[0] == "ZSAC_FEBRERO_2026.xlsx" and dup[1] == 2 and "ZSAC_ENERO_2026.xlsx" in dup[2]


def test_periodo_anterior_reemplaza(cargar, bd):
    cargar("ZSAC_ENERO_2026.xlsx", ENE, DOS)
    assert _conteos(cargar("ZSAC_DICIEMBRE_2025.xlsx", DIC, [fila_valida(Placa="DIC")])) == (0, 1, 0)
    fila = _uno(bd, f"SELECT placa, archivo_origen, periodo FROM {TABLAS['core']} WHERE factura = '9961583720'")
    assert tuple(fila) == ("DIC", "ZSAC_DICIEMBRE_2025.xlsx", DIC)
    dup = _uno(bd, f"SELECT archivo, motivo FROM {TABLAS['rechazos']} WHERE tipo = 'DUPLICADO'")
    assert dup[0] == "ZSAC_ENERO_2026.xlsx" and "ZSAC_DICIEMBRE_2025.xlsx" in dup[1]
    # Si luego se reprocesa enero, sigue siendo duplicado y diciembre se mantiene
    assert _conteos(cargar("ZSAC_ENERO_2026.xlsx", ENE, DOS)) == (0, 0, 1)
    assert _uno(bd, f"SELECT COUNT(*) FROM {TABLAS['rechazos']} WHERE archivo = 'ZSAC_ENERO_2026.xlsx'")[0] == 1


def test_llave_que_desaparece_no_se_borra(cargar, bd):
    cargar("ZSAC_ENERO_2026.xlsx", ENE, DOS)
    cargar("ZSAC_ENERO_2026.xlsx", ENE, DOS[:1])
    assert _uno(bd, f"SELECT COUNT(*) FROM {TABLAS['core']}")[0] == 2


def test_rechazos_se_reemplazan_por_archivo(cargar, bd):
    cargar("ZSAC_ENERO_2026.xlsx", ENE, [fila_valida(**{"Fec. Compromiso": "basura"})])
    cargar("ZSAC_ENERO_2026.xlsx", ENE, [fila_valida()])
    assert _uno(bd, f"SELECT COUNT(*) FROM {TABLAS['rechazos']}")[0] == 0


def test_falla_en_merge_hace_rollback(cargar, bd, monkeypatch):
    cargar("ZSAC_ENERO_2026.xlsx", ENE, DOS[:1])

    def merge_que_falla(*a, **k):
        raise RuntimeError("conexión perdida")

    monkeypatch.setattr(carga, "ejecutar_merge", merge_que_falla)
    with pytest.raises(RuntimeError):
        cargar("ZSAC_FEBRERO_2026.xlsx", FEB, [fila_valida(Factura="5550001111")])
    assert _uno(bd, f"SELECT COUNT(*) FROM {TABLAS['core']}")[0] == 1
    assert _uno(bd, f"SELECT COUNT(*) FROM {TABLAS['archivos']} WHERE archivo = 'ZSAC_FEBRERO_2026.xlsx'")[0] == 0
    assert _uno(bd, f"SELECT COUNT(*) FROM {TABLAS['staging']}")[0] == 1   # staging previo intacto


def test_registrar_error_no_cambia_hash(bd):
    carga.registrar_archivo(bd, TABLAS, "ZSAC_ENERO_2026.xlsx", ENE, "OK", str(uuid.uuid4()), hash_sha256=b"\x02" * 32)
    carga.registrar_archivo(bd, TABLAS, "ZSAC_ENERO_2026.xlsx", ENE, "ERROR", str(uuid.uuid4()), detalle="falló")
    bd.commit()
    fila = _uno(bd, f"SELECT estado, hash_sha256 FROM {TABLAS['archivos']}")
    assert fila[0] == "ERROR" and bytes(fila[1]) == b"\x02" * 32
    assert carga.hashes_cargados(bd, TABLAS) == {}
