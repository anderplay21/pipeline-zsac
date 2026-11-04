# zsac/carga Specification

## Purpose

Persistir en SQL Server (AnaliticaOperativa) los registros ZSAC transformados como una tabla de hechos sin duplicados, actualizable por upsert, junto con los rechazos y el estado de cada archivo procesado.

## Requirements

### Requirement: Estructuras de base de datos
El sistema SHALL contar con estas tablas en AnaliticaOperativa:
- `staging.ZSAC_Stage`: lote en curso.
- `core.HechosZSAC`: hechos, con índice único sobre la llave de negocio.
- `core.ZSAC_Rechazos`: errores y advertencias por fila.
- `core.ZSAC_Archivos`: estado por archivo.

El script que las crea SHALL ser idempotente y MUST NOT modificar las demás tablas de la base.

#### Scenario: Ejecutar el DDL dos veces
- **WHEN** el script de creación se ejecuta sobre una base donde las tablas ya existen
- **THEN** termina sin error y sin perder datos

### Requirement: Llave de negocio
La llave de negocio de un registro ZSAC SHALL ser la combinación `factura` + `clase_doc_fwo` + `clase_expedicion`. En `core.HechosZSAC` MUST NOT existir dos registros con la misma llave.

#### Scenario: Unicidad en core
- **WHEN** termina cualquier corrida
- **THEN** no hay en `core.HechosZSAC` dos filas con la misma combinación de `factura`, `clase_doc_fwo` y `clase_expedicion`

### Requirement: La primera aparición gana
Cuando varias filas tienen la misma llave, el sistema SHALL conservar la primera aparición, ordenando por periodo del archivo (más antiguo primero) y luego por fila de origen. Esto aplica tanto dentro del lote como frente a lo que ya existe en core. Las demás filas SHALL registrarse en rechazos como tipo `DUPLICADO`, indicando el archivo y la fila que prevaleció.

#### Scenario: Duplicado dentro del mismo archivo
- **WHEN** `ZSAC_ENERO_2026.xlsx` tiene la misma llave en las filas 10 y 500
- **THEN** se carga la fila 10 y la fila 500 queda en rechazos como `DUPLICADO`

#### Scenario: Llave ya cargada desde un mes anterior
- **WHEN** `core.HechosZSAC` tiene la llave K cargada desde `ZSAC_MAYO_2026.xlsx` y `ZSAC_JULIO_2026.xlsx` trae otra vez la llave K
- **THEN** el registro de mayo no cambia y la fila de julio queda en rechazos como `DUPLICADO`

#### Scenario: Llega un mes anterior al ya cargado
- **WHEN** `core.HechosZSAC` tiene la llave K desde `ZSAC_JULIO_2026.xlsx` y se procesa por primera vez `ZSAC_MAYO_2026.xlsx` con la llave K
- **THEN** el registro pasa a tener el contenido y el origen de mayo

### Requirement: Upsert sin borrado
El sistema SHALL insertar las llaves nuevas. Si la llave existe y proviene del mismo archivo, SHALL actualizar el registro solo cuando cambie `row_hash` y registrar la fecha de la última actualización. El sistema MUST NOT borrar registros de `core.HechosZSAC`, aunque una llave deje de aparecer en un archivo reprocesado.

#### Scenario: Archivo reemplazado con correcciones
- **WHEN** `ZSAC_JULIO_2025.xlsx` se reemplaza y una fila cambia su `fecha_cumplido`
- **THEN** ese registro se actualiza en core, `fecha_ultima_actualizacion` toma la hora de la corrida y la corrida cuenta 1 actualizada

#### Scenario: Reproceso idéntico
- **WHEN** se reprocesa un archivo que no cambió
- **THEN** la corrida reporta 0 insertadas y 0 actualizadas

#### Scenario: Llave que desaparece
- **WHEN** un archivo reemplazado ya no contiene una llave que antes sí tenía
- **THEN** el registro sigue en `core.HechosZSAC` sin cambios

### Requirement: Carga transaccional por archivo
La carga de cada archivo (staging, upsert y sus rechazos) SHALL ser atómica: si falla, MUST NOT quedar en core una parte de sus filas, y el archivo SHALL quedar como no procesado para reintentarlo.

#### Scenario: Falla a mitad del upsert
- **WHEN** se pierde la conexión durante el upsert de un archivo
- **THEN** core queda como antes de ese archivo y en la siguiente corrida el archivo se vuelve a procesar

### Requirement: Registro de rechazos
Cada error, advertencia o duplicado SHALL quedar en `core.ZSAC_Rechazos` con: identificador de corrida, archivo, fila de origen, tipo (`ERROR`, `ADVERTENCIA` o `DUPLICADO`), columna, valor original y motivo. Al reprocesar un archivo, SHALL reemplazarse sus rechazos anteriores para que reflejen solo su versión vigente.

#### Scenario: Consulta de rechazos de un archivo
- **WHEN** un analista consulta `core.ZSAC_Rechazos` filtrando por `ZSAC_ENERO_2026.xlsx`
- **THEN** obtiene los rechazos de la última vez que se procesó ese archivo, cada uno con un motivo legible

### Requirement: Trazabilidad en core
Cada registro de `core.HechosZSAC` SHALL incluir el archivo de origen, la fila de origen, el periodo, la fecha en que se insertó y la fecha de su última actualización.

#### Scenario: Auditoría de un registro
- **WHEN** se consulta un registro de core
- **THEN** se puede identificar de qué archivo y de qué fila de Excel proviene
