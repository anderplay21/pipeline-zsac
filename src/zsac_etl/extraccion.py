"""Extracción: descubrir archivos ZSAC, copiarlos de forma segura y leer la hoja BD/DB.

Nunca escribe en la carpeta de origen: cada archivo se copia a una carpeta temporal y se
lee (y se le calcula el hash) desde esa copia.
"""

import hashlib
import logging
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from python_calamine import CalamineWorkbook

from zsac_etl.config import normalizar_nombre

logger = logging.getLogger("zsac_etl")

MESES = {
    "ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4, "MAYO": 5, "JUNIO": 6, "JULIO": 7,
    "AGOSTO": 8, "SEPTIEMBRE": 9, "SETIEMBRE": 9, "OCTUBRE": 10, "NOVIEMBRE": 11, "DICIEMBRE": 12,
}
PATRON_NOMBRE = re.compile(r"^ZSAC_(?P<mes>[A-ZÑ]+)_(?P<anio>\d{4})\.xlsx$", re.IGNORECASE)

COLUMNAS_TRAZA = ("archivo_origen", "fila_origen", "periodo")


class ArchivoRechazado(Exception):
    """El archivo completo no se puede cargar (hoja ausente, columna obligatoria faltante...)."""


class ArchivoBloqueado(Exception):
    """El archivo sigue bloqueado por otro proceso tras agotar los reintentos."""


@dataclass(frozen=True)
class ArchivoFuente:
    nombre: str
    ruta: Path
    periodo: date


@dataclass
class ResultadoExtraccion:
    datos: pd.DataFrame
    hoja: str
    advertencias: list = field(default_factory=list)


def descubrir_archivos(carpeta):
    """Lista los ZSAC_<MES>_<AAAA>.xlsx de la carpeta, ordenados por periodo."""
    fuentes = []
    for entrada in sorted(Path(carpeta).iterdir()):
        if not entrada.is_file():
            continue
        coincidencia = PATRON_NOMBRE.match(entrada.name)
        if not coincidencia:
            logger.info("Archivo ignorado (no cumple ZSAC_<MES>_<AAAA>.xlsx): %s", entrada.name)
            continue
        mes = MESES.get(coincidencia["mes"].upper())
        if mes is None:
            logger.warning("Archivo omitido, mes no válido '%s': %s", coincidencia["mes"], entrada.name)
            continue
        fuentes.append(ArchivoFuente(entrada.name, entrada, date(int(coincidencia["anio"]), mes, 1)))
    return sorted(fuentes, key=lambda f: (f.periodo, f.nombre))


def copiar_con_reintentos(ruta, carpeta_destino=None, reintentos=3, espera_segundos=10, dormir=time.sleep):
    """Copia el archivo a una carpeta temporal; reintenta mientras esté bloqueado.

    La copia también fuerza la descarga de archivos de OneDrive "solo en la nube".
    """
    carpeta_destino = Path(carpeta_destino or Path(tempfile.gettempdir()) / "zsac_etl")
    carpeta_destino.mkdir(parents=True, exist_ok=True)
    destino = carpeta_destino / Path(ruta).name

    for intento in range(1, reintentos + 1):
        try:
            shutil.copy2(ruta, destino)
            return destino
        except PermissionError as e:
            logger.warning("Archivo bloqueado (%s/%s): %s", intento, reintentos, Path(ruta).name)
            if intento == reintentos:
                raise ArchivoBloqueado(f"{Path(ruta).name} sigue bloqueado tras {reintentos} intentos") from e
            dormir(espera_segundos)


def calcular_sha256(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.digest()


def _vacio(v):
    return v is None or (isinstance(v, str) and v.strip() == "")


def _resolver_encabezado(encabezado, filas, columnas):
    """Empareja cada columna esperada con un índice del encabezado.

    Devuelve (mapa destino -> índice o None, advertencias). Lanza ArchivoRechazado si falta
    una columna obligatoria.
    """
    nombres = [normalizar_nombre(n) if not _vacio(n) else "" for n in encabezado]
    advertencias, mapa, usados = [], {}, set()

    for col in columnas:
        aceptados = {normalizar_nombre(n) for n in (col.origen, *col.alias)}
        candidatos = [i for i, n in enumerate(nombres) if n in aceptados]
        usados.update(candidatos)
        if not candidatos:
            mapa[col.destino] = None
            continue
        if len(candidatos) > 1:
            # Grupo de columnas repetidas (p. ej. "Fecha de factura" vacía + "Fecha de Factura"):
            # se usa la que tenga más valores.
            llenos = {i: sum(not _vacio(f[i]) for f in filas if i < len(f)) for i in candidatos}
            elegido = max(candidatos, key=lambda i: (llenos[i], -i))
            con_datos = [i for i in candidatos if llenos[i] > 0]
            if len(con_datos) > 1:
                advertencias.append(
                    f"'{col.origen}' aparece {len(candidatos)} veces con datos; se usa la columna "
                    f"{elegido + 1} ({llenos[elegido]} valores) y se descartan las demás"
                )
            mapa[col.destino] = elegido
        else:
            mapa[col.destino] = candidatos[0]

    faltantes = [c for c in columnas if mapa[c.destino] is None]
    obligatorias = [c.origen for c in faltantes if c.obligatoria]
    if obligatorias:
        raise ArchivoRechazado(f"faltan columnas obligatorias: {', '.join(obligatorias)}")
    for c in faltantes:
        advertencias.append(f"falta la columna '{c.origen}'; se carga en NULL")

    for i, n in enumerate(nombres):
        if i in usados:
            continue
        if n:
            advertencias.append(f"columna no esperada '{str(encabezado[i]).strip()}'; se omite")
        elif any(i < len(f) and not _vacio(f[i]) for f in filas):
            advertencias.append(f"columna {i + 1} sin encabezado con datos; se omite")

    return mapa, advertencias


def leer_archivo(ruta, fuente, columnas, hojas=("BD", "DB")):
    """Lee la hoja de datos completa (incluidas filas ocultas) y la deja con nombres destino.

    `ruta` es la copia local; `fuente` aporta nombre original y periodo para la trazabilidad.
    Los valores se devuelven tal como los entrega Excel (str, float, datetime...).
    """
    libro = CalamineWorkbook.from_path(str(ruta))
    hoja = next((h for h in hojas if h in libro.sheet_names), None)
    if hoja is None:
        raise ArchivoRechazado(f"hoja {'/'.join(hojas)} no encontrada (hojas: {libro.sheet_names})")

    # skip_empty_area=False: la fila 0 de la lista es la fila 1 de Excel.
    filas = libro.get_sheet_by_name(hoja).to_python(skip_empty_area=False)
    if not filas:
        raise ArchivoRechazado(f"la hoja {hoja} está vacía")

    encabezado, cuerpo = filas[0], filas[1:]
    mapa, advertencias = _resolver_encabezado(encabezado, cuerpo, columnas)

    registros, numeros_fila = [], []
    for n, fila in enumerate(cuerpo, start=2):
        if all(_vacio(v) for v in fila):
            continue
        registros.append([fila[i] if i is not None and i < len(fila) else None for i in mapa.values()])
        numeros_fila.append(n)

    datos = pd.DataFrame(registros, columns=list(mapa.keys()), dtype=object)
    datos["archivo_origen"] = fuente.nombre
    datos["fila_origen"] = numeros_fila
    datos["periodo"] = fuente.periodo
    return ResultadoExtraccion(datos=datos, hoja=hoja, advertencias=advertencias)


def info_archivo(ruta):
    """Tamaño y fecha de modificación del archivo original (solo lectura)."""
    st = os.stat(ruta)
    return st.st_size, datetime.fromtimestamp(st.st_mtime).replace(microsecond=0)
