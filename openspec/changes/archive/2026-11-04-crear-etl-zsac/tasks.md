# Tasks

## 1. Andamiaje y configuración

- [x] 1.1 Crear `pyproject.toml` (paquete `zsac_etl` en `src/`), `requirements.txt` (pandas, python-calamine, pyodbc, pyyaml, python-dotenv, pytest) e instalar con `env\Scripts\python -m pip install -e .`. Se verifica con `python -c "import zsac_etl"`.
- [x] 1.2 Crear `config/config.yaml` (ruta de origen, servidor/base, driver, reintentos 3×10 s, `factura_min_digitos: 4`, logging) y `config/.env.example`. Implementar `config.py` y `db.py` (adaptado de `pipeline-analitica-ontime/src/utils/db.py`). Se verifica con un test que carga la config y con un `SELECT 1` contra AnaliticaOperativa.
- [x] 1.3 Crear `config/columnas.yaml` con el mapeo de las 44 columnas (origen → snake_case, tipo, obligatoria, longitud) según D2. Se verifica con un test de que los 43 destinos son únicos y no hay choques con la intercalación CI.

## 2. Base de datos

- [x] 2.1 Escribir `sql/01_crear_tablas_zsac.sql` (idempotente, `IF NOT EXISTS`) con `staging.ZSAC_Stage`, `core.HechosZSAC` (PK sustituta + índice único sobre la llave + trazabilidad), `core.ZSAC_Rechazos` y `core.ZSAC_Archivos`. Se verifica ejecutándolo dos veces con `sqlcmd` sin errores.
- [x] 2.2 Escribir `sql/merges/merge_zsac.sql` según D6: MERGE por llave, la primera gana por periodo, actualización solo si cambia el hash del mismo archivo o llega un periodo anterior, sin borrado, salida con insertadas/actualizadas y `DUPLICADO` frente a core hacia rechazos. Se verifica en la tarea 5.3.

## 3. Extracción

- [x] 3.1 Implementar el descubrimiento de archivos (patrón `ZSAC_<MES>_<AAAA>.xlsx`, meses en español, ignorar `~$*`) y el cálculo del periodo. Se verifica con tests de nombres válidos, temporales y mes inválido.
- [x] 3.2 Implementar la copia temporal con reintentos ante `PermissionError`, el SHA-256 sobre la copia y el estado `OMITIDO`. Se verifica con un test que simula el bloqueo (mock de `shutil.copy2`).
- [x] 3.3 Implementar la lectura con calamine (hoja `BD` → `DB` → rechazo), la resolución del grupo "fecha de factura" por máximo de no vacíos, la validación del encabezado por nombre y la trazabilidad (`archivo_origen`, `fila_origen`, `periodo`). Las columnas extra se ignoran, una faltante no obligatoria queda en NULL y una faltante obligatoria rechaza el archivo. Se verifica con tests sobre xlsx mínimos generados en `tmp_path`: orden distinto, faltante opcional, faltante obligatoria, columna extra, hoja DB y fecha duplicada.

## 4. Transformación

- [x] 4.1 Implementar la limpieza de texto (strip de espacios/`\r\n`, vacío → NULL) y los IDs como texto, sin ceros a la izquierda si son solo dígitos. Se verifica con tests de `8520777971\r` → `8520777971`, `00000000002104607371` → `2104607371`, `0003009178` → `3009178` y `CNS-000123` sin cambios.
- [x] 4.2 Implementar el parseo de fechas según D4 (formatos explícitos, datetime nativo, serial de Excel). Se verifica con tests de `05.01.2026-10:00`, `20.01.2026-08:49:00`, datetime nativo, `693596-` → inválido y `31.02.2026` → inválido.
- [x] 4.3 Implementar `parsear_decimal` según D3, con `kilos_txt`/`volumen_txt`/`numero_ambiguo`, y `cajas` como entero. Se verifica con tests de `3.048,65`, `243.91`, `5.673` (kilos → 5673, volumen → 5.673), `0,000012`, `1.234.567` y `0000001500`.
- [x] 4.4 Implementar el filtro de factura real (vacía o con menos de N dígitos seguidos → descartada y contada). Se verifica con tests de `LEGALIZACION`, `1`, `PMP12345` y `9961583720`.
- [x] 4.5 Implementar la separación en válidas/rechazos (`ERROR` en obligatorias, `ADVERTENCIA` en opcionales), la deduplicación por llave dentro del lote (orden `periodo, fila_origen`, la primera gana → `DUPLICADO`) y el `row_hash` canónico (D11). Se verifica con tests de rechazo de obligatoria, advertencia de opcional, duplicado y hash estable.

## 5. Carga

- [x] 5.1 Implementar en `carga.py` la carga a staging (TRUNCATE + `fast_executemany`, adaptado de `cargar_sql.py`) y la ejecución del MERGE con lectura de conteos. Se verifica cargando un archivo real y comparando `COUNT(*)` en staging con las filas válidas.
- [x] 5.2 Implementar la transacción por archivo (D7): reemplazo de rechazos del archivo, upsert en `core.ZSAC_Archivos` y ROLLBACK ante error. Se verifica forzando una excepción después del MERGE y comprobando que core no cambió.
- [x] 5.3 Hacer un test de integración (marcado `@pytest.mark.sql`, se salta si no hay servidor) con tablas en un esquema temporal: inserción, reproceso idéntico (0/0), cambio de un valor (0/1), periodo posterior → `DUPLICADO` y periodo anterior → reemplaza. Se verifica con `pytest -m sql` en verde.

## 6. Orquestación

- [x] 6.1 Implementar el CLI (`run`, `--todos`, `--archivo`, `init-db`), la detección incremental por hash y el aislamiento de fallas por archivo, con estados OK/PARCIAL/ERROR y código de salida. Se verifica con tests del cálculo de estado y del código de salida usando mocks.
- [x] 6.2 Implementar el registro en `core.ETL_Control` (`NombreProceso='zsac'`, detalle con archivos, rechazos por tipo y descartes), el logging rotativo en `logs/` y `sp_getapplock`. Se verifica lanzando dos corridas a la vez (la segunda termina sin procesar nada) y revisando la fila en `ETL_Control`.
- [x] 6.3 Crear `scripts/ejecutar_zsac.ps1` (usa `env\Scripts\python`, corre en modo incremental y propaga `$LASTEXITCODE`). Se verifica ejecutándolo a mano y comprobando el código de salida.
- [x] 6.4 Escribir `README.md` (instalación, configuración, comandos, tablas, consultas de auditoría y cómo registrar la tarea). Se verifica siguiendo el README desde cero en una terminal nueva.

## 7. Carga inicial y verificación integral

- [x] 7.1 Ejecutar `python -m zsac_etl init-db` y `python -m zsac_etl run --todos`. Se verifica con una corrida OK en `ETL_Control` y en menos de 15 minutos.
- [x] 7.2 Cuadrar los conteos por archivo: filas leídas = cargadas + sin factura real + `ERROR` + `DUPLICADO`. Revisar una muestra de fechas (día/mes), IDs con ceros y `numero_ambiguo`. Se verifica con las consultas documentadas en el README.
- [x] 7.3 Repetir `run` (0 archivos) y `run --todos` (0 insertadas / 0 actualizadas), y probar un archivo bloqueado (`OMITIDO`; la corrida queda `PARCIAL` si se cargó otro archivo y `ERROR` si no se cargó ninguno; en la siguiente corrida se reprocesa). Se verifica con los resultados en `ETL_Control` y en el log.
- [x] 7.4 Registrar la tarea programada con `schtasks`, previa confirmación de la hora con el usuario, y ejecutarla una vez a demanda. Se verifica con `schtasks /Query /TN zsac_etl` y una fila nueva en `ETL_Control`.

## Workflow follow-up

- Revisar la implementación contra las specs (`/opsx:verify` si está habilitado, o una revisión manual).
- Archivar el cambio con `/opsx:archive crear-etl-zsac` para promover las specs a `openspec/specs/zsac/`.
