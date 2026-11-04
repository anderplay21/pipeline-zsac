"""Logging, bloqueo de corrida concurrente y bitácora en core.ETL_Control."""

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from zsac_etl.config import RAIZ_PROYECTO

RECURSO_BLOQUEO = "zsac_etl"


def configurar_logging(carpeta="logs", nivel="INFO", rotacion_dias=30):
    """Archivo rotativo diario + consola (patrón de pipeline-analitica-ontime)."""
    carpeta = Path(carpeta)
    if not carpeta.is_absolute():
        carpeta = RAIZ_PROYECTO / carpeta
    carpeta.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("zsac_etl")
    logger.setLevel(getattr(logging, str(nivel).upper(), logging.INFO))
    logger.propagate = False
    if logger.handlers:
        return logger

    formato = logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    archivo = TimedRotatingFileHandler(carpeta / "zsac_etl.log", when="midnight",
                                       backupCount=rotacion_dias, encoding="utf-8")
    archivo.setFormatter(formato)
    consola = logging.StreamHandler()
    consola.setFormatter(formato)
    logger.addHandler(archivo)
    logger.addHandler(consola)
    return logger


def tomar_bloqueo(conexion):
    """Bloqueo de aplicación a nivel de sesión: si el proceso muere, SQL Server lo libera solo."""
    cursor = conexion.cursor()
    cursor.execute(
        "SET NOCOUNT ON; DECLARE @r INT; "
        "EXEC @r = sp_getapplock @Resource = ?, @LockMode = 'Exclusive', @LockOwner = 'Session', @LockTimeout = 0; "
        "SELECT @r;",
        RECURSO_BLOQUEO,
    )
    obtenido = cursor.fetchone()[0] >= 0
    conexion.commit()
    return obtenido


def liberar_bloqueo(conexion):
    try:
        conexion.cursor().execute(
            "EXEC sp_releaseapplock @Resource = ?, @LockOwner = 'Session';", RECURSO_BLOQUEO
        )
        conexion.commit()
    except Exception:
        pass


def registrar_corrida(conexion, tabla_control, nombre_proceso, fecha_desde, fecha_hasta, *,
                      filas_staging=None, filas_insertadas=None, filas_actualizadas=None,
                      estado="OK", detalle=None):
    conexion.cursor().execute(
        f"""
        INSERT INTO {tabla_control}
            (NombreProceso, FechaDesde, FechaHasta, FilasStaging, FilasInsertadas,
             FilasActualizadas, Estado, Detalle)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        nombre_proceso, fecha_desde, fecha_hasta, filas_staging, filas_insertadas,
        filas_actualizadas, estado, detalle,
    )
    conexion.commit()
