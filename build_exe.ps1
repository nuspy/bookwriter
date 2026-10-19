<#
.SYNOPSIS
    Compila bookwriter in un eseguibile Windows con Nuitka, senza sorgenti dentro.

.DESCRIPTION
    Nuitka traduce il Python in C e lo compila con Visual Studio: nella cartella che esce non c'è
    né un file .py né un .pyc del programma, solo codice macchina. È quello che si consegna.

    Prima di compilare esegue tutta la suite di test: se uno solo fallisce, non compila.

    Il risultato è dist\bookwriter\ con dentro bookwriter.exe e le librerie che gli servono:
    senza argomenti apre la finestra, con argomenti è la riga di comando.

    La prima compilazione è lunga (Qt, numpy, scikit-learn, pyarrow diventano C anche loro); le
    successive riusano la cache di Nuitka e sono molto più rapide.

.PARAMETER Version
    Versione scritta nelle proprietà del file. Se omessa viene letta da pyproject.toml.

.EXAMPLE
    .\build_exe.ps1
#>
[CmdletBinding()]
param([string]$Version)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[OK] $msg"  -ForegroundColor Green }

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Error "Virtualenv non trovato: esegui prima .\install.ps1" }

if (-not $Version) {
    $riga = Select-String -Path "pyproject.toml" -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
    $Version = $riga.Matches[0].Groups[1].Value
}
# Le proprietà di un .exe vogliono quattro numeri.
$versioneFile = (($Version -split '[^0-9]+' | Where-Object { $_ }) + @('0', '0', '0', '0'))[0..3] -join '.'

# Serve Nuitka 4.4 o successivo. Con la 4.3 e PySide6 6.11 la finestra compilata va in crash
# all'avvio: il plugin PySide6 di Nuitka libera il metodo passato a connect() quando arriva da
# una tupla spacchettata in un ciclo (lo schema dei pulsanti di «Prepara contenuti»). La riga di
# comando funzionava, ed è per questo che la 4.3 sembrava andare bene.
$versioneNuitka = (& $py -m nuitka --version 2>$null | Select-Object -First 1)
if (-not $versioneNuitka -or [version]($versioneNuitka -replace '[^0-9.].*$', '') -lt [version]'4.4') {
    Write-Step "Installo Nuitka 4.4 o successivo nel virtualenv (trovato: $versioneNuitka)"
    & $py -m pip install -q --timeout 120 -U --pre "nuitka>=4.4rc1" ordered-set
    if ($LASTEXITCODE -ne 0) {
        & $py -m pip install -q --timeout 120 -U "https://github.com/Nuitka/Nuitka/archive/factory.zip"
    }
}

# Prima si prova, poi si compila: compilare richiede venticinque minuti, e un errore che i test
# trovano in tre va scoperto prima, non dentro un eseguibile già consegnato.
Write-Step "Eseguo i test prima di compilare"
& $py -m pytest -q -p no:cacheprovider
if ($LASTEXITCODE -ne 0) {
    Write-Error "I test non passano (codice $LASTEXITCODE): non compilo. Correggi e rilancia."
}
Write-Ok "Test superati"

$lavoro = Join-Path $PSScriptRoot "build\nuitka"
$uscita = Join-Path $PSScriptRoot "dist\bookwriter"

$argomenti = @(
    "-m", "nuitka",
    "--standalone",
    "--assume-yes-for-downloads",
    "--msvc=latest",
    "--enable-plugin=pyside6",
    # Doppio clic: nessuna console. Da un terminale: l'output della riga di comando ci arriva.
    "--windows-console-mode=attach",
    # Tutto il programma, compresi i moduli caricati solo quando servono.
    "--include-package=bookwriter",
    "--include-package=llmswitch",
    # La sillabazione la chiede reportlab solo se c'è: va inclusa a mano, dizionari compresi.
    "--include-package=pyphen",
    # I dati che il programma legge accanto al codice (traduzioni, metodo, modelli) e quelli
    # delle librerie: i caratteri di reportlab, il modello vuoto di python-docx. I dizionari di
    # pyphen li porta già Nuitka da sé.
    "--include-package-data=bookwriter",
    "--include-package-data=reportlab",
    "--include-package-data=docx",
    "--include-data-files=docs/bookwriter-manual.pdf=docs/bookwriter-manual.pdf",
    # Quello che non serve e farebbe solo crescere la cartella e i tempi.
    "--nofollow-import-to=pytest,_pytest,IPython,matplotlib,tkinter,torch,pandas,polars",
    "--output-dir=$lavoro",
    "--output-folder-name=bookwriter",
    "--output-filename=bookwriter.exe",
    "--product-name=bookwriter",
    "--file-description=bookwriter",
    "--product-version=$versioneFile",
    "--file-version=$versioneFile",
    "--report=$lavoro\report.xml",
    "packaging\bookwriter_app.py"
)

Write-Step "Compilo bookwriter $Version (Nuitka, MSVC)"
$inizio = Get-Date
# MSVC accoda a ogni compilazione le opzioni scritte in CL e _CL_. Se lì c'è uno standard C++
# (per esempio «_CL_=/std:c++20», messo per un altro progetto), cl rifiuta il C di Nuitka:
# «'/std:c11' and '/std:c++20' command-line options are incompatible». Le si toglie solo a
# questo processo e solo per la durata della build: le impostazioni di Windows non si toccano.
$salvate = @{ CL = $env:CL; _CL_ = $env:_CL_ }
$env:CL = $null
$env:_CL_ = $null
try {
    & $py @argomenti
    $esito = $LASTEXITCODE
} finally {
    $env:CL = $salvate.CL
    $env:_CL_ = $salvate._CL_
}
if ($esito -ne 0) { Write-Error "Nuitka si è fermato con codice $esito." }

Write-Step "Copio il risultato in $uscita"
if (Test-Path $uscita) { Remove-Item $uscita -Recurse -Force }
New-Item -ItemType Directory -Force -Path (Split-Path $uscita) | Out-Null
# Nuitka mette il risultato in «<nome>.dist» anche con --output-folder-name.
Copy-Item (Join-Path $lavoro "bookwriter.dist") $uscita -Recurse

# Il controllo che conta: nella consegna non deve esserci codice sorgente del programma.
$sorgenti = Get-ChildItem $uscita -Recurse -File -Include *.py, *.pyc, *.pyw |
    Where-Object { $_.FullName -match '\\bookwriter\\(bookwriter|packaging)\\' }
if ($sorgenti) { Write-Error "Nella cartella ci sono sorgenti del programma: $($sorgenti[0].FullName)" }

$peso = (Get-ChildItem $uscita -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
$durata = (Get-Date) - $inizio
Write-Host ""
Write-Ok ("Eseguibile pronto: {0}" -f (Join-Path $uscita "bookwriter.exe"))
Write-Host ("     {0:N0} MB · compilato in {1:N0} minuti" -f $peso, $durata.TotalMinutes)
