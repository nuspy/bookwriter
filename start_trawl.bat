@echo off
rem Avvio di TRAWL in modalita standalone per Bookwriter
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_trawl.ps1" %*
