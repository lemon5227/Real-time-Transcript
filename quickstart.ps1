param(
    [ValidateSet("auto", "local", "cloud")]
    [string]$Mode = "auto"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $scriptDir ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    & $venvPython (Join-Path $scriptDir "quickstart.py") --mode $Mode
} else {
    & python (Join-Path $scriptDir "quickstart.py") --mode $Mode
}
exit $LASTEXITCODE
