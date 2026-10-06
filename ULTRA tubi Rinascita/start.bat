@echo off
cd /d "%~dp0"

set "PYTHON311=%LocalAppData%\Programs\Python\Python311\python.exe"

if exist "%PYTHON311%" (
    "%PYTHON311%" app.py
    goto :eof
)

py -3.11 -c "import sys" >nul 2>&1
if not errorlevel 1 (
    py -3.11 app.py
    goto :eof
)

python app.py
