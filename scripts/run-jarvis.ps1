<#
.SYNOPSIS
  Start JARVIS. Arguments are passed to `python -m jarvis`.
.EXAMPLE
  .\scripts\run-jarvis.ps1                          # text mode
  .\scripts\run-jarvis.ps1 --voice                  # push-to-talk microphone mode
  .\scripts\run-jarvis.ps1 --voice --device cuda    # speech recognition on the NVIDIA GPU
  .\scripts\run-jarvis.ps1 --once "Покажи состояние ПК"
  .\scripts\run-jarvis.ps1 --wav C:\path\phrase.wav --dry-run
  .\scripts\run-jarvis.ps1 --check
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONUTF8 = "1"
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Run .\scripts\setup.ps1 first." }
& $py -m jarvis @args
