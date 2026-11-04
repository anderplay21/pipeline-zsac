# zsac/orquestacion Specification

## Purpose

Ejecutar el pipeline ZSAC de punta a punta, manualmente o de forma programada, procesando solo lo que cambió y dejando bitácora y logs suficientes para operar sin supervisión.

## Requirements

### Requirement: Interfaz de línea de comandos
El sistema SHALL ofrecer un comando de ejecución con tres modos:
- Por defecto: procesa los archivos nuevos o modificados.
- `--todos`: reprocesa todos los archivos.
- `--archivo <nombre>`: procesa solo ese archivo.

Además, SHALL ofrecer una opción para crear o actualizar las estructuras de base de datos.

#### Scenario: Ejecución por defecto
- **WHEN** el usuario ejecuta el comando sin opciones
- **THEN** solo se procesan los archivos nuevos o modificados desde la última carga exitosa

#### Scenario: Un solo archivo
- **WHEN** el usuario ejecuta el comando con `--archivo ZSAC_MAYO_2026.xlsx`
- **THEN** solo se procesa ese archivo, aunque no haya cambiado

### Requirement: Detección incremental de cambios
El sistema SHALL detectar si un archivo cambió comparando la huella SHA-256 de su contenido con la de su última carga exitosa registrada en `core.ZSAC_Archivos`. Solo SHALL registrar la nueva huella cuando el archivo se cargue con éxito.

#### Scenario: Sin cambios
- **WHEN** se ejecuta dos veces seguidas sin que cambie ningún archivo
- **THEN** la segunda corrida procesa 0 archivos y termina con estado OK

#### Scenario: Archivo reemplazado
- **WHEN** `ZSAC_JULIO_2025.xlsx` se reemplaza por una versión con contenido distinto
- **THEN** la siguiente corrida procesa solo ese archivo

#### Scenario: Solo cambió la fecha de modificación
- **WHEN** OneDrive vuelve a sincronizar un archivo sin cambiar su contenido
- **THEN** el archivo no se reprocesa

### Requirement: Aislamiento de fallas por archivo
Una falla al leer, transformar o cargar un archivo MUST NOT impedir que se procesen los demás. La corrida SHALL terminar con estado `PARCIAL` si al menos un archivo falló y otro se cargó, y `ERROR` si no se pudo cargar ninguno de los que había que procesar.

#### Scenario: Un archivo corrupto
- **WHEN** hay 3 archivos por procesar y uno está corrupto
- **THEN** se cargan los otros dos y la corrida queda en estado `PARCIAL` con el detalle del archivo fallido

### Requirement: Bitácora de corridas
Cada corrida SHALL registrar una fila en `core.ETL_Control` con `NombreProceso = 'zsac'`, la hora de ejecución, las filas cargadas a staging, las insertadas y las actualizadas, el estado (`OK`, `PARCIAL` o `ERROR`) y un detalle. El detalle incluye los archivos procesados y omitidos, los conteos de rechazos por tipo, las filas sin factura real y, si hubo error, la traza.

#### Scenario: Corrida exitosa
- **WHEN** una corrida carga 2 archivos sin errores
- **THEN** `core.ETL_Control` tiene una nueva fila con `NombreProceso = 'zsac'`, estado `OK` y los conteos correspondientes

### Requirement: Logging
El sistema SHALL escribir un log en archivo, con rotación diaria y retención configurable, además de en la consola. Para cada archivo SHALL registrar: inicio, hoja usada, filas leídas, filas descartadas, rechazos, insertadas, actualizadas y duración.

#### Scenario: Revisión posterior
- **WHEN** una corrida programada falla de madrugada
- **THEN** el log de ese día permite identificar qué archivo y qué paso fallaron

### Requirement: Código de salida
El proceso SHALL terminar con código 0 si la corrida queda en estado `OK`, y con un código distinto de 0 si queda en `PARCIAL` o `ERROR`. Así el Programador de tareas puede detectar las fallas.

#### Scenario: Falla de conexión
- **WHEN** SQL Server no está disponible al iniciar
- **THEN** el proceso escribe el error en el log y termina con un código distinto de 0

### Requirement: Ejecución programada
El proyecto SHALL incluir un script de ejecución para el Programador de tareas de Windows. El script activa el entorno del proyecto, ejecuta el pipeline en modo incremental y propaga el código de salida. Dos corridas MUST NOT ejecutarse al mismo tiempo.

#### Scenario: Corrida programada diaria
- **WHEN** la tarea programada se dispara a la hora configurada
- **THEN** se ejecuta una corrida incremental y su resultado queda en el log y en `core.ETL_Control`

#### Scenario: Corrida solapada
- **WHEN** se inicia una corrida mientras otra sigue en curso
- **THEN** la segunda termina de inmediato sin procesar nada y lo registra en el log

### Requirement: Configuración externa
La ruta de origen, la conexión a SQL Server, los reintentos ante archivos bloqueados, el mínimo de dígitos de una factura real y la retención de logs SHALL configurarse fuera del código. Las credenciales, si se usan, MUST NOT guardarse en archivos versionados.

#### Scenario: Cambio de carpeta de origen
- **WHEN** se cambia la ruta de origen en la configuración
- **THEN** la siguiente corrida lee la nueva carpeta sin modificar el código
