# pipeline-zsac

Pipeline ETL que consolida los reportes mensuales **ZSAC** (Excel exportados de SAP) y los carga por upsert a SQL Server (`AnaliticaOperativa`).

- **Origen**: `C:\Users\aguzman\OneDrive - Industria Colombiana de Logistica y Transporte\Documentos\ZSAC CONSOLIDADO`. Archivos `ZSAC_<MES>_<AAAA>.xlsx`, hoja `BD` (o `DB`).
- **Destino**: `core.HechosZSAC`, con llave de negocio única `factura + clase_doc_fwo + clase_expedicion`.
- **Especificación**: las reglas de negocio están en `openspec/specs/zsac/` (`extraccion`, `transformacion`, `carga`, `orquestacion`), y este README cubre la operación. El cambio que las creó está archivado en `openspec/changes/archive/2026-11-04-crear-etl-zsac/`. Para modificar una regla se abre un cambio nuevo con `/opsx:propose`.

## Instalación

```powershell
cd C:\Users\aguzman\pipeline-zsac
python -m venv env                       # si el entorno virtual no existe
env\Scripts\python -m pip install -e ".[dev]"
```

Requiere el **ODBC Driver 18 for SQL Server** y acceso con autenticación de Windows a `ICMEMEOPERACI02`.

## Configuración

| Archivo | Contenido |
|---|---|
| `config/config.yaml` | Carpeta de origen, hojas, reintentos ante archivos bloqueados, conexión SQL, nombres de tablas, `factura_min_digitos`, logging |
| `config/columnas.yaml` | Mapeo de las columnas del Excel a columnas SQL: tipo, si es obligatoria, longitud, escala y alias |
| `config/.env` | Solo si se usa autenticación SQL (`SQL_USUARIO`, `SQL_PASSWORD`). No se versiona; ver `.env.example` |

## Comandos

```powershell
env\Scripts\python -m zsac_etl init-db                          # crea/verifica tablas (idempotente)
env\Scripts\python -m zsac_etl run                              # incremental: solo archivos nuevos o modificados
env\Scripts\python -m zsac_etl run --todos                      # reprocesa todo
env\Scripts\python -m zsac_etl run --archivo ZSAC_MAYO_2026.xlsx
```

**Modo incremental** (`run` sin opciones, el que usa la tarea programada):
- Se calcula el SHA-256 de cada Excel y se compara con el guardado en `core.ZSAC_Archivos` en su última carga OK.
- Los archivos sin cambios se saltan; solo se procesan los nuevos, los modificados y los que fallaron o quedaron omitidos en la corrida anterior.
- Dentro de un archivo modificado, solo se actualizan las filas cuyo `row_hash` cambió y se insertan las nuevas.
- Si no hay cambios, la corrida tarda unos segundos. Un `--todos` completo tarda unos 9 minutos.

Códigos de salida: `0` OK · `1` PARCIAL (algún archivo falló u omitido) · `2` ERROR · `3` ya había otra corrida en curso.

Los logs están en `logs/zsac_etl.log`, con rotación diaria y 30 días de retención.

## Qué hace con los datos

1. **Lectura**: incluye las filas ocultas por el filtro. Las columnas se ubican por nombre y en cualquier orden.
   - Una columna desconocida se ignora.
   - Si falta una columna opcional, se carga en NULL.
   - Si falta una obligatoria (`Factura`, `Cl Doc FWO`, `Cl. Expedicion`, `Fecha de Factura`), se rechaza el archivo.
   - De las columnas "Fecha de Factura" repetidas se usa la que tiene datos.
2. **Limpieza**:
   - Texto sin espacios ni `\r` en los extremos.
   - IDs numéricos sin ceros a la izquierda.
   - Fechas `dd.MM.yyyy-HH:mm` (siempre con el día primero). `00.00.0000` y `693596-` cuentan como fecha vacía.
   - Kilos y Volumen con separadores mixtos. Los casos ambiguos (`5.673`) quedan con `numero_ambiguo = 1` y el texto original en `kilos_txt` / `volumen_txt`.
3. **Descartes**: las filas sin factura real (vacía o sin 4 dígitos seguidos, como `1`, `F` o `LEGALIZACION`) no se cargan. Su cantidad queda en la bitácora.
4. **Rechazos** (`core.ZSAC_Rechazos`):
   - `ERROR`: valor obligatorio inválido; la fila no se carga.
   - `ADVERTENCIA`: valor opcional inválido; se carga en NULL.
   - `DUPLICADO`: llave repetida. Gana la primera aparición, ordenando por periodo del archivo y luego por fila.
5. **Upsert**: inserta las llaves nuevas y actualiza solo si cambió `row_hash` en el mismo archivo. Un archivo de periodo anterior reemplaza al posterior. Nunca borra.

## Tablas

| Tabla | Uso |
|---|---|
| `staging.ZSAC_Stage` | Lote del archivo en curso (se trunca en cada carga) |
| `core.HechosZSAC` | Hechos, con trazabilidad (`archivo_origen`, `fila_origen`, `periodo`, `fecha_carga`, `fecha_ultima_actualizacion`) |
| `core.ZSAC_Rechazos` | Errores, advertencias y duplicados por fila. Se reemplazan al reprocesar el archivo |
| `core.ZSAC_Archivos` | Estado por archivo: hash, conteos, último intento y última carga OK |
| `core.ETL_Control` | Bitácora compartida; filas con `NombreProceso = 'zsac'` |

## Consultas de auditoría

```sql
-- Estado y cuadre por archivo: leidas = validas + sin_factura + error + duplicadas
SELECT archivo, estado, filas_leidas, filas_validas, filas_sin_factura, filas_error, filas_duplicadas,
       filas_leidas - (filas_validas + filas_sin_factura + filas_error + filas_duplicadas) AS diferencia,
       filas_insertadas, filas_actualizadas, ultima_carga_ok
FROM core.ZSAC_Archivos ORDER BY periodo;

-- Filas en core por archivo de origen
SELECT archivo_origen, COUNT(*) AS filas FROM core.HechosZSAC GROUP BY archivo_origen ORDER BY archivo_origen;

-- Motivos de rechazo más frecuentes
SELECT tipo, columna, motivo, COUNT(*) AS n
FROM core.ZSAC_Rechazos GROUP BY tipo, columna, motivo ORDER BY n DESC;

-- Valores numéricos ambiguos para revisar
SELECT TOP 100 cliente, cajas, kilos_txt, kilos, volumen_txt, volumen
FROM core.HechosZSAC WHERE numero_ambiguo = 1;

-- Últimas corridas
SELECT TOP 10 * FROM core.ETL_Control WHERE NombreProceso = 'zsac' ORDER BY ProcesoID DESC;
```

## Ejecución programada

`scripts/ejecutar_zsac.ps1` ejecuta una corrida incremental y devuelve su código de salida. Con `-Todos` reprocesa todos los archivos, pero eso es solo para uso manual: la tarea no lo usa.

La tarea `zsac_etl` está registrada de lunes a viernes a las 10:00 con el usuario actual. Para que corra, el equipo debe estar encendido, con la sesión del usuario iniciada y conectado a la corriente, porque con la configuración por defecto de `schtasks` no inicia con batería. Si a esa hora no se cumplen estas condiciones, la corrida se pierde y los cambios se cargan en la siguiente.

Para registrarla o volver a crearla:

```powershell
schtasks /Create /TN zsac_etl /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 10:00 /F `
  /TR 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\aguzman\pipeline-zsac\scripts\ejecutar_zsac.ps1"'
schtasks /Run /TN zsac_etl          # ejecutar ahora
schtasks /Query /TN zsac_etl /V /FO LIST
```

Si se lanza una corrida mientras otra está en curso, la segunda termina de inmediato con código `3` (bloqueo `sp_getapplock`).

## Pruebas

```powershell
env\Scripts\python -m pytest            # todas (las de SQL usan el esquema temporal zsac_test)
env\Scripts\python -m pytest -m "not sql"
```

## Rollback

Deshabilitar la tarea (`schtasks /Change /TN zsac_etl /DISABLE`), eliminar las cuatro tablas `ZSAC` y, si hace falta, borrar de `core.ETL_Control` las filas con `NombreProceso = 'zsac'`.
