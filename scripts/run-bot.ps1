<#
.SYNOPSIS
  Start the Telegram bot (needs BOT_TOKEN and AUTHORIZED_USER_IDS in .env).
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONUTF8 = "1"
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Run .\scripts\setup.ps1 first." }
& $py bot.py @args
