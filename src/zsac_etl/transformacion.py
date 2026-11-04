"""Transformación: limpieza, conversión de tipos, filtro de factura real, rechazos y row_hash.

Entrada: el DataFrame de `extraccion.leer_archivo` (columnas destino con valores crudos de Excel).
Salida: filas válidas ya tipadas (str/int/Decimal/date/datetime/None) + lista de rechazos.
"""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import lru_cache

import pandas as pd

from zsac_etl.extraccion import COLUMNAS_TRAZA

LLAVE = ("factura", "clase_doc_fwo", "clase_expedicion")

FORMATOS_FECHA = ("%d.%m.%Y-%H:%M", "%d.%m.%Y-%H:%M:%S", "%d.%m.%Y")
ORIGEN_EXCEL = datetime(1899, 12, 30)
SERIAL_MIN, SERIAL_MAX = 20000, 80000   # 1954 .. 2119: fuera de eso no es un serial de fecha creíble
_CARACTERES_EXTREMOS = " \t\r\n\xa0"
# Marcas con que SAP exporta una fecha vacía: "00.00.0000[-00:00[:00]]" y "693596-"
_FECHA_VACIA_SAP = re.compile(r"^(00\.00\.0000(-00:00(:00)?)?|693596-)$")
_NUMERO = re.compile(r"^[+-]?[\d.,]+$")
_SEP = "\x1f"


class ValorInvalido(ValueError):
    """El valor no se puede convertir al tipo de la columna; el mensaje es el motivo."""


@dataclass
class ResultadoTransformacion:
    validas: pd.DataFrame
    rechazos: list = field(default_factory=list)
    sin_factura: int = 0
    fechas_vacias_sap: int = 0

    def contar(self, tipo):
        if tipo == "ERROR":
            # Una fila con varios valores inválidos cuenta una sola vez
            return len({r["fila_origen"] for r in self.rechazos if r["tipo"] == "ERROR"})
        return sum(1 for r in self.rechazos if r["tipo"] == tipo)


def columnas_salida(columnas):
    """Orden de columnas de staging/core: negocio (+ _txt), marca de ambiguo, traza y hash."""
    nombres = []
    for c in columnas:
        nombres.append(c.destino)
        if c.guardar_txt:
            nombres.append(f"{c.destino}_txt")
    return nombres + ["numero_ambiguo", *COLUMNAS_TRAZA, "row_hash"]


# --- conversores por tipo -------------------------------------------------------------

def limpiar_texto(valor):
    """Texto sin espacios/saltos de línea en los extremos; vacío -> None. Números -> su texto."""
    if valor is None:
        return None
    if isinstance(valor, float):
        if valor != valor:   # NaN
            return None
        valor = int(valor) if valor.is_integer() else repr(valor)
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    texto = str(valor).strip(_CARACTERES_EXTREMOS)
    return texto or None


def limpiar_id(valor):
    """Identificador como texto; si es solo dígitos, sin ceros a la izquierda ("000" -> "0")."""
    texto = limpiar_texto(valor)
    if texto is not None and texto.isdigit():
        return texto.lstrip("0") or "0"
    return texto


@lru_cache(maxsize=200_000)
def _parsear_fecha_texto(texto):
    for formato in FORMATOS_FECHA:
        try:
            return datetime.strptime(texto, formato)
        except ValueError:
            continue
    raise ValorInvalido("fecha inválida")


def parsear_fecha(valor, solo_fecha):
    """datetime/date nativo, texto dd.MM.yyyy[-HH:mm[:ss]] o serial de Excel. Nunca infiere día/mes."""
    if valor is None or (isinstance(valor, str) and not valor.strip(_CARACTERES_EXTREMOS)):
        return None
    if isinstance(valor, datetime):
        resultado = valor
    elif isinstance(valor, date):
        resultado = datetime(valor.year, valor.month, valor.day)
    elif isinstance(valor, (int, float)) and not isinstance(valor, bool):
        if not SERIAL_MIN <= valor <= SERIAL_MAX:
            raise ValorInvalido("fecha inválida (número fuera de rango)")
        resultado = ORIGEN_EXCEL + timedelta(days=float(valor))
        resultado = resultado.replace(microsecond=0)
    else:
        texto = str(valor).strip(_CARACTERES_EXTREMOS)
        if _FECHA_VACIA_SAP.match(texto):
            return None
        resultado = _parsear_fecha_texto(texto)
    return resultado.date() if solo_fecha else resultado.replace(microsecond=0)


@lru_cache(maxsize=200_000)
def _parsear_decimal_texto(texto, ambiguo):
    s = texto.replace(" ", "")
    if not _NUMERO.match(s):
        raise ValorInvalido("número inválido")
    es_ambiguo = False
    if "," in s:
        if s.count(",") > 1:
            raise ValorInvalido("número inválido (varias comas)")
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    elif "." in s and len(s.rsplit(".", 1)[1]) == 3:
        es_ambiguo = True
        if ambiguo == "miles":
            s = s.replace(".", "")
    try:
        return Decimal(s), es_ambiguo
    except InvalidOperation as e:
        raise ValorInvalido("número inválido") from e


def parsear_decimal(valor, ambiguo="miles"):
    """Devuelve (Decimal | None, es_ambiguo). Ver reglas en design.md D3."""
    if valor is None:
        return None, False
    if isinstance(valor, bool):
        raise ValorInvalido("número inválido")
    if isinstance(valor, int):
        return Decimal(valor), False
    if isinstance(valor, float):
        if valor != valor:
            return None, False
        return Decimal(repr(valor)), False
    texto = str(valor).strip(_CARACTERES_EXTREMOS)
    if not texto:
        return None, False
    return _parsear_decimal_texto(texto, ambiguo or "miles")


def _a_escala(numero, escala, max_digitos=18):
    if numero is None:
        return None
    if escala is not None:
        numero = numero.quantize(Decimal(1).scaleb(-escala), rounding=ROUND_HALF_UP)
    if len(numero.as_tuple().digits) > max_digitos or numero.adjusted() >= max_digitos - (escala or 0):
        raise ValorInvalido("número fuera de rango")
    return numero


def parsear_entero(valor):
    numero, _ = parsear_decimal(valor, ambiguo="miles")
    if numero is None:
        return None
    if numero != numero.to_integral_value():
        raise ValorInvalido("entero inválido (tiene decimales)")
    if abs(numero) > 2_147_483_647:
        raise ValorInvalido("entero fuera de rango")
    return int(numero)


def factura_real(factura, min_digitos):
    """True si la factura (ya limpia) contiene al menos `min_digitos` dígitos consecutivos."""
    return factura is not None and re.search(r"\d{%d,}" % min_digitos, factura) is not None


# --- huella ---------------------------------------------------------------------------

def _canonico(v):
    if v is None:
        return ""
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return format(v, "f")
    return str(v)


def calcular_row_hash(valores):
    return hashlib.sha256(_SEP.join(_canonico(v) for v in valores).encode("utf-8")).digest()


# --- transformación completa ----------------------------------------------------------

def _rechazo(fila, archivo, tipo, columna, valor, motivo):
    valor_txt = None if valor is None else str(valor)[:400]
    return {"archivo": archivo, "fila_origen": int(fila), "tipo": tipo,
            "columna": columna, "valor": valor_txt, "motivo": motivo}


def transformar(datos, columnas, factura_min_digitos=4):
    """Aplica todas las reglas de transformación a un archivo ya extraído."""
    rechazos = []

    # 1) Filtro de factura real (antes de cualquier otra validación: esas filas no son rechazos)
    col_factura = next(c for c in columnas if c.destino == "factura")
    facturas = [limpiar_id(v) for v in datos["factura"]]
    mascara = [factura_real(f, factura_min_digitos) for f in facturas]
    sin_factura = len(mascara) - sum(mascara)
    datos = datos.loc[mascara].reset_index(drop=True)
    facturas = [f for f, ok in zip(facturas, mascara) if ok]

    archivos = list(datos["archivo_origen"])
    filas = list(datos["fila_origen"])
    n = len(datos)
    fila_con_error = [False] * n
    fechas_vacias_sap = 0
    ambiguo = [False] * n
    salida = {}

    for col in columnas:
        crudos = list(datos[col.destino]) if col.destino != "factura" else facturas
        convertidos = [None] * n
        textos = [None] * n if col.guardar_txt else None

        for i, crudo in enumerate(crudos):
            try:
                if col.destino == "factura":
                    valor = crudo
                elif col.tipo == "texto":
                    valor = limpiar_texto(crudo)
                elif col.tipo == "id":
                    valor = limpiar_id(crudo)
                elif col.tipo in ("fecha", "fecha_hora"):
                    valor = parsear_fecha(crudo, solo_fecha=col.tipo == "fecha")
                    if valor is None and isinstance(crudo, str) and _FECHA_VACIA_SAP.match(crudo.strip()):
                        fechas_vacias_sap += 1
                elif col.tipo == "entero":
                    valor = parsear_entero(crudo)
                else:
                    numero, es_ambiguo = parsear_decimal(crudo, col.ambiguo)
                    valor = _a_escala(numero, col.escala)
                    ambiguo[i] = ambiguo[i] or es_ambiguo

                if isinstance(valor, str) and col.longitud and len(valor) > col.longitud:
                    if col.obligatoria:
                        raise ValorInvalido(f"texto excede {col.longitud} caracteres")
                    rechazos.append(_rechazo(filas[i], archivos[i], "ADVERTENCIA", col.destino, valor,
                                             f"texto truncado a {col.longitud} caracteres"))
                    valor = valor[: col.longitud]
                if valor is None and col.obligatoria:
                    raise ValorInvalido("valor obligatorio vacío")
            except ValorInvalido as e:
                valor = None
                tipo = "ERROR" if col.obligatoria else "ADVERTENCIA"
                fila_con_error[i] = fila_con_error[i] or col.obligatoria
                rechazos.append(_rechazo(filas[i], archivos[i], tipo, col.destino, crudo, str(e)))

            convertidos[i] = valor
            if textos is not None:
                textos[i] = limpiar_texto(crudo)

        salida[col.destino] = convertidos
        if textos is not None:
            salida[f"{col.destino}_txt"] = textos

    salida["numero_ambiguo"] = [int(a) for a in ambiguo]
    for c in COLUMNAS_TRAZA:
        salida[c] = list(datos[c])

    validas = pd.DataFrame(salida, dtype=object)
    validas = validas.loc[[not e for e in fila_con_error]]

    # 2) Deduplicación por llave: la primera aparición (periodo, fila) gana.
    # La comparación ignora mayúsculas, igual que el índice único en SQL Server (intercalación CI).
    validas = validas.sort_values(["periodo", "fila_origen"], kind="stable").reset_index(drop=True)
    llave_ci = validas[list(LLAVE)].apply(lambda s: s.str.casefold())
    repetida = llave_ci.duplicated(keep="first")
    if repetida.any():
        primera = validas["fila_origen"].groupby([llave_ci[k] for k in LLAVE], sort=False).transform("first")
        for i in validas.index[repetida]:
            r = validas.loc[i]
            rechazos.append(_rechazo(r["fila_origen"], r["archivo_origen"], "DUPLICADO", "llave",
                                     " | ".join(str(r[k]) for k in LLAVE),
                                     f"Llave repetida en el archivo; prevalece la fila {primera[i]}"))
        validas = validas.loc[~repetida].reset_index(drop=True)

    # 3) Huella sobre las columnas de negocio (sin trazabilidad)
    negocio = [c for c in columnas_salida(columnas) if c not in (*COLUMNAS_TRAZA, "row_hash")]
    validas["row_hash"] = [calcular_row_hash(fila) for fila in validas[negocio].itertuples(index=False)]

    return ResultadoTransformacion(validas=validas[columnas_salida(columnas)], rechazos=rechazos,
                                   sin_factura=sin_factura, fechas_vacias_sap=fechas_vacias_sap)
