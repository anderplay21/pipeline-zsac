# Spec Delta

## Purpose

Convertir las filas crudas del reporte ZSAC en registros con tipos y formatos uniformes, filtrar lo que no es una factura real y separar los valores inválidos con un motivo auditable.

## ADDED Requirements

### Requirement: Nombres de columna normalizados
El sistema SHALL entregar cada columna de negocio con un nombre en snake_case, sin tildes ni signos, según un mapeo fijo y documentado del encabezado ZSAC. Por ejemplo, `Ciudad Org.` → `ciudad_origen`, `Cl Doc FWO` → `clase_doc_fwo`, `Cl. Expedicion` → `clase_expedicion`, `Fecha de Factura` → `fecha_factura` y `Vlr. declarado` → `valor_declarado`.

#### Scenario: Mapeo de encabezado
- **WHEN** se transforma un archivo con la columna `Fecha de Entrega Pla`
- **THEN** el valor aparece en la columna `fecha_entrega_planeada`

### Requirement: Limpieza de texto
En todas las columnas de texto, el sistema SHALL quitar los espacios y los saltos de línea (`\r`, `\n`) de los extremos y convertir a NULL las cadenas que queden vacías.

#### Scenario: Retorno de carro al final
- **WHEN** `Orden de Pedido` contiene `8520777971\r`
- **THEN** `orden_pedido` es `8520777971`

#### Scenario: Celda solo con espacios
- **WHEN** `Placa` contiene `"   "`
- **THEN** `placa` es NULL

### Requirement: Identificadores como texto
El sistema SHALL guardar los identificadores como texto. Son identificadores Remision, Manifiesto, Factura, Carta Porte, las órdenes y los BP. Si un identificador tiene solo dígitos, SHALL quitarle los ceros a la izquierda (un valor de solo ceros queda en `0`). Si tiene letras o signos, SHALL conservarse sin cambios. Teléfono y placa no son identificadores: se tratan como texto normal.

#### Scenario: Remision con ceros
- **WHEN** `Remision` es `00000000002104607371`
- **THEN** `remision` es el texto `2104607371`

#### Scenario: BP con ceros
- **WHEN** `BP Centro Org` es `0003009178`
- **THEN** `bp_centro_origen` es el texto `3009178`

#### Scenario: Identificador alfanumérico
- **WHEN** `Factura` es `CNS-000123`
- **THEN** `factura` es `CNS-000123`

### Requirement: Conversión de fechas
El sistema SHALL convertir las fechas de texto `dd.MM.yyyy-HH:mm` y `dd.MM.yyyy-HH:mm:ss`, interpretando siempre el primer número como el día. También SHALL convertir los valores que Excel entrega como fecha o como serial. Fecha de factura, compromiso, operación y lead time se guardan solo con la fecha; entrega, entrega planeada, cumplido y cita, con fecha y hora.

#### Scenario: Texto con hora
- **WHEN** `Fecha Entrega` es `05.01.2026-10:00`
- **THEN** `fecha_entrega` es 2026-01-05 10:00:00 (5 de enero, no 1 de mayo)

#### Scenario: Texto con segundos
- **WHEN** `Fecha de cita de com` es `20.01.2026-08:49:00`
- **THEN** `fecha_cita` es 2026-01-20 08:49:00

#### Scenario: Fecha vacía de SAP
- **WHEN** `Fecha de Entrega Pla` es `00.00.0000-00:00` o `Fecha de Operación` es `693596-`
- **THEN** la fecha queda en NULL, no se registra advertencia y la cantidad de estas fechas vacías por archivo queda en el log

#### Scenario: Fecha nativa de Excel
- **WHEN** `Fecha de Factura` es una fecha de Excel con valor 07/01/2026
- **THEN** `fecha_factura` es 2026-01-07

### Requirement: Conversión de cantidades
El sistema SHALL convertir `Cajas` a entero y `Vlr. declarado`, `Kilos` y `Volumen` a decimal, aceptando separadores mixtos. Si hay coma, el punto es de miles y la coma es decimal. Con un solo punto seguido de 1 o 2 dígitos, el punto es decimal. Con varios puntos, son separadores de miles.

#### Scenario: Formato colombiano
- **WHEN** `Kilos` es `3.048,65`
- **THEN** `kilos` es 3048.65

#### Scenario: Punto decimal
- **WHEN** `Kilos` es `243.91`
- **THEN** `kilos` es 243.91

#### Scenario: Cajas con ceros
- **WHEN** `Cajas` es `0000001500`
- **THEN** `cajas` es 1500

### Requirement: Valores numéricos ambiguos
Un número con un solo punto seguido de exactamente 3 dígitos y sin coma (por ejemplo `5.673`) es ambiguo. El sistema SHALL interpretarlo como separador de miles en `Kilos` y como punto decimal en `Volumen`. En ambos casos SHALL conservar el texto original (`kilos_txt`, `volumen_txt`) y marcar la fila con `numero_ambiguo = 1`.

#### Scenario: Kilos ambiguo
- **WHEN** `Kilos` es `5.673`
- **THEN** `kilos` es 5673, `kilos_txt` es `5.673` y `numero_ambiguo` es 1

#### Scenario: Volumen ambiguo
- **WHEN** `Volumen` es `68.864`
- **THEN** `volumen` es 68.864, `volumen_txt` es `68.864` y `numero_ambiguo` es 1

### Requirement: Exclusión de filas sin factura real
El sistema SHALL descartar las filas cuya factura esté vacía o sea un valor de relleno, evaluado después de limpiar la factura. Un valor de relleno es aquel que no contiene al menos 4 dígitos consecutivos, por ejemplo `1`, `F`, `LEGALIZACION` o `VACIO CALI`. El número mínimo de dígitos SHALL ser configurable. Las filas descartadas no van a la tabla de rechazos, pero su cantidad SHALL quedar en el log y en la bitácora de la corrida.

#### Scenario: Factura de relleno
- **WHEN** `Factura` es `LEGALIZACION`
- **THEN** la fila no se carga ni se registra como rechazo, y suma al contador de "sin factura real"

#### Scenario: Factura real
- **WHEN** `Factura` es `PMP12345` o `9961583720`
- **THEN** la fila continúa en el proceso

### Requirement: Rechazo por valores inválidos
Si una columna obligatoria (`factura`, `clase_doc_fwo`, `clase_expedicion` o `fecha_factura`) queda vacía o no se puede convertir, el sistema SHALL enviar la fila a rechazos. Si una columna no obligatoria no se puede convertir, SHALL cargarla como NULL y registrar una advertencia. Cada registro indica archivo, fila, columna, valor original y motivo.

#### Scenario: Fecha de factura ilegible
- **WHEN** `Fecha de Factura` es `31.02.2026`
- **THEN** la fila no se carga y en rechazos queda un registro tipo `ERROR` con columna `fecha_factura`, valor `31.02.2026` y motivo "fecha inválida"

#### Scenario: Fecha opcional con basura
- **WHEN** `Fec. Compromiso` es `14/25/2026`
- **THEN** la fila se carga con `fecha_compromiso` NULL y en rechazos queda un registro tipo `ADVERTENCIA`

### Requirement: Huella de contenido
Para cada fila, el sistema SHALL calcular una huella determinista (`row_hash`) sobre las columnas de negocio ya transformadas. Con el mismo contenido, la huella SHALL ser siempre la misma, sin importar el archivo, la fila de origen ni la fecha de la corrida.

#### Scenario: Reproceso sin cambios
- **WHEN** el mismo archivo se transforma dos veces sin cambios
- **THEN** cada fila obtiene el mismo `row_hash` en ambas ejecuciones
