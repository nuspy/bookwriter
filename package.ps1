<#
.SYNOPSIS
    Costruisce lo zip di bookwriter da consegnare a terzi.

.DESCRIPTION
    Impacchetta i sorgenti presi dall'ALBERO DI LAVORO (non da git: parte del codice potrebbe non
    essere ancora committata) escludendo virtualenv, cache, artefatti di build e qualunque dato di
    lavoro. Prima di scrivere lo zip verifica che i file indispensabili ci siano davvero: senza
    questo controllo si rischia di distribuire un pacchetto che non si avvia.

    I libri elaborati non sono nel repository: vivono in ~/.bookwriter, quindi non finiscono
    nel pacchetto in nessun caso.

.PARAMETER Version
    Etichetta del pacchetto. Se omessa viene letta da pyproject.toml.

.PARAMETER OutDir
    Cartella di destinazione. Default: dist.

.EXAMPLE
    .\package.ps1
.EXAMPLE
    .\package.ps1 -Version 0.2.0 -OutDir C:\Consegne
#>
[CmdletBinding()]
param(
    [string]$Version,
    [string]$OutDir = "dist"
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[OK] $msg"  -ForegroundColor Green }

# --- 1. Versione ------------------------------------------------------------
if (-not $Version) {
    $riga = Select-String -Path "pyproject.toml" -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
    if (-not $riga) { Write-Error "Versione non trovata in pyproject.toml: passala con -Version." }
    $Version = $riga.Matches[0].Groups[1].Value
}
Write-Step "Pacchetto bookwriter $Version"

# --- 2. Selezione dei file --------------------------------------------------
# Cartelle e schemi da non spedire mai: ambienti, cache, artefatti, dati di lavoro.
$cartelleEscluse = @('.git', '.venv', 'venv', '__pycache__', '.pytest_cache', '.ruff_cache',
                     'dist', 'build', '.bookwriter', 'node_modules')
$estensioniEscluse = @('.pyc', '.pyo', '.db', '.docx', '.pdf', '.epub', '.zip', '.whl',
                       '.db-wal', '.db-shm')

$radici = @('bookwriter', 'tests', 'docs')
$fileSingoli = @('pyproject.toml', 'README.md', 'LICENSE', 'install.ps1', 'run.ps1',
                 'build_manual.py', '.gitignore')

$prefisso = (Get-Location).Path.TrimEnd('\') + '\'
$selezionati = New-Object System.Collections.Generic.List[string]

foreach ($radice in $radici) {
    if (-not (Test-Path $radice)) { continue }
    Get-ChildItem -Path $radice -Recurse -File | ForEach-Object {
        $relativo = $_.FullName.Substring($prefisso.Length)
        $pezzi = $relativo -split '\\'
        if ($pezzi | Where-Object { $cartelleEscluse -contains $_ }) { return }
        # I PDF sono esclusi come dato di lavoro, ma il manuale in docs\ deve viaggiare.
        if ($estensioniEscluse -contains $_.Extension.ToLower() -and
            -not ($relativo -like 'docs\*.pdf')) { return }
        if ($_.Name -like '*.egg-info') { return }
        $selezionati.Add($relativo)
    }
}
foreach ($file in $fileSingoli) {
    if (Test-Path $file) { $selezionati.Add($file) }
    else { Write-Error "File obbligatorio mancante: $file" }
}

# --- 3. Controllo di sicurezza ---------------------------------------------
# Un pacchetto senza questi file si installa ma non parte: meglio fallire qui.
Write-Step "Verifica dei file indispensabili"
$obbligatori = @(
    'bookwriter\__init__.py', 'bookwriter\cli.py',
    'bookwriter\gui\main_window.py', 'bookwriter\gui\i18n.py',
    'bookwriter\update\render.py', 'bookwriter\update\revise.py',
    'bookwriter\atomize\sections.py', 'bookwriter\compose\narrative.py',
    'docs\MANUAL.md', 'docs\bookwriter-manual.pdf'
)
foreach ($atteso in $obbligatori) {
    if ($selezionati -notcontains $atteso) { Write-Error "Manca dal pacchetto: $atteso" }
}
$lingue = @($selezionati | Where-Object { $_ -like 'bookwriter\gui\locales\*.json' })
if ($lingue.Count -lt 11) {
    Write-Error "Cataloghi di traduzione insufficienti: $($lingue.Count) invece di 11."
}
Write-Ok "$($selezionati.Count) file, di cui $($lingue.Count) cataloghi di lingua"

# --- 4. Scrittura dello zip -------------------------------------------------
$destinazione = Join-Path $PSScriptRoot $OutDir
New-Item -ItemType Directory -Force -Path $destinazione | Out-Null
# Artefatti obsoleti di build precedenti: non devono viaggiare con la consegna.
@('*.whl', '*.tar.gz') | ForEach-Object {
    Get-ChildItem -Path (Join-Path $destinazione $_) -File -ErrorAction SilentlyContinue |
        Remove-Item -Force
}

$zip = Join-Path $destinazione "bookwriter-$Version.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }

Write-Step "Scrittura di $zip"
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archivio = [System.IO.Compression.ZipFile]::Open($zip, 'Create')
try {
    foreach ($relativo in $selezionati) {
        # Dentro allo zip tutto sta in una cartella col nome del pacchetto: chi lo apre non si
        # ritrova venti file sparsi nella cartella dei download.
        $dentro = "bookwriter-$Version/" + ($relativo -replace '\\', '/')
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $archivio, (Join-Path $PSScriptRoot $relativo), $dentro, 'Optimal') | Out-Null
    }
} finally {
    $archivio.Dispose()
}

$dimensione = (Get-Item $zip).Length / 1MB
Write-Host ""
Write-Ok ("Pacchetto pronto: {0}" -f $zip)
Write-Host ("     {0} file · {1:N1} MB" -f $selezionati.Count, $dimensione)
Write-Host "     Chi lo riceve: scompatta, poi .\install.ps1 e infine .\run.ps1"
