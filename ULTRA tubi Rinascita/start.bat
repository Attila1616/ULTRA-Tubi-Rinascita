@echo off
cd /d "%~dp0"

set "PYTHON311=%LocalAppData%\Programs\Python\Python311\python.exe"
set "PYTHONW311=%LocalAppData%\Programs\Python\Python311\pythonw.exe"

rem Prefer pythonw so ULTRA is detached from this CMD window.
if exist "%PYTHONW311%" (
    start "" "%PYTHONW311%" app.py
    exit /b
)

rem Fallbacks still launch as a separate process so closing this CMD does not
rem terminate ULTRA.
if exist "%PYTHON311%" (
    start "" "%PYTHON311%" app.py
    exit /b
)

where pyw >nul 2>&1
if not errorlevel 1 (
    start "" pyw -3.11 app.py
    exit /b
)

where py >nul 2>&1
if not errorlevel 1 (
    start "" py -3.11 app.py
    exit /b
)

start "" python app.py
