<#
.SYNOPSIS
  Run the tests and the diagnostics (no Telegram token needed).
.EXAMPLE
  .\scripts\check.ps1
  .\scripts\check.ps1 -Voice     # also the WAV -> action tests (needs the speech model and a Russian SAPI voice)
#>
[CmdletBinding()]
param([switch]$Voice)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONUTF8 = "1"
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Run .\scripts\setup.ps1 first." }

Write-Host "== tests =="
& $py -m pytest -q
$testsExit = $LASTEXITCODE

if ($Voice) {
    Write-Host "`n== voice pipeline tests (WAV -> speech recognition -> action) =="
    & $py -m pytest -q -m voice -v
}

Write-Host "`n== JARVIS diagnostics =="
& $py -m jarvis --check

Write-Host "`n== Telegram configuration =="
& $py bot.py --check
if ($LASTEXITCODE -ne 0) { Write-Host "(Telegram is not configured yet: local JARVIS still works.)" }

exit $testsExit
