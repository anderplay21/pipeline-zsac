"""Orquestación con dependencias simuladas (sin SQL Server ni OneDrive)."""

from datetime import date
from pathlib import Path

import pytest

from zsac_etl import pipeline
from zsac_etl.cli import construir_parser
from zsac_etl.extraccion import ArchivoBloqueado, ArchivoFuente


@pytest.mark.parametrize("cargados, fallidos, global_, esperado", [
    (2, 0, False, "OK"),
    (0, 0, False, "OK"),        # nada que procesar también es OK
    (2, 1, False, "PARCIAL"),
    (0, 1, False, "ERROR"),
    (0, 0, True, "ERROR"),
    (1, 0, True, "PARCIAL"),
])
def test_estado_corrida(cargados, fallidos, global_, esperado):
    assert pipeline.estado_corrida(cargados, fallidos, global_) == esperado


def test_cli_opciones():
    p = construir_parser()
    assert p.parse_args(["run"]).todos is False
    assert p.parse_args(["run", "--todos"]).todos is True
    assert p.parse_args(["run", "--archivo", "ZSAC_MAYO_2026.xlsx"]).archivo == "ZSAC_MAYO_2026.xlsx"
    with pytest.raises(SystemExit):
        p.parse_args(["run", "--todos", "--archivo", "X"])


class ConexionFalsa:
    def __init__(self):
        self.cerrada = False

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.cerrada = True


@pytest.fixture
def entorno(monkeypatch, tmp_path):
    """Simula todo el entorno; devuelve un dict para controlar y observar la corrida."""
    estado = {
        "fuentes": [ArchivoFuente("ZSAC_ENERO_2026.xlsx", tmp_path / "a", date(2026, 1, 1)),
                    ArchivoFuente("ZSAC_FEBRERO_2026.xlsx", tmp_path / "b", date(2026, 2, 1))],
        "bloqueados": set(), "hashes": {}, "procesados": [], "corridas": [], "fallas": [],
        "bloqueo": True, "conexion": ConexionFalsa(),
    }
    cfg = {"origen": {"carpeta": str(tmp_path), "hojas": ["BD", "DB"]}, "reglas": {"factura_min_digitos": 4},
           "logging": {"carpeta": str(tmp_path / "logs")},
           "tablas": {"control": "core.ETL_Control", "nombre_proceso": "zsac"}}

    def copiar(ruta, *a, **k):
        if Path(ruta).name in [f.ruta.name for f in estado["fuentes"] if f.nombre in estado["bloqueados"]]:
            raise ArchivoBloqueado("bloqueado")
        destino = tmp_path / f"copia_{Path(ruta).name}"
        destino.write_bytes(Path(ruta).name.encode())
        return destino

    def procesar(conexion, cfg_, columnas, fuente, copia, sha, resumen):
        estado["procesados"].append(fuente.nombre)
        resumen.cargados.append((fuente.nombre, fuente.periodo,
                                 {"insertadas": 1, "actualizadas": 0, "staging": 1, "leidas": 1, "sin_factura": 0,
                                  "error": 0, "advertencia": 0, "duplicadas": 0}))

    monkeypatch.setattr(pipeline, "obtener_conexion", lambda cfg: estado["conexion"])
    monkeypatch.setattr(pipeline.control, "tomar_bloqueo", lambda cx: estado["bloqueo"])
    monkeypatch.setattr(pipeline.control, "liberar_bloqueo", lambda cx: None)
    monkeypatch.setattr(pipeline.control, "registrar_corrida",
                        lambda cx, *a, **k: estado["corridas"].append(k))
    monkeypatch.setattr(pipeline, "cargar_columnas", lambda: [])
    monkeypatch.setattr(pipeline, "descubrir_archivos", lambda carpeta: estado["fuentes"])
    monkeypatch.setattr(pipeline, "copiar_con_reintentos", copiar)
    monkeypatch.setattr(pipeline, "calcular_sha256", lambda ruta: ruta.read_bytes())
    monkeypatch.setattr(pipeline.carga, "hashes_cargados", lambda cx, t: estado["hashes"])
    monkeypatch.setattr(pipeline.carga, "registrar_archivo",
                        lambda cx, t, nombre, periodo, est, *a, **k: estado["fallas"].append((nombre, est)))
    monkeypatch.setattr(pipeline, "_procesar_archivo", procesar)
    estado["cfg"] = cfg
    return estado


def test_corrida_ok_procesa_todo_y_sale_0(entorno):
    assert pipeline.ejecutar(cfg=entorno["cfg"]) == 0
    assert entorno["procesados"] == ["ZSAC_ENERO_2026.xlsx", "ZSAC_FEBRERO_2026.xlsx"]
    assert entorno["corridas"][0]["estado"] == "OK" and entorno["corridas"][0]["filas_insertadas"] == 2
    assert entorno["conexion"].cerrada


def test_incremental_salta_archivos_sin_cambios(entorno):
    entorno["hashes"] = {"ZSAC_ENERO_2026.xlsx": b"a", "ZSAC_FEBRERO_2026.xlsx": b"b"}
    assert pipeline.ejecutar(cfg=entorno["cfg"]) == 0
    assert entorno["procesados"] == []
    assert "sin cambios=2" in entorno["corridas"][0]["detalle"]


def test_todos_ignora_hashes(entorno):
    entorno["hashes"] = {"ZSAC_ENERO_2026.xlsx": b"a", "ZSAC_FEBRERO_2026.xlsx": b"b"}
    pipeline.ejecutar(todos=True, cfg=entorno["cfg"])
    assert len(entorno["procesados"]) == 2


def test_un_archivo(entorno):
    entorno["hashes"] = {"ZSAC_FEBRERO_2026.xlsx": b"b"}
    pipeline.ejecutar(archivo="zsac_febrero_2026.xlsx", cfg=entorno["cfg"])
    assert entorno["procesados"] == ["ZSAC_FEBRERO_2026.xlsx"]


def test_archivo_inexistente_es_error(entorno):
    assert pipeline.ejecutar(archivo="ZSAC_MAYO_2099.xlsx", cfg=entorno["cfg"]) == 2
    assert entorno["corridas"][0]["estado"] == "ERROR"


def test_archivo_bloqueado_da_parcial(entorno):
    entorno["bloqueados"] = {"ZSAC_FEBRERO_2026.xlsx"}
    assert pipeline.ejecutar(cfg=entorno["cfg"]) == 1
    assert entorno["procesados"] == ["ZSAC_ENERO_2026.xlsx"]
    assert ("ZSAC_FEBRERO_2026.xlsx", "OMITIDO") in entorno["fallas"]
    assert entorno["corridas"][0]["estado"] == "PARCIAL"


def test_corrida_concurrente_no_procesa(entorno):
    entorno["bloqueo"] = False
    assert pipeline.ejecutar(cfg=entorno["cfg"]) == pipeline.SALIDA_BLOQUEADA
    assert entorno["procesados"] == [] and entorno["corridas"] == []


def test_sin_conexion_sale_con_error(entorno, monkeypatch):
    def falla(cfg):
        raise RuntimeError("servidor caído")
    monkeypatch.setattr(pipeline, "obtener_conexion", falla)
    assert pipeline.ejecutar(cfg=entorno["cfg"]) == 2
