<#
.SYNOPSIS
    Aggiorna il microservizio TRAWL (tools/trawl) dal repository originale GitHub.

.DESCRIPTION
    - Ferma eventuali processi TRAWL o Camoufox in esecuzione locale.
    - Se tools/trawl non esiste, clona il repository da https://github.com/germondai/trawl.git.
    - Esegue il pull degli ultimi aggiornamenti dal repository upstream.
    - Esegue bun install per aggiornare le dipendenze e i pacchetti.
    - Mantiene o ripristina la configurazione locale .env (in-memory, porta 8191).

.EXAMPLE
    .\update_trawl.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[OK] $msg"  -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "[!] $msg"  -ForegroundColor Yellow }

$trawlDir = Join-Path $PSScriptRoot "tools\trawl"

# --- 1. Verifica Git e Bun ---------------------------------------------------
Write-Step "Controllo degli strumenti richiesti (git e bun)"

if (-not (Get-Command "git" -ErrorAction SilentlyContinue)) {
    Write-Error "Git non è stato trovato nel PATH. Installa Git e riprova."
}
Write-Ok "Git disponibile"

$bunExe = $null
$homeBun = Join-Path $env:USERPROFILE ".bun\bin\bun.exe"
if (Test-Path $homeBun) {
    $bunExe = $homeBun
} elseif (Get-Command "bun" -ErrorAction SilentlyContinue) {
    $bunExe = "bun"
} else {
    $localAppData = $env:LOCALAPPDATA
    if ($localAppData) {
        $cands = Get-ChildItem -Path "$localAppData\Microsoft\WinGet\Packages" -Filter "bun.exe" -Recurse -ErrorAction SilentlyContinue
        if ($cands -and $cands.Count -gt 0) {
            $bunExe = $cands[0].FullName
        }
    }
}


if (-not $bunExe) {
    Write-Error "Bun non è stato trovato. Installalo con 'winget install Oven-sh.Bun' o da https://bun.sh"
}
Write-Ok "Bun disponibile: $bunExe"

# --- 2. Arresto di TRAWL se in esecuzione -------------------------------------
Write-Step "Controllo processi attivi di TRAWL"
$camoufoxProc = Get-Process -Name "camoufox" -ErrorAction SilentlyContinue
if ($camoufoxProc) {
    Write-Warn2 "Arresto dei processi Camoufox in corso..."
    $camoufoxProc | Stop-Process -Force -ErrorAction SilentlyContinue
}

# --- 3. Clone o Pull da GitHub ------------------------------------------------
if (-not (Test-Path $trawlDir)) {
    Write-Step "Cartella tools/trawl assente: clonazione iniziale dal repository originale"
    New-Item -ItemType Directory -Path (Join-Path $PSScriptRoot "tools") -Force | Out-Null
    git clone --depth 1 https://github.com/germondai/trawl.git $trawlDir
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Errore durante la clonazione di TRAWL."
    }
    Write-Ok "Clonato con successo in $trawlDir"
} else {
    Write-Step "Aggiornamento del repository da upstream (git pull)"
    Push-Location $trawlDir
    try {
        git fetch origin
        git pull --rebase origin (git rev-parse --abbrev-ref HEAD)
        if ($LASTEXITCODE -ne 0) {
            Write-Warn2 "Pull con rebase non riuscito, provo pull standard..."
            git pull origin
        }
    } finally {
        Pop-Location
    }
    Write-Ok "Codice sorgente aggiornato"
}

# --- 4. Aggiornamento dipendenze con Bun --------------------------------------
Write-Step "Installazione e aggiornamento delle dipendenze (bun install)"
Push-Location $trawlDir
try {
    & $bunExe install
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Errore durante 'bun install' in $trawlDir."
    }
} finally {
    Pop-Location
}
Write-Ok "Dipendenze aggiornate"

# --- 5. Configurazione .env --------------------------------------------------
$envFile = Join-Path $trawlDir ".env"
if (-not (Test-Path $envFile)) {
    Write-Step "Creazione della configurazione locale .env per bookwriter"
    @"
PORT=8191
SESSION_CACHE_DRIVER=memory
BROWSER_POOL_SIZE=1
BROWSER_HEADFUL_POOL_SIZE=0
SCRAPE_MIN_TIER=1
MCP_ENABLED=false
MITM_ENABLED=false
"@ | Set-Content -Path $envFile -Encoding utf8
    Write-Ok "Configurazione .env creata"
} else {
    Write-Ok "Configurazione .env verificata"
}

# --- 6. Riepilogo versione ---------------------------------------------------
Push-Location $trawlDir
try {
    $commit = git rev-parse --short HEAD
    $date = git log -1 --format=%cd --date=short
    Write-Host "`n========================================================" -ForegroundColor Green
    Write-Host " TRAWL aggiornato con successo!" -ForegroundColor Green
    Write-Host " Commit: $commit ($date)" -ForegroundColor Green
    Write-Host " Directory: $trawlDir" -ForegroundColor Green
    Write-Host "========================================================`n" -ForegroundColor Green
} finally {
    Pop-Location
}
