import logging
from datetime import date, datetime

import pytest
from openpyxl import Workbook

from zsac_etl.config import cargar_columnas
from zsac_etl.extraccion import ArchivoFuente


@pytest.fixture(scope="session")
def columnas():
    return cargar_columnas()


@pytest.fixture(scope="session")
def encabezado_estandar(columnas):
    """Encabezado real del reporte: incluye la 'Fecha de factura' vacía junto a 'Fecha de Factura'."""
    nombres = [c.origen for c in columnas]
    nombres.insert(nombres.index("Cl Doc FWO"), "Fecha de factura")
    return nombres


def fila_valida(**cambios):
    """Una fila de datos típica, con los valores tal como los entrega Excel."""
    base = {
        "Cliente": "NESTLE PURINA DE COLOMBIA S.A",
        "destinatario": "Almacenes Exito S.A.",
        "Ciudad Org.": "MOSQUERA",
        "BP Centro Org": "0003009174",
        "Centro Org": "ICOLTRANS COTA CEDI",
        "Ciudad Dest.": "FUNZA",
        "BP Centro Dest.": "0003108892",
        "Centro Dest.": "ICOLTRANS COTA CEDI",
        "BP Origen FU": "0003009174",
        "BP Destino FU": "0003108892",
        "Factura": "9961583720",
        "Dir. Cliente": "Via Mosquera Kilometro 2",
        "Fecha Entrega": "05.01.2026-10:00",
        "Ultima Ubicacion": "FUNZA",
        "Remision": "00000000002104613011",
        "Fecha de Entrega Pla": "05.01.2026-00:00",
        "Fecha de Cumplido": None,
        "Cajas": "0000000008",
        "Kilos": "50,16",
        "Volumen": "0,12",
        "Ult. Evento": "Documentos Escaneados",
        "Sigla Causal": None,
        "Novedad.": None,
        "Cl. Expedicion": "Z5",
        "Cl. Movimiento": "ICN",
        "Conductor": None,
        "Telefono": None,
        "Placa": "TRACTOMULA",
        "Manifiesto": "00000000006100441992",
        "Carta Porte": "8031113559",
        "Orden de Compra": "5530114408",
        "Orden de Pedido": "0085444124\r",
        "Orden de Entrega/Del": None,
        "Fecha de factura": None,
        "Cl Doc FWO": "Z001",
        "Fecha de cita de com": None,
        "On Time": None,
        "Documento Escaneado": "Descargar",
        "Tipo de ruta": "URBANO",
        "Fecha de Factura": datetime(2026, 1, 10),
        "Fec. Compromiso": datetime(2026, 1, 12),
        "Fecha de Operación": datetime(2026, 1, 10),
        "Fecha de LeadTime": datetime(2026, 1, 12),
        "Vlr. declarado": 58619604,
    }
    base.update(cambios)
    return base


@pytest.fixture
def crear_xlsx(tmp_path, encabezado_estandar):
    """Crea un xlsx mínimo. `filas` son dicts por nombre de encabezado."""

    def _crear(nombre="ZSAC_ENERO_2026.xlsx", filas=None, encabezado=None, hoja="BD", ocultar=()):
        encabezado = encabezado or encabezado_estandar
        filas = filas if filas is not None else [fila_valida()]
        libro = Workbook()
        ws = libro.active
        ws.title = hoja
        ws.append(encabezado)
        for f in filas:
            ws.append([f.get(n) for n in encabezado])
        for n in ocultar:
            ws.row_dimensions[n].hidden = True
        if ocultar:
            ws.auto_filter.ref = ws.dimensions
        ruta = tmp_path / nombre
        libro.save(ruta)
        return ruta

    return _crear


@pytest.fixture
def fuente_enero(tmp_path):
    return ArchivoFuente("ZSAC_ENERO_2026.xlsx", tmp_path / "ZSAC_ENERO_2026.xlsx", date(2026, 1, 1))


@pytest.fixture(autouse=True)
def _log_propagado():
    logging.getLogger("zsac_etl").propagate = True
