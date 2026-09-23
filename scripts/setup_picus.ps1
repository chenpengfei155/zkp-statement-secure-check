param([string]$HomeDirectory = '', [string]$Python = '')
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
if (-not $Python) {
    $Python = Join-Path $projectDirectory '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $Python)) {
        throw 'Create the project Python environment first: py -3 -m venv .venv'
    }
}
$installArgs = @((Join-Path $PSScriptRoot 'setup_picus_native.py'))
if ($HomeDirectory) { $installArgs += @('--home', $HomeDirectory) }
& $Python @installArgs
if ($LASTEXITCODE -ne 0) { throw 'Native Picus installation failed; see the output above.' }
