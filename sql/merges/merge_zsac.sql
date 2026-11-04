-- MERGE staging.ZSAC_Stage -> core.HechosZSAC (un archivo por ejecución).
-- Llave de negocio: factura + clase_doc_fwo + clase_expedicion.
-- Regla "la primera aparición gana" (ordenando por periodo del archivo):
--   * Llave ya cargada desde OTRO archivo con periodo <= al de staging -> la fila de staging es DUPLICADO.
--   * Llave cargada desde el MISMO archivo -> se actualiza solo si cambió row_hash.
--   * Llave cargada desde un archivo de periodo POSTERIOR -> se reemplaza y la fila desplazada
--     queda como DUPLICADO de su archivo.
-- IMPORTANTE: sin WHEN NOT MATCHED BY SOURCE. Nunca se borra histórico.
-- Un solo lote (sin GO): se ejecuta con pyodbc pasando corrida_id como parámetro.
-- Devuelve: FilasInsertadas, FilasActualizadas, FilasDuplicadas.

SET NOCOUNT ON;
DECLARE @corrida_id UNIQUEIDENTIFIER = ?;

DECLARE @Duplicadas TABLE (
    factura NVARCHAR(50), clase_doc_fwo NVARCHAR(10), clase_expedicion NVARCHAR(10),
    archivo_origen NVARCHAR(100), fila_origen INT,
    archivo_gana NVARCHAR(100), fila_gana INT
);

INSERT INTO @Duplicadas
SELECT s.factura, s.clase_doc_fwo, s.clase_expedicion, s.archivo_origen, s.fila_origen,
       d.archivo_origen, d.fila_origen
FROM staging.ZSAC_Stage AS s
JOIN core.HechosZSAC AS d
  ON d.factura = s.factura
 AND d.clase_doc_fwo = s.clase_doc_fwo
 AND d.clase_expedicion = s.clase_expedicion
WHERE d.archivo_origen <> s.archivo_origen
  AND d.periodo <= s.periodo;

INSERT INTO core.ZSAC_Rechazos (corrida_id, archivo, fila_origen, tipo, columna, valor, motivo)
SELECT @corrida_id, archivo_origen, fila_origen, N'DUPLICADO', N'llave',
       CONCAT(factura, N' | ', clase_doc_fwo, N' | ', clase_expedicion),
       CONCAT(N'Llave ya cargada desde ', archivo_gana, N' fila ', fila_gana)
FROM @Duplicadas;

DECLARE @ResultadoMerge TABLE (
    Accion NVARCHAR(10),
    archivo_anterior NVARCHAR(100), fila_anterior INT,
    archivo_nuevo NVARCHAR(100), fila_nueva INT,
    factura NVARCHAR(50), clase_doc_fwo NVARCHAR(10), clase_expedicion NVARCHAR(10)
);

MERGE core.HechosZSAC AS destino
USING (
    SELECT s.*
    FROM staging.ZSAC_Stage AS s
    WHERE NOT EXISTS (
        SELECT 1 FROM @Duplicadas AS x
        WHERE x.archivo_origen = s.archivo_origen AND x.fila_origen = s.fila_origen
    )
) AS origen
    ON destino.factura = origen.factura
   AND destino.clase_doc_fwo = origen.clase_doc_fwo
   AND destino.clase_expedicion = origen.clase_expedicion
WHEN MATCHED AND (
        (destino.archivo_origen = origen.archivo_origen AND destino.row_hash <> origen.row_hash)
     OR (destino.archivo_origen <> origen.archivo_origen AND origen.periodo < destino.periodo)
) THEN
    UPDATE SET
        destino.cliente = origen.cliente,
        destino.destinatario = origen.destinatario,
        destino.ciudad_origen = origen.ciudad_origen,
        destino.bp_centro_origen = origen.bp_centro_origen,
        destino.centro_origen = origen.centro_origen,
        destino.ciudad_destino = origen.ciudad_destino,
        destino.bp_centro_destino = origen.bp_centro_destino,
        destino.centro_destino = origen.centro_destino,
        destino.bp_origen_fu = origen.bp_origen_fu,
        destino.bp_destino_fu = origen.bp_destino_fu,
        destino.direccion_cliente = origen.direccion_cliente,
        destino.fecha_entrega = origen.fecha_entrega,
        destino.ultima_ubicacion = origen.ultima_ubicacion,
        destino.remision = origen.remision,
        destino.fecha_entrega_planeada = origen.fecha_entrega_planeada,
        destino.fecha_cumplido = origen.fecha_cumplido,
        destino.cajas = origen.cajas,
        destino.kilos = origen.kilos,
        destino.kilos_txt = origen.kilos_txt,
        destino.volumen = origen.volumen,
        destino.volumen_txt = origen.volumen_txt,
        destino.ultimo_evento = origen.ultimo_evento,
        destino.sigla_causal = origen.sigla_causal,
        destino.novedad = origen.novedad,
        destino.clase_movimiento = origen.clase_movimiento,
        destino.conductor = origen.conductor,
        destino.telefono = origen.telefono,
        destino.placa = origen.placa,
        destino.manifiesto = origen.manifiesto,
        destino.carta_porte = origen.carta_porte,
        destino.orden_compra = origen.orden_compra,
        destino.orden_pedido = origen.orden_pedido,
        destino.orden_entrega = origen.orden_entrega,
        destino.fecha_cita = origen.fecha_cita,
        destino.on_time = origen.on_time,
        destino.documento_escaneado = origen.documento_escaneado,
        destino.tipo_ruta = origen.tipo_ruta,
        destino.fecha_factura = origen.fecha_factura,
        destino.fecha_compromiso = origen.fecha_compromiso,
        destino.fecha_operacion = origen.fecha_operacion,
        destino.fecha_leadtime = origen.fecha_leadtime,
        destino.valor_declarado = origen.valor_declarado,
        destino.numero_ambiguo = origen.numero_ambiguo,
        destino.archivo_origen = origen.archivo_origen,
        destino.fila_origen = origen.fila_origen,
        destino.periodo = origen.periodo,
        destino.row_hash = origen.row_hash,
        destino.fecha_ultima_actualizacion = SYSDATETIME()
WHEN NOT MATCHED BY TARGET THEN
    INSERT (
        cliente, destinatario, ciudad_origen, bp_centro_origen, centro_origen, ciudad_destino,
        bp_centro_destino, centro_destino, bp_origen_fu, bp_destino_fu, factura, direccion_cliente,
        fecha_entrega, ultima_ubicacion, remision, fecha_entrega_planeada, fecha_cumplido, cajas,
        kilos, kilos_txt, volumen, volumen_txt, ultimo_evento, sigla_causal, novedad,
        clase_expedicion, clase_movimiento, conductor, telefono, placa, manifiesto, carta_porte,
        orden_compra, orden_pedido, orden_entrega, clase_doc_fwo, fecha_cita, on_time,
        documento_escaneado, tipo_ruta, fecha_factura, fecha_compromiso, fecha_operacion,
        fecha_leadtime, valor_declarado, numero_ambiguo, archivo_origen, fila_origen, periodo, row_hash
    )
    VALUES (
        origen.cliente, origen.destinatario, origen.ciudad_origen, origen.bp_centro_origen,
        origen.centro_origen, origen.ciudad_destino, origen.bp_centro_destino, origen.centro_destino,
        origen.bp_origen_fu, origen.bp_destino_fu, origen.factura, origen.direccion_cliente,
        origen.fecha_entrega, origen.ultima_ubicacion, origen.remision, origen.fecha_entrega_planeada,
        origen.fecha_cumplido, origen.cajas, origen.kilos, origen.kilos_txt, origen.volumen,
        origen.volumen_txt, origen.ultimo_evento, origen.sigla_causal, origen.novedad,
        origen.clase_expedicion, origen.clase_movimiento, origen.conductor, origen.telefono,
        origen.placa, origen.manifiesto, origen.carta_porte, origen.orden_compra, origen.orden_pedido,
        origen.orden_entrega, origen.clase_doc_fwo, origen.fecha_cita, origen.on_time,
        origen.documento_escaneado, origen.tipo_ruta, origen.fecha_factura, origen.fecha_compromiso,
        origen.fecha_operacion, origen.fecha_leadtime, origen.valor_declarado, origen.numero_ambiguo,
        origen.archivo_origen, origen.fila_origen, origen.periodo, origen.row_hash
    )
OUTPUT $action, deleted.archivo_origen, deleted.fila_origen, inserted.archivo_origen, inserted.fila_origen,
       inserted.factura, inserted.clase_doc_fwo, inserted.clase_expedicion
INTO @ResultadoMerge;

-- Filas desplazadas por un archivo de periodo anterior: quedan como DUPLICADO de su propio archivo.
INSERT INTO core.ZSAC_Rechazos (corrida_id, archivo, fila_origen, tipo, columna, valor, motivo)
SELECT @corrida_id, archivo_anterior, fila_anterior, N'DUPLICADO', N'llave',
       CONCAT(factura, N' | ', clase_doc_fwo, N' | ', clase_expedicion),
       CONCAT(N'Llave reemplazada por ', archivo_nuevo, N' fila ', fila_nueva, N' (periodo anterior)')
FROM @ResultadoMerge
WHERE Accion = 'UPDATE' AND archivo_anterior <> archivo_nuevo;

SELECT
    (SELECT COUNT(*) FROM @ResultadoMerge WHERE Accion = 'INSERT') AS FilasInsertadas,
    (SELECT COUNT(*) FROM @ResultadoMerge WHERE Accion = 'UPDATE') AS FilasActualizadas,
    (SELECT COUNT(*) FROM @Duplicadas) AS FilasDuplicadas;
