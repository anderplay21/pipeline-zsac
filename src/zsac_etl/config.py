"""Carga de configuración: config.yaml, columnas.yaml y variables de entorno (.env)."""

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

RAIZ_PROYECTO = Path(__file__).resolve().parents[2]
CARPETA_CONFIG = Path(os.getenv("ZSAC_CONFIG_DIR", RAIZ_PROYECTO / "config"))

TIPOS_VALIDOS = {"texto", "id", "fecha", "fecha_hora", "entero", "decimal"}


@dataclass(frozen=True)
class Columna:
    """Definición de una columna de negocio: cómo se llama en el Excel y cómo queda en SQL."""

    origen: str
    destino: str
    tipo: str
    obligatoria: bool = False
    longitud: int | None = None
    # Solo para decimales: cantidad de decimales con que se guarda (igual que en el DDL)
    escala: int | None = None
    # Solo para decimales: cómo interpretar "d.ddd" ambiguo ("miles" o "decimal")
    ambiguo: str | None = None
    # Si True, además del valor convertido se guarda el texto original en <destino>_txt
    guardar_txt: bool = False
    # Otros nombres aceptados en el encabezado para esta columna
    alias: tuple = ()

    def __post_init__(self):
        object.__setattr__(self, "alias", tuple(self.alias))


_SUFIJO_PANDAS = re.compile(r"\.\d+$")


def normalizar_nombre(nombre):
    """Nombre de columna comparable: sin espacios extremos, casefold y sin el sufijo ".N" de pandas."""
    return _SUFIJO_PANDAS.sub("", str(nombre).strip()).strip().casefold()


def cargar_config(carpeta=None):
    carpeta = Path(carpeta or CARPETA_CONFIG)
    load_dotenv(carpeta / ".env")
    with open(carpeta / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def cargar_columnas(carpeta=None):
    """Devuelve la lista ordenada de Columna definida en columnas.yaml."""
    carpeta = Path(carpeta or CARPETA_CONFIG)
    with open(carpeta / "columnas.yaml", "r", encoding="utf-8") as f:
        datos = yaml.safe_load(f)

    columnas = [Columna(**c) for c in datos["columnas"]]
    for c in columnas:
        if c.tipo not in TIPOS_VALIDOS:
            raise ValueError(f"Tipo inválido '{c.tipo}' en columna {c.origen!r}")
    return columnas
