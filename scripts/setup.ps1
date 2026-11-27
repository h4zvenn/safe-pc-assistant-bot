<#
.SYNOPSIS
  Create .venv and install dependencies (idempotent).
.EXAMPLE
  .\scripts\setup.ps1                  # bot + voice + tests
  .\scripts\setup.ps1 -NoVoice         # Telegram bot / text mode only
  .\scripts\setup.ps1 -Gpu             # also CUDA libraries for NVIDIA GPU recognition (~1 GB)
  .\scripts\setup.ps1 -DownloadModel   # also fetch the speech model now
#>
[CmdletBinding()]
param(
    [switch]$NoVoice,
    [switch]$Gpu,
    [switch]$DownloadModel,
    [string]$VenvDir = ".venv"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONUTF8 = "1"

function Find-Python {
    $supported = "3.13", "3.12", "3.11"
    $local = [Environment]::GetFolderPath("LocalApplicationData")
    foreach ($v in $supported) {
        $candidate = Join-Path $local "Programs\Python\Python$($v.Replace('.', ''))\python.exe"
        if (Test-Path $candidate) { return $candidate }
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
        $ver = & $cmd.Source -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($ver -in $supported) { return $cmd.Source }
    }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        foreach ($v in $supported) {
            $found = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $found) { return $found }
        }
    }
    throw "Python 3.11-3.13 not found. Install Python 3.13 from python.org (the project is tested on 3.13)."
}

$python = Find-Python
Write-Host "Python: $python ($(& $python --version))"

if (-not (Test-Path "$VenvDir\Scripts\python.exe")) {
    Write-Host "Creating $VenvDir ..."
    & $python -m venv $VenvDir
}
$venvPython = Join-Path $root "$VenvDir\Scripts\python.exe"

& $venvPython -m pip install --upgrade pip | Out-Null

$requirements = if ($Gpu) { "requirements-gpu.txt" } elseif ($NoVoice) { "requirements.txt" } else { "requirements-voice.txt" }
Write-Host "Installing $requirements ..."
& $venvPython -m pip install -r $requirements
& $venvPython -m pip install "pytest>=8"

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example. Fill in BOT_TOKEN and AUTHORIZED_USER_IDS for Telegram (not needed for local JARVIS)."
} else {
    Write-Host ".env already exists, left untouched."
}

if ($DownloadModel -and -not $NoVoice) {
    $model = if ($env:JARVIS_STT_MODEL) { $env:JARVIS_STT_MODEL } else { "small" }
    Write-Host "Downloading speech model '$model' ..."
    & $venvPython -c "from faster_whisper.utils import download_model; print(download_model('$model'))"
}

Write-Host "`nDone. Next: .\scripts\check.ps1   then   .\scripts\run-jarvis.ps1"
