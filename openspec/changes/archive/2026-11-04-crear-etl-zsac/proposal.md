# Proposal

## Why

Los reportes mensuales ZSAC (exportación SAP de despachos y entregas) se acumulan como archivos Excel sueltos en una carpeta de OneDrive (21 archivos, unas 1M filas). Para analizarlos hoy hay que abrirlos y consolidarlos a mano. Los tipos vienen mezclados: fechas como texto `dd.MM.yyyy-HH:mm` o como seriales de Excel, IDs con ceros a la izquierda y números como texto. Además, los archivos se reemplazan cuando SAP corrige datos. Se necesita una fuente única, tipada y actualizada en SQL Server (AnaliticaOperativa) que se refresque sola.

## What Changes

- Nuevo pipeline Python `zsac_etl` que descubre los archivos `ZSAC_<MES>_<AAAA>.xlsx` de la carpeta de origen y lee la hoja `BD` completa, incluidas las filas ocultas por el autofiltro.
- Validación estructural del encabezado (44 columnas esperadas) y trazabilidad por fila: archivo, número de fila y periodo.
- Transformación: nombres de columna en snake_case; recorte de espacios; vacíos a NULL; IDs como texto conservando los ceros; fechas de texto y seriales de Excel a `date`/`datetime2`; cantidades a `decimal`.
- Se descartan las filas sin factura real (vacía o de relleno, como `1`, `F` o `LEGALIZACION`).
- Las filas con valores inválidos se desvían a una tabla de rechazos con el motivo, sin detener la corrida.
- Llave de negocio `Factura + Cl Doc FWO + Cl. Expedicion`; si se repite, gana la primera aparición y las demás quedan como duplicadas.
- Carga en dos etapas: `staging` y luego MERGE (upsert) a `core` por llave de negocio, con detección de cambios por `RowHash`. Nunca se borra histórico.
- Carga incremental por archivo: solo se procesan los archivos nuevos o modificados (por hash). Hay opción de reprocesar todos.
- Cada corrida se registra en `core.ETL_Control`, con log rotativo y código de salida distinto de 0 si hay error.
- Script de ejecución para el Programador de tareas de Windows.

Fuera de alcance: dashboards o reportes sobre los datos, notificaciones por correo/Teams, y otras fuentes distintas de la carpeta ZSAC.

## Capabilities

### New Capabilities
- `zsac/extraccion`: descubrimiento de archivos ZSAC, lectura completa de la hoja `BD`, validación del encabezado, manejo de archivos bloqueados y trazabilidad de origen.
- `zsac/transformacion`: normalización de nombres, validación y conversión de tipos (fechas, números, IDs), desvío de filas inválidas a rechazos y cálculo del `RowHash`.
- `zsac/carga`: estructuras en SQL Server, carga a staging, upsert a core por llave de negocio, deduplicación dentro del lote y registro de rechazos.
- `zsac/orquestacion`: CLI, detección incremental de archivos cambiados, bitácora de corridas, logging y ejecución programada.

### Modified Capabilities
_(ninguna; el proyecto no tiene specs previas)_

## Impact

- **Código nuevo**: `src/zsac_etl/`, `sql/`, `scripts/`, `tests/`, `config/`.
- **Base de datos AnaliticaOperativa**: tablas nuevas `staging.ZSAC_Stage`, `core.HechosZSAC`, `core.ZSAC_Rechazos` y `core.ZSAC_Archivos`; se insertan filas en la tabla existente `core.ETL_Control` (`NombreProceso = 'zsac'`) sin cambiar su estructura.
- **Dependencias nuevas en el venv**: `python-calamine`, `pyyaml`, `python-dotenv`, `pytest`.
- **Sistema**: tarea programada en Windows bajo el usuario actual. La carpeta de origen solo se lee, nunca se modifica.
