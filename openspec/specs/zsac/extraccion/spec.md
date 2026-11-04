# zsac/extraccion Specification

## Purpose

Obtener de la carpeta de origen los reportes mensuales ZSAC en Excel, completos y estructuralmente válidos, con trazabilidad de cada fila hasta su archivo y su posición.

## Requirements

### Requirement: Descubrimiento de archivos ZSAC
El sistema SHALL considerar como fuente solo los archivos de la carpeta de origen configurada cuyo nombre cumpla el patrón `ZSAC_<MES>_<AAAA>.xlsx`, donde `<MES>` es el nombre de un mes en español en mayúsculas y `<AAAA>` un año de cuatro dígitos. Los demás archivos SHALL ignorarse y registrarse en el log.

#### Scenario: Archivo con nombre válido
- **WHEN** la carpeta contiene `ZSAC_ENERO_2026.xlsx`
- **THEN** el archivo se incluye como fuente con periodo 2026-01

#### Scenario: Archivo temporal o con otro nombre
- **WHEN** la carpeta contiene `~$ZSAC_ENERO_2026.xlsx` o `notas.xlsx`
- **THEN** el archivo se ignora y queda una línea informativa en el log

#### Scenario: Mes no reconocido
- **WHEN** la carpeta contiene `ZSAC_ENERITO_2026.xlsx`
- **THEN** el archivo se omite y se registra una advertencia indicando que el mes no es válido

### Requirement: Lectura completa de la hoja BD
El sistema SHALL leer la hoja `BD` de cada archivo o, si no existe, la hoja `DB`. SHALL incluir todas sus filas de datos, también las ocultas por filtros, y conservar los valores de texto exactamente como vienen, sin perder ceros a la izquierda.

#### Scenario: Hoja llamada DB
- **WHEN** un archivo no tiene hoja `BD` pero sí una hoja `DB`
- **THEN** se lee la hoja `DB` y se registra en el log qué hoja se usó

#### Scenario: Filas ocultas por autofiltro
- **WHEN** la hoja `BD` tiene 49.179 filas de datos y un filtro activo que oculta parte de ellas
- **THEN** se extraen las 49.179 filas

#### Scenario: Identificador con ceros a la izquierda
- **WHEN** una celda de `Remision` contiene el texto `00000000002104607371`
- **THEN** el valor extraído es exactamente `00000000002104607371`

#### Scenario: Ni BD ni DB
- **WHEN** un archivo no contiene una hoja llamada `BD` ni `DB`
- **THEN** el archivo se rechaza completo con el motivo "hoja BD/DB no encontrada" y la corrida continúa con los demás archivos

### Requirement: Validación del encabezado
El sistema SHALL ubicar cada columna esperada del reporte ZSAC por su nombre, sin importar el orden, las mayúsculas ni los espacios en los extremos. Las columnas desconocidas SHALL ignorarse. Si falta una columna no obligatoria, SHALL cargarse en NULL. En ambos casos queda una advertencia en el log. Solo si falta una columna obligatoria (`Factura`, `Cl Doc FWO`, `Cl. Expedicion` o `Fecha de Factura`) el archivo SHALL rechazarse completo, indicando cuál falta.

#### Scenario: Encabezado correcto en otro orden
- **WHEN** el encabezado contiene todas las columnas esperadas, pero en un orden distinto al habitual
- **THEN** el archivo pasa a transformación con cada columna correctamente identificada

#### Scenario: Falta una columna no obligatoria
- **WHEN** al encabezado le falta la columna `Kilos`
- **THEN** el archivo se carga con `kilos` en NULL en todas sus filas y queda una advertencia en el log que menciona `Kilos`

#### Scenario: Falta una columna obligatoria
- **WHEN** al encabezado le falta la columna `Cl Doc FWO`
- **THEN** el archivo se rechaza con un motivo que menciona `Cl Doc FWO` como faltante y no se carga ninguna de sus filas

#### Scenario: Columna adicional
- **WHEN** el encabezado trae una columna extra `Observaciones`
- **THEN** el archivo no se rechaza, los datos de `Observaciones` se omiten y queda una advertencia en el log

### Requirement: Columna Fecha de Factura duplicada
El reporte trae dos columnas que solo difieren en mayúsculas (`Fecha de factura` y `Fecha de Factura`) y a veces una copia adicional; una de ellas siempre está vacía. El sistema SHALL tomar como fecha de factura la columna de ese grupo que tenga más valores no vacíos y descartar las demás, sin que esto cuente como columna desconocida.

#### Scenario: Una vacía y otra con fechas
- **WHEN** `Fecha de factura` está vacía en todas las filas y `Fecha de Factura` tiene fechas
- **THEN** la fecha de factura se toma de `Fecha de Factura`

#### Scenario: Copia duplicada con fechas
- **WHEN** el archivo trae `Fecha de factura` vacía y dos columnas `Fecha de Factura` con fechas
- **THEN** se usa la que tenga más valores no vacíos, se descartan las otras y queda una advertencia en el log

### Requirement: Archivos bloqueados
El sistema SHALL tolerar archivos que estén abiertos o bloqueados por otro proceso (por ejemplo, Excel o la sincronización de OneDrive). Si tras los reintentos configurados el archivo sigue sin poder leerse, SHALL omitirse con una advertencia, sin abortar la corrida y sin marcarse como procesado.

#### Scenario: Archivo abierto en Excel
- **WHEN** `ZSAC_SEPTIEMBRE_2026.xlsx` está bloqueado durante toda la corrida
- **THEN** la corrida procesa los demás archivos, registra una advertencia para ese archivo y en la siguiente corrida vuelve a intentarlo

### Requirement: Trazabilidad de origen
Cada fila extraída SHALL llevar el nombre del archivo de origen, su número de fila en Excel (contando el encabezado como fila 1) y el periodo (año y mes) derivado del nombre del archivo.

#### Scenario: Fila trazable
- **WHEN** se extrae la fila 2 de `ZSAC_ENERO_2026.xlsx`
- **THEN** la fila lleva archivo de origen `ZSAC_ENERO_2026.xlsx`, fila de origen 2 y periodo 2026-01

### Requirement: Solo lectura sobre la carpeta de origen
El sistema MUST NOT modificar, mover, renombrar ni borrar archivos de la carpeta de origen.

#### Scenario: Después de una corrida
- **WHEN** termina una corrida, exitosa o con errores
- **THEN** los archivos de la carpeta de origen conservan su contenido y su fecha de modificación
