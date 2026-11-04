"""Carga a SQL Server: staging -> MERGE a core, rechazos y estado por archivo.

Cada archivo se carga en una sola transacción (design.md D7): si algo falla se hace
ROLLBACK y core queda como antes de ese archivo.
"""

import re
from pathlib import Path

import numpy as np
import pyodbc

RAIZ_PROYECTO = Path(__file__).resolve().parents[2]
CARPETA_SQL = RAIZ_PROYECTO / "sql"

# Nombres tal como están escritos en los .sql; se sustituyen por los de config["tablas"]
# (permite correr las pruebas de integración en otro esquema).
NOMBRES_SQL = {
    "staging": "staging.ZSAC_Stage",
    "core": "core.HechosZSAC",
    "rechazos": "core.ZSAC_Rechazos",
    "archivos": "core.ZSAC_Archivos",
}

COLUMNAS_RECHAZO = ("corrida_id", "archivo", "fila_origen", "tipo", "columna", "valor", "motivo")


def _sustituir_tablas(script, tablas):
    for clave, original in NOMBRES_SQL.items():
        script = script.replace(original, tablas[clave])
    return script


def _lotes(script):
    """Separa un script por líneas GO (pyodbc no entiende GO)."""
    return [lote.strip() for lote in re.split(r"^\s*GO\s*$", script, flags=re.MULTILINE) if lote.strip()]


def crear_estructuras(conexion, tablas):
    """Ejecuta el DDL idempotente (sql/01_crear_tablas_zsac.sql)."""
    script = _sustituir_tablas((CARPETA_SQL / "01_crear_tablas_zsac.sql").read_text(encoding="utf-8"), tablas)
    cursor = conexion.cursor()
    for lote in _lotes(script):
        cursor.execute(lote)
    conexion.commit()


def _nativo(v):
    """Valores que pyodbc entiende: numpy -> python, NaN/NaT -> None."""
    if v is None:
        return None
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and v != v:
        return None
    if v is getattr(np, "NaT", object()):
        return None
    return v


def cargar_staging(conexion, df, tablas):
    """TRUNCATE + inserción masiva (patrón de pipeline-analitica-ontime/src/carga/cargar_sql.py).

    Las columnas se insertan en el orden del DataFrame, que ya viene en el orden del DDL.
    """
    cursor = conexion.cursor()
    cursor.execute(f"TRUNCATE TABLE {tablas['staging']}")
    if df.empty:
        return 0
    cursor.fast_executemany = True
    columnas = ",".join(f"[{c}]" for c in df.columns)
    marcadores = ",".join("?" * len(df.columns))
    filas = [tuple(_nativo(v) for v in fila) for fila in df.itertuples(index=False, name=None)]
    cursor.executemany(f"INSERT INTO {tablas['staging']} ({columnas}) VALUES ({marcadores})", filas)
    return len(filas)


def ejecutar_merge(conexion, tablas, corrida_id):
    """Ejecuta sql/merges/merge_zsac.sql. Devuelve (insertadas, actualizadas, duplicadas_core)."""
    script = _sustituir_tablas((CARPETA_SQL / "merges" / "merge_zsac.sql").read_text(encoding="utf-8"), tablas)
    cursor = conexion.cursor()
    cursor.execute(script, corrida_id)
    # Recorre los result sets hasta encontrar el SELECT final con los conteos.
    while True:
        try:
            fila = cursor.fetchone()
        except pyodbc.ProgrammingError:
            fila = None
        if fila is not None:
            return int(fila[0]), int(fila[1]), int(fila[2])
        if not cursor.nextset():
            raise RuntimeError("El MERGE no devolvió conteos")


def borrar_rechazos(conexion, tablas, archivo):
    conexion.cursor().execute(f"DELETE FROM {tablas['rechazos']} WHERE archivo = ?", archivo)


def insertar_rechazos(conexion, tablas, corrida_id, rechazos):
    if not rechazos:
        return
    cursor = conexion.cursor()
    cursor.fast_executemany = True
    columnas = ",".join(COLUMNAS_RECHAZO)
    cursor.executemany(
        f"INSERT INTO {tablas['rechazos']} ({columnas}) VALUES (?,?,?,?,?,?,?)",
        [(corrida_id, r["archivo"], r["fila_origen"], r["tipo"], r["columna"], r["valor"], r["motivo"])
         for r in rechazos],
    )


def registrar_archivo(conexion, tablas, archivo, periodo, estado, corrida_id, *, hash_sha256=None,
                      tamano_bytes=None, fecha_modificacion=None, hoja=None, conteos=None, detalle=None):
    """Upsert del estado de un archivo. El hash solo se guarda cuando el estado es OK."""
    conteos = conteos or {}
    ok = estado == "OK"
    conexion.cursor().execute(
        f"""
        MERGE {tablas['archivos']} AS d
        USING (SELECT ? AS archivo) AS o ON d.archivo = o.archivo
        WHEN MATCHED THEN UPDATE SET
            periodo = ?, estado = ?, corrida_id = ?, ultimo_intento = SYSDATETIME(), detalle = ?,
            hash_sha256 = CASE WHEN ? = 1 THEN CAST(? AS BINARY(32)) ELSE d.hash_sha256 END,
            tamano_bytes = COALESCE(?, d.tamano_bytes),
            fecha_modificacion = COALESCE(?, d.fecha_modificacion),
            hoja = COALESCE(?, d.hoja),
            filas_leidas = ?, filas_sin_factura = ?, filas_validas = ?, filas_error = ?,
            filas_advertencia = ?, filas_duplicadas = ?, filas_insertadas = ?, filas_actualizadas = ?,
            ultima_carga_ok = CASE WHEN ? = 1 THEN SYSDATETIME() ELSE d.ultima_carga_ok END
        WHEN NOT MATCHED THEN INSERT (
            archivo, periodo, estado, corrida_id, ultimo_intento, detalle, hash_sha256, tamano_bytes,
            fecha_modificacion, hoja, filas_leidas, filas_sin_factura, filas_validas, filas_error,
            filas_advertencia, filas_duplicadas, filas_insertadas, filas_actualizadas, ultima_carga_ok)
        VALUES (o.archivo, ?, ?, ?, SYSDATETIME(), ?, CASE WHEN ? = 1 THEN CAST(? AS BINARY(32)) END, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, CASE WHEN ? = 1 THEN SYSDATETIME() END);
        """,
        archivo,
        periodo, estado, corrida_id, detalle, int(ok), hash_sha256, tamano_bytes, fecha_modificacion, hoja,
        *[conteos.get(k) for k in _CONTEOS], int(ok),
        periodo, estado, corrida_id, detalle, int(ok), hash_sha256, tamano_bytes, fecha_modificacion, hoja,
        *[conteos.get(k) for k in _CONTEOS], int(ok),
    )


_CONTEOS = ("leidas", "sin_factura", "validas", "error", "advertencia", "duplicadas", "insertadas", "actualizadas")


def hashes_cargados(conexion, tablas):
    """{archivo: hash} de los archivos cuya última carga terminó OK."""
    cursor = conexion.cursor()
    cursor.execute(f"SELECT archivo, hash_sha256 FROM {tablas['archivos']} WHERE estado = 'OK'")
    return {a: bytes(h) for a, h in cursor.fetchall() if h is not None}


def cargar_archivo(conexion, tablas, corrida_id, fuente, transformado, *, filas_leidas, hoja,
                   hash_sha256, tamano_bytes, fecha_modificacion, advertencias_archivo=()):
    """Carga un archivo completo en una transacción. Devuelve el dict de conteos.

    Si algo falla hace ROLLBACK y relanza la excepción (el llamador registra el error).
    """
    try:
        borrar_rechazos(conexion, tablas, fuente.nombre)
        insertar_rechazos(conexion, tablas, corrida_id, transformado.rechazos)
        en_staging = cargar_staging(conexion, transformado.validas, tablas)
        insertadas, actualizadas, duplicadas_core = ejecutar_merge(conexion, tablas, corrida_id)
        conteos = {
            "leidas": filas_leidas,
            "sin_factura": transformado.sin_factura,
            "validas": en_staging - duplicadas_core,
            "error": transformado.contar("ERROR"),
            "advertencia": transformado.contar("ADVERTENCIA"),
            "duplicadas": transformado.contar("DUPLICADO") + duplicadas_core,
            "insertadas": insertadas,
            "actualizadas": actualizadas,
            "staging": en_staging,
        }
        registrar_archivo(
            conexion, tablas, fuente.nombre, fuente.periodo, "OK", corrida_id,
            hash_sha256=hash_sha256, tamano_bytes=tamano_bytes, fecha_modificacion=fecha_modificacion,
            hoja=hoja, conteos=conteos, detalle="; ".join(advertencias_archivo) or None,
        )
        conexion.commit()
        return conteos
    except Exception:
        conexion.rollback()
        raise
