<#
.SYNOPSIS
    Avvia il microservizio TRAWL in modalita standalone per Bookwriter.
#>
[CmdletBinding()]
param(
    [int]$Port = 8191
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$trawlDir = Join-Path $PSScriptRoot "tools\trawl"
if (-not (Test-Path $trawlDir)) {
    Write-Error "La cartella $trawlDir non esiste. Esegui prima .\update_trawl.ps1 per clonare e preparare TRAWL."
}

# Ricerca Bun (priorità a ~/.bun/bin per le versioni aggiornate 1.4+)
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
    Write-Error "Bun non trovato nel sistema. Installalo con 'winget install Oven-sh.Bun' o da https://bun.sh"
}

$env:PORT = "$Port"
$env:SESSION_CACHE_DRIVER = "memory"
$env:BROWSER_POOL_SIZE = "1"
$env:BROWSER_HEADFUL_POOL_SIZE = "0"
$env:SCRAPE_MIN_TIER = "1"
$env:MCP_ENABLED = "false"
$env:MITM_ENABLED = "false"

Write-Host "`n========================================================" -ForegroundColor Cyan
Write-Host " Avvio di TRAWL in modalita standalone" -ForegroundColor Cyan
Write-Host " Endpoint API: http://127.0.0.1:$Port/v1" -ForegroundColor Green
Write-Host " Per arrestare: premi Ctrl+C" -ForegroundColor Yellow
Write-Host "========================================================`n" -ForegroundColor Cyan

Push-Location $trawlDir
try {
    & $bunExe --cwd apps/api src/index.ts
} finally {
    Pop-Location
}
