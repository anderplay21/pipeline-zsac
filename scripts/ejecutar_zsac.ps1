# Ejecuta una corrida incremental del pipeline ZSAC (pensado para el Programador de tareas de Windows).
# Propaga el código de salida: 0 = OK, 1 = PARCIAL, 2 = ERROR, 3 = otra corrida en curso.
#
# Registro de la tarea (lunes a viernes 10:00, usuario actual):
#   schtasks /Create /TN zsac_etl /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 10:00 /F `
#     /TR 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\aguzman\pipeline-zsac\scripts\ejecutar_zsac.ps1"'

param(
    [switch]$Todos
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
$python = Join-Path $raiz "env\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Error "No se encontró el entorno virtual en $python"
    exit 2
}

Set-Location $raiz
$argumentos = @("-m", "zsac_etl", "run")
if ($Todos) { $argumentos += "--todos" }

& $python @argumentos
exit $LASTEXITCODE
