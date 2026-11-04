"""Conexión a SQL Server con pyodbc (adaptado de pipeline-analitica-ontime/src/utils/db.py)."""

import os

import pyodbc


def construir_connection_string(config):
    sql_cfg = config["sql_server"]

    partes = [
        f"DRIVER={{{sql_cfg['driver']}}}",
        f"SERVER={sql_cfg['servidor']}",
        f"DATABASE={sql_cfg['base_datos']}",
    ]

    if sql_cfg.get("trusted_connection", True):
        partes.append("Trusted_Connection=yes")
    else:
        partes.append(f"UID={os.getenv('SQL_USUARIO')}")
        partes.append(f"PWD={os.getenv('SQL_PASSWORD')}")

    if sql_cfg.get("trust_server_certificate", True):
        partes.append("TrustServerCertificate=yes")

    return ";".join(partes) + ";"


def obtener_conexion(config, autocommit=False):
    """Conexión pyodbc. Por defecto sin autocommit: cada archivo se carga en su propia transacción."""
    return pyodbc.connect(construir_connection_string(config), autocommit=autocommit)
