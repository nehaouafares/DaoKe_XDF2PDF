@echo off
cd /d "%~dp0"
py -m pip install -r requirements.txt pyinstaller
py -m PyInstaller --noconsole --onefile --name DocBoxXDF2PDF --collect-data reportlab xdf_gui.py
echo.
echo Executable : dist\DocBoxXDF2PDF.exe
pause
