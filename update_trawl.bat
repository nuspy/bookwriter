@echo off
rem Aggiornamento automatico del microservizio TRAWL per Bookwriter
echo Avvio aggiornamento TRAWL...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0update_trawl.ps1"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERRORE] Aggiornamento non riuscito.
    pause
    exit /b %ERRORLEVEL%
)
