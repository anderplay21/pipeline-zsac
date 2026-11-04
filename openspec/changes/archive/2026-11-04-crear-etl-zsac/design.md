# Design

## Context

Motivación: ver `proposal.md`. Requisitos: ver `specs/zsac/*`.

Hallazgos del perfilado de los 21 archivos actuales (~1,28 M filas):
- Lectura con `python-calamine`: unos 6 s por archivo, y se incluyen las filas ocultas por el autofiltro.
- Los tipos que entrega el lector varían según la columna:
  - Fecha de Factura, Compromiso, Operación y LeadTime llegan como `datetime`, con algún texto basura como `693596-`.
  - Las demás fechas llegan como texto `dd.MM.yyyy-HH:mm` y, en el caso de la cita, también con `:ss`.
  - `Vlr. declarado` llega como `int`. Todo lo demás llega como `str`.
- `Kilos` y `Volumen` mezclan formatos dentro del mismo archivo e incluso dentro del mismo cliente: `3.048,65`, `243.91`, `5.673`.
- Hay columnas duplicadas por mayúsculas (`Fecha de factura`, siempre vacía, y `Fecha de Factura`) y a veces una tercera copia (pandas la renombra `Fecha de Factura.1`). Una hoja se llamaba `DB` y el orden de las columnas varía.
- Llave `factura + clase_doc_fwo + clase_expedicion`:
  - 487 filas sin factura y unas 30 mil con factura de relleno (sin 4 dígitos seguidos).
  - Entre las facturas reales, cerca del 6 % de las filas repiten la llave. Casi siempre es la misma factura con otra Remision, y se descartan como `DUPLICADO` (decisión de negocio).
- En AnaliticaOperativa (`Modern_Spanish_CI_AS`) ya existen los esquemas `staging`/`core` y `core.ETL_Control`, que comparte el proyecto `pipeline-analitica-ontime`. Sus patrones se reutilizan.

## Goals / Non-Goals

**Goals:**
- Corrida incremental típica (1–2 archivos) en menos de 2 minutos; carga completa (21 archivos) en menos de 15 minutos.
- Resultado determinista: el mismo conjunto de archivos produce el mismo contenido en core, sin importar el orden en que se procesen.
- Que cada decisión de limpieza (descartes, ambiguos, duplicados) se pueda auditar desde SQL.

**Non-Goals:**
- Historial de versiones de cada registro (SCD2). Solo se guarda el estado vigente.
- Procesamiento en paralelo de archivos.
- Compartir código con `pipeline-analitica-ontime`. Los patrones se copian y adaptan; no se crea una dependencia.

## Decisions

### D1. Lector: python-calamine vía pandas
`pd.read_excel(engine="calamine", dtype=object)`, sin forzar `str`, para recibir `datetime`/`int` nativos donde Excel los tiene.
- *Alternativa*: openpyxl. Es entre 10 y 20 veces más lento con XML de unos 90 MB y no aporta nada, porque no se necesitan estilos.
- *Alternativa*: `dtype=str`. Haría perder el tipo nativo de las fechas y obligaría a parsear `2026-01-07 00:00:00`.

### D2. Mapeo de columnas declarativo
`config/columnas.yaml` define, por cada columna de origen: nombre destino, tipo (`texto`, `id`, `fecha`, `fecha_hora`, `entero`, `decimal`), si es obligatoria y su longitud. El emparejamiento se hace por nombre normalizado: casefold, sin espacios en los extremos y sin el sufijo `.N` que agrega pandas.

Las columnas que caen en el grupo "fecha de factura" se resuelven con la regla del máximo de valores no vacíos *antes* de validar el encabezado. Así, la columna sobrante no cuenta como desconocida. Las columnas desconocidas se descartan con advertencia. Una faltante no obligatoria se crea en NULL con advertencia, y solo una faltante obligatoria rechaza el archivo.

El tipo `id` aplica `lstrip("0")` cuando el valor es solo dígitos (`"000"` → `"0"`) y deja intactos los alfanuméricos. Teléfono y placa son `texto`. El filtro de factura real (D-filtro, ≥N dígitos seguidos) se evalúa sobre la factura ya limpia.

### D3. Parseo de números
Una sola función, `parsear_decimal(texto, columna)`, con reglas en este orden:
1. Valor numérico nativo → se usa tal cual.
2. Contiene `,` → se quitan los `.` y la `,` pasa a `.`.
3. Varios `.` → se quitan todos (miles).
4. Un solo `.` con 1–2 dígitos después → decimal.
5. Un solo `.` con exactamente 3 dígitos después → ambiguo. Es miles si la columna es `kilos` y decimal si es `volumen`. Se marca `numero_ambiguo = 1`.
6. Cualquier otro caso → `Decimal(texto)`. Si falla, es inválido.

Los tipos SQL serán `decimal(18,3)` para kilos, `decimal(18,6)` para volumen y `decimal(18,2)` para el valor declarado. Siempre se guarda `kilos_txt`/`volumen_txt`.
- *Alternativa descartada*: inferir el formato por cliente. El perfilado mostró que un mismo cliente mezcla formatos.

### D4. Parseo de fechas
Se usan formatos explícitos (`%d.%m.%Y-%H:%M`, `%d.%m.%Y-%H:%M:%S`, `%d.%m.%Y`) con `pd.to_datetime(format=..., errors="coerce")`, probando cada formato sobre los valores que siguen sin parsear. Nunca se usa `dayfirst` ni la inferencia automática, para no invertir día y mes. Los `datetime` nativos pasan directo. Un entero entre 20000 y 80000 se interpreta como serial de Excel (origen 1899-12-30).

### D5. Rechazos sin perder la fila válida
La transformación devuelve `(df_validas, df_rechazos)`. Las advertencias no sacan la fila del lote, pero generan un registro en rechazos. Los errores sí la sacan.

### D6. Estrategia de upsert con "la primera aparición gana"
1. En Python: se ordena el lote por `(periodo, fila_origen)`, se aplica `drop_duplicates(llave, keep="first")` y los descartados van a rechazos como `DUPLICADO`.
2. Se carga `staging.ZSAC_Stage` (TRUNCATE + `fast_executemany`, patrón de `cargar_sql.py` del proyecto ontime).
3. Un `MERGE core.HechosZSAC` por llave:
   - `WHEN MATCHED AND (origen.archivo_origen = destino.archivo_origen AND origen.row_hash <> destino.row_hash) OR (origen.periodo < destino.periodo)` → `UPDATE`.
   - `WHEN NOT MATCHED BY TARGET` → `INSERT`.
   - No hay rama `NOT MATCHED BY SOURCE`.
4. Las filas de staging cuya llave ya existe en core desde *otro* archivo con periodo menor o igual se insertan en rechazos como `DUPLICADO`, en el mismo lote SQL.

Así el resultado es determinista sin importar el orden en que se procesen los archivos.
- *Alternativa*: DELETE + INSERT por archivo. Es más simple, pero choca con "la primera gana" entre archivos y con la regla de no borrar.

### D7. Unidad de trabajo = archivo
Cada archivo se procesa en su propia transacción (`autocommit=False`): staging → MERGE → reemplazo de sus rechazos (`DELETE ... WHERE archivo = ?` + INSERT) → upsert de `core.ZSAC_Archivos` con hash y conteos → `COMMIT`. Si algo falla, se hace `ROLLBACK`, el error se registra y se sigue con el siguiente archivo. Al final de la corrida se escribe una sola fila en `core.ETL_Control`.

### D8. Archivos bloqueados
Se copia el archivo a `%TEMP%\zsac_etl\` con `shutil.copy2` y se lee la copia. La copia también se usa para calcular el SHA-256, de modo que la huella corresponde exactamente a lo que se leyó. Ante `PermissionError` se reintenta N veces con una espera de S segundos (configurable; 3 × 10 s por defecto). Si sigue bloqueado, el archivo queda `OMITIDO`.

### D9. Bloqueo de corrida concurrente
`sp_getapplock` con `@LockOwner='Session'` y el recurso `zsac_etl`, tomado al inicio con `@LockTimeout=0`. No deja archivos de bloqueo huérfanos si el proceso muere.

### D10. Estructura del código
```
src/zsac_etl/
  __main__.py      # python -m zsac_etl
  cli.py           # argparse: run [--todos|--archivo X], init-db
  config.py        # carga config.yaml/.env y columnas.yaml
  db.py            # conexión pyodbc (adaptado de pipeline-analitica-ontime/src/utils/db.py)
  extraccion.py    # descubrir, copiar, hash, leer hoja, resolver encabezado
  transformacion.py# limpieza, parseo fechas/números, filtro factura, rechazos, row_hash
  carga.py         # staging, MERGE, rechazos, ZSAC_Archivos (adaptado de cargar_sql.py)
  control.py       # ETL_Control, applock, logging
  pipeline.py      # orquestación de la corrida: selección incremental, aislamiento por archivo, estado
sql/01_crear_tablas_zsac.sql, sql/merges/merge_zsac.sql
scripts/ejecutar_zsac.ps1
```
Se ejecuta con `env\Scripts\python -m zsac_etl` y `PYTHONPATH=src`, o con `pip install -e .`, usando un `pyproject.toml` mínimo.

### D11. Huella `row_hash`
SHA-256 de los valores de negocio, serializados de forma canónica (ISO para las fechas, `str(Decimal)` normalizado, `""` para NULL, separador `\x1f`), guardado como `binary(32)`. No incluye columnas de trazabilidad.

## Risks / Trade-offs

- [La heurística de "d.ddd" interpreta mal un valor real] → Se guarda el texto original y `numero_ambiguo`, y se puede recalcular con SQL si el negocio define otra regla.
- [Descartar como `DUPLICADO` facturas reales despachadas en varias remesas (~6 %)] → Es una decisión explícita del usuario. Quedan todas en `core.ZSAC_Rechazos` y, si se cambia la llave en un cambio futuro, se recuperan reprocesando con `--todos`.
- [La regla de relleno (≥4 dígitos) deja pasar valores raros o descarta facturas válidas cortas] → El mínimo es configurable y el conteo queda en la bitácora.
- [Cambios de encabezado por edición manual de los archivos] → El archivo se rechaza completo con el detalle de las columnas, y se pueden agregar alias en `columnas.yaml` sin tocar el código.
- [OneDrive con "archivos a petición" (solo en la nube)] → La copia fuerza la descarga. Si falla, el archivo queda `OMITIDO` con advertencia.
- [`core.ETL_Control` es compartida con el pipeline ontime] → Solo se insertan filas con `NombreProceso='zsac'`; no se cambia su estructura.
- [Una carga completa infla el log de transacciones] → La transacción es por archivo (unas 60 mil filas) y la base está en el modelo de recuperación que ya tenga configurado.

## Migration Plan

1. `python -m zsac_etl init-db` crea las tablas (idempotente).
2. `python -m zsac_etl run --todos` hace la carga histórica inicial.
3. Verificación con las consultas de la tarea 8.
4. Registro de la tarea programada (`schtasks`) con el usuario actual.

Rollback: deshabilitar la tarea y `DROP` de las 4 tablas nuevas. `core.ETL_Control` solo tiene filas `zsac`, que se pueden borrar sin afectar al pipeline ontime.

## Open Questions

- Resuelta: la tarea programada corre de lunes a viernes a las 10:00 (confirmado por el usuario).
