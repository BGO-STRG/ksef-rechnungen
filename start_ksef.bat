@echo off
cd /d "%~dp0"
echo KSeF Batch-Uebermittlung wird gestartet...
echo Verzeichnis: %~dp0
echo.
KSeF_Batch.exe --no-wait
echo.
echo Batch abgeschlossen.
