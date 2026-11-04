-- Estructuras del pipeline ZSAC en AnaliticaOperativa.
-- Idempotente: solo crea lo que no existe; nunca borra ni altera tablas existentes.
-- Las columnas de negocio salen de config/columnas.yaml (mismo orden).

USE AnaliticaOperativa;
GO

IF SCHEMA_ID('staging') IS NULL EXEC('CREATE SCHEMA staging');
GO
IF SCHEMA_ID('core') IS NULL EXEC('CREATE SCHEMA core');
GO

-- Lote en curso (un archivo a la vez). Se trunca antes de cada carga.
IF OBJECT_ID('staging.ZSAC_Stage', 'U') IS NULL
CREATE TABLE staging.ZSAC_Stage (
    cliente                NVARCHAR(100)  NULL,
    destinatario           NVARCHAR(150)  NULL,
    ciudad_origen          NVARCHAR(60)   NULL,
    bp_centro_origen       NVARCHAR(20)   NULL,
    centro_origen          NVARCHAR(100)  NULL,
    ciudad_destino         NVARCHAR(60)   NULL,
    bp_centro_destino      NVARCHAR(20)   NULL,
    centro_destino         NVARCHAR(100)  NULL,
    bp_origen_fu           NVARCHAR(20)   NULL,
    bp_destino_fu          NVARCHAR(20)   NULL,
    factura                NVARCHAR(50)   NOT NULL,
    direccion_cliente      NVARCHAR(250)  NULL,
    fecha_entrega          DATETIME2(0)   NULL,
    ultima_ubicacion       NVARCHAR(100)  NULL,
    remision               NVARCHAR(30)   NULL,
    fecha_entrega_planeada DATETIME2(0)   NULL,
    fecha_cumplido         DATETIME2(0)   NULL,
    cajas                  INT            NULL,
    kilos                  DECIMAL(18,3)  NULL,
    kilos_txt              NVARCHAR(30)   NULL,
    volumen                DECIMAL(18,6)  NULL,
    volumen_txt            NVARCHAR(30)   NULL,
    ultimo_evento          NVARCHAR(100)  NULL,
    sigla_causal           NVARCHAR(30)   NULL,
    novedad                NVARCHAR(250)  NULL,
    clase_expedicion       NVARCHAR(10)   NOT NULL,
    clase_movimiento       NVARCHAR(10)   NULL,
    conductor              NVARCHAR(150)  NULL,
    telefono               NVARCHAR(50)   NULL,
    placa                  NVARCHAR(30)   NULL,
    manifiesto             NVARCHAR(30)   NULL,
    carta_porte            NVARCHAR(60)   NULL,
    orden_compra           NVARCHAR(60)   NULL,
    orden_pedido           NVARCHAR(60)   NULL,
    orden_entrega          NVARCHAR(60)   NULL,
    clase_doc_fwo          NVARCHAR(10)   NOT NULL,
    fecha_cita             DATETIME2(0)   NULL,
    on_time                NVARCHAR(20)   NULL,
    documento_escaneado    NVARCHAR(30)   NULL,
    tipo_ruta              NVARCHAR(30)   NULL,
    fecha_factura          DATE           NOT NULL,
    fecha_compromiso       DATE           NULL,
    fecha_operacion        DATE           NULL,
    fecha_leadtime         DATE           NULL,
    valor_declarado        DECIMAL(18,2)  NULL,
    numero_ambiguo         BIT            NOT NULL,
    archivo_origen         NVARCHAR(100)  NOT NULL,
    fila_origen            INT            NOT NULL,
    periodo                DATE           NOT NULL,
    row_hash               BINARY(32)     NOT NULL
);
GO

-- Hechos ZSAC. Llave de negocio: factura + clase_doc_fwo + clase_expedicion (única).
IF OBJECT_ID('core.HechosZSAC', 'U') IS NULL
CREATE TABLE core.HechosZSAC (
    id_zsac                    BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_HechosZSAC PRIMARY KEY,
    cliente                NVARCHAR(100)  NULL,
    destinatario           NVARCHAR(150)  NULL,
    ciudad_origen          NVARCHAR(60)   NULL,
    bp_centro_origen       NVARCHAR(20)   NULL,
    centro_origen          NVARCHAR(100)  NULL,
    ciudad_destino         NVARCHAR(60)   NULL,
    bp_centro_destino      NVARCHAR(20)   NULL,
    centro_destino         NVARCHAR(100)  NULL,
    bp_origen_fu           NVARCHAR(20)   NULL,
    bp_destino_fu          NVARCHAR(20)   NULL,
    factura                NVARCHAR(50)   NOT NULL,
    direccion_cliente      NVARCHAR(250)  NULL,
    fecha_entrega          DATETIME2(0)   NULL,
    ultima_ubicacion       NVARCHAR(100)  NULL,
    remision               NVARCHAR(30)   NULL,
    fecha_entrega_planeada DATETIME2(0)   NULL,
    fecha_cumplido         DATETIME2(0)   NULL,
    cajas                  INT            NULL,
    kilos                  DECIMAL(18,3)  NULL,
    kilos_txt              NVARCHAR(30)   NULL,
    volumen                DECIMAL(18,6)  NULL,
    volumen_txt            NVARCHAR(30)   NULL,
    ultimo_evento          NVARCHAR(100)  NULL,
    sigla_causal           NVARCHAR(30)   NULL,
    novedad                NVARCHAR(250)  NULL,
    clase_expedicion       NVARCHAR(10)   NOT NULL,
    clase_movimiento       NVARCHAR(10)   NULL,
    conductor              NVARCHAR(150)  NULL,
    telefono               NVARCHAR(50)   NULL,
    placa                  NVARCHAR(30)   NULL,
    manifiesto             NVARCHAR(30)   NULL,
    carta_porte            NVARCHAR(60)   NULL,
    orden_compra           NVARCHAR(60)   NULL,
    orden_pedido           NVARCHAR(60)   NULL,
    orden_entrega          NVARCHAR(60)   NULL,
    clase_doc_fwo          NVARCHAR(10)   NOT NULL,
    fecha_cita             DATETIME2(0)   NULL,
    on_time                NVARCHAR(20)   NULL,
    documento_escaneado    NVARCHAR(30)   NULL,
    tipo_ruta              NVARCHAR(30)   NULL,
    fecha_factura          DATE           NOT NULL,
    fecha_compromiso       DATE           NULL,
    fecha_operacion        DATE           NULL,
    fecha_leadtime         DATE           NULL,
    valor_declarado        DECIMAL(18,2)  NULL,
    numero_ambiguo         BIT            NOT NULL,
    archivo_origen         NVARCHAR(100)  NOT NULL,
    fila_origen            INT            NOT NULL,
    periodo                DATE           NOT NULL,
    row_hash               BINARY(32)     NOT NULL,
    fecha_carga                DATETIME2(0) NOT NULL CONSTRAINT DF_HechosZSAC_fecha_carga DEFAULT SYSDATETIME(),
    fecha_ultima_actualizacion DATETIME2(0) NULL
);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UX_HechosZSAC_llave' AND object_id = OBJECT_ID('core.HechosZSAC'))
CREATE UNIQUE INDEX UX_HechosZSAC_llave ON core.HechosZSAC (factura, clase_doc_fwo, clase_expedicion);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_HechosZSAC_archivo' AND object_id = OBJECT_ID('core.HechosZSAC'))
CREATE INDEX IX_HechosZSAC_archivo ON core.HechosZSAC (archivo_origen);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_HechosZSAC_fecha_factura' AND object_id = OBJECT_ID('core.HechosZSAC'))
CREATE INDEX IX_HechosZSAC_fecha_factura ON core.HechosZSAC (fecha_factura);
GO

-- Errores, advertencias y duplicados por fila. Se reemplazan por archivo en cada reproceso.
IF OBJECT_ID('core.ZSAC_Rechazos', 'U') IS NULL
CREATE TABLE core.ZSAC_Rechazos (
    id_rechazo     BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_ZSAC_Rechazos PRIMARY KEY,
    corrida_id     UNIQUEIDENTIFIER NOT NULL,
    archivo        NVARCHAR(100)    NOT NULL,
    fila_origen    INT              NULL,
    tipo           NVARCHAR(15)     NOT NULL,   -- ERROR | ADVERTENCIA | DUPLICADO
    columna        NVARCHAR(60)     NULL,
    valor          NVARCHAR(400)    NULL,
    motivo         NVARCHAR(400)    NOT NULL,
    fecha_registro DATETIME2(0)     NOT NULL CONSTRAINT DF_ZSAC_Rechazos_fecha DEFAULT SYSDATETIME()
);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ZSAC_Rechazos_archivo' AND object_id = OBJECT_ID('core.ZSAC_Rechazos'))
CREATE INDEX IX_ZSAC_Rechazos_archivo ON core.ZSAC_Rechazos (archivo, tipo);
GO

-- Estado por archivo de origen. hash_sha256 solo cambia cuando la carga termina bien.
IF OBJECT_ID('core.ZSAC_Archivos', 'U') IS NULL
CREATE TABLE core.ZSAC_Archivos (
    archivo             NVARCHAR(100)    NOT NULL CONSTRAINT PK_ZSAC_Archivos PRIMARY KEY,
    periodo             DATE             NOT NULL,
    hash_sha256         BINARY(32)       NULL,
    tamano_bytes        BIGINT           NULL,
    fecha_modificacion  DATETIME2(0)     NULL,
    hoja                NVARCHAR(31)     NULL,
    estado              NVARCHAR(15)     NOT NULL,   -- OK | ERROR | OMITIDO | RECHAZADO
    filas_leidas        INT              NULL,
    filas_sin_factura   INT              NULL,
    filas_validas       INT              NULL,
    filas_error         INT              NULL,
    filas_advertencia   INT              NULL,
    filas_duplicadas    INT              NULL,
    filas_insertadas    INT              NULL,
    filas_actualizadas  INT              NULL,
    ultima_carga_ok     DATETIME2(0)     NULL,
    ultimo_intento      DATETIME2(0)     NOT NULL,
    corrida_id          UNIQUEIDENTIFIER NULL,
    detalle             NVARCHAR(MAX)    NULL
);
GO
