$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $scriptDir ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    & $venvPython (Join-Path $scriptDir "quickstart.py") @args
} else {
    & python (Join-Path $scriptDir "quickstart.py") @args
}
exit $LASTEXITCODE
