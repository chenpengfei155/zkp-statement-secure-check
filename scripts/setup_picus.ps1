param([string]$Distro = 'Ubuntu-22.04', [int]$Jobs = 4)
$ErrorActionPreference = 'Stop'
$scriptPath = (Resolve-Path (Join-Path $PSScriptRoot 'setup_picus.sh')).Path
$linuxScript = (& wsl.exe -d $Distro --exec wslpath -u $scriptPath).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot access the selected WSL distribution.' }
# Root is used only for Ubuntu packages. Picus and its solver belong to the WSL user.
& wsl.exe -d $Distro -u root --exec bash $linuxScript --system-deps
if ($LASTEXITCODE -ne 0) { throw 'Installing Ubuntu packages failed.' }
$installArgs = @('-d', $Distro, '--exec', 'env')
if ($env:CIRVERIFY_PICUS_HOME) { $installArgs += "CIRVERIFY_PICUS_HOME=$env:CIRVERIFY_PICUS_HOME" }
$installArgs += @('bash', $linuxScript, '--jobs', $Jobs)
& wsl.exe @installArgs
if ($LASTEXITCODE -ne 0) { throw 'Installing Picus failed; see the output above.' }
Write-Host 'Picus is ready. Restart the CirVerify web server to refresh its environment check.'
