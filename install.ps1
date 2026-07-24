<#
.SYNOPSIS
    Installa bookwriter in un virtualenv locale (.venv) con tutte le dipendenze.

.DESCRIPTION
    - Verifica che Python 3.10+ sia disponibile.
    - Crea .venv se non esiste (idempotente: riusa quello presente).
    - Aggiorna pip e installa il pacchetto in editable con gli extra scelti.
    - Esegue un controllo (bookwriter doctor) se LM Studio è raggiungibile.

.PARAMETER Extras
    Set di dipendenze opzionali. Default: "all,dev" (loader pdf/docx/epub + GUI + strumenti dev).
    Alternative: "" (core), "ingest", "gui", "all", "all,dev".

.PARAMETER Recreate
    Ricrea .venv da zero eliminando quello esistente.

.PARAMETER SkipDoctor
    Non eseguire il controllo LM Studio al termine.

.EXAMPLE
    .\install.ps1
.EXAMPLE
    .\install.ps1 -Extras "gui" -Recreate
#>
[CmdletBinding()]
param(
    [string]$Extras = "all,dev",
    [switch]$Recreate,
    [switch]$SkipDoctor
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[OK] $msg"  -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "[!] $msg"  -ForegroundColor Yellow }

# --- 1. Trova un interprete Python 3.10+ ------------------------------------
Write-Step "Ricerca di Python 3.10+"
$python = $null
foreach ($cand in @("py -3", "python", "python3")) {
    $parts = $cand.Split(" ")
    $exe = $parts[0]
    if (Get-Command $exe -ErrorAction SilentlyContinue) {
        try {
            $ver = & $exe @($parts[1..($parts.Length-1)]) -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($ver -and [version]$ver -ge [version]"3.10") {
                $python = $cand
                Write-Ok "Trovato Python $ver ($cand)"
                break
            }
        } catch { }
    }
}
if (-not $python) {
    Write-Error "Nessun Python 3.10+ trovato nel PATH. Installa Python da https://www.python.org/ e riprova."
}

# --- 2. Virtualenv ----------------------------------------------------------
$venv = Join-Path $PSScriptRoot ".venv"
$venvPy = Join-Path $venv "Scripts\python.exe"

if ($Recreate -and (Test-Path $venv)) {
    Write-Step "Rimozione di .venv esistente (-Recreate)"
    Remove-Item -Recurse -Force $venv
}

if (-not (Test-Path $venvPy)) {
    Write-Step "Creazione del virtualenv in .venv"
    $pyParts = $python.Split(" ")
    & $pyParts[0] @($pyParts[1..($pyParts.Length-1)]) -m venv $venv
    Write-Ok "Virtualenv creato"
} else {
    Write-Ok "Virtualenv già presente (riuso). Usa -Recreate per ricrearlo."
}

# --- 3. Installazione -------------------------------------------------------
Write-Step "Aggiornamento di pip"
& $venvPy -m pip install --upgrade pip --quiet

$spec = if ($Extras -and $Extras.Trim()) { ".[$Extras]" } else { "." }
Write-Step "Installazione di bookwriter (pip install -e `"$spec`")"
& $venvPy -m pip install -e $spec
Write-Ok "Pacchetto installato"

# --- 4. Controllo LM Studio (opzionale) -------------------------------------
if (-not $SkipDoctor) {
    Write-Step "Controllo ambiente (bookwriter doctor)"
    try {
        & $venvPy -m bookwriter doctor
    } catch {
        Write-Warn2 "doctor non completato: LM Studio è avviato su http://localhost:1234 con i modelli caricati?"
    }
}

Write-Host ""
Write-Ok "Installazione completata."
Write-Host "Avvia con:  " -NoNewline; Write-Host ".\run.ps1" -ForegroundColor White -NoNewline
Write-Host "         (GUI)   oppure   " -NoNewline; Write-Host ".\run.ps1 doctor" -ForegroundColor White -NoNewline
Write-Host "  (CLI)"
