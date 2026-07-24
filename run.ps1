<#
.SYNOPSIS
    Avvia bookwriter dal virtualenv locale (.venv).

.DESCRIPTION
    Senza argomenti lancia la GUI desktop (PySide6).
    Con argomenti li inoltra alla CLI di bookwriter.

.EXAMPLE
    .\run.ps1
    # -> apre la GUI

.EXAMPLE
    .\run.ps1 doctor
    # -> esegue: bookwriter doctor

.EXAMPLE
    .\run.ps1 project new "Il mio libro" --target-words 30000
    # -> esegue: bookwriter project new "Il mio libro" --target-words 30000
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "[!] Virtualenv non trovato. Esegui prima .\install.ps1" -ForegroundColor Yellow
    exit 1
}

if (-not $Args -or $Args.Count -eq 0) {
    Write-Host "==> Avvio della GUI (bookwriter gui). Chiudi la finestra per terminare." -ForegroundColor Cyan
    & $venvPy -m bookwriter gui
} else {
    & $venvPy -m bookwriter @Args
}

exit $LASTEXITCODE
