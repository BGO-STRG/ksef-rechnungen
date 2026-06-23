@echo off
echo === KSeF EXE Builder ===
pip install pyinstaller ksef2 lxml
pyinstaller --onefile --windowed --name KSeF_Rechnungen ksef_app.py
echo.
echo Fertig! EXE liegt unter: dist\KSeF_Rechnungen.exe
pause
