from zsac_etl.config import cargar_columnas, cargar_config, normalizar_nombre


def test_config_carga_secciones_principales():
    config = cargar_config()
    assert config["sql_server"]["base_datos"] == "AnaliticaOperativa"
    assert config["origen"]["hojas"] == ["BD", "DB"]
    assert config["reglas"]["factura_min_digitos"] == 4


def test_columnas_destino_unicas_sin_choque_por_intercalacion():
    columnas = cargar_columnas()
    assert len(columnas) == 43
    destinos = [c.destino.casefold() for c in columnas]
    assert len(set(destinos)) == len(destinos)
    origenes = [normalizar_nombre(n) for c in columnas for n in (c.origen, *c.alias)]
    assert len(set(origenes)) == len(origenes)


def test_columnas_obligatorias_son_la_llave_y_la_fecha_de_factura():
    obligatorias = {c.destino for c in cargar_columnas() if c.obligatoria}
    assert obligatorias == {"factura", "clase_doc_fwo", "clase_expedicion", "fecha_factura"}


def test_normalizar_nombre():
    assert normalizar_nombre(" Fecha de Factura.1 ") == "fecha de factura"
    assert normalizar_nombre("Fecha de factura") == normalizar_nombre("Fecha de Factura")
    assert normalizar_nombre("Ciudad Org.") == "ciudad org."
