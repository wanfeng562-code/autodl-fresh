@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 goto :error
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error
echo.
echo Setup completed. Double-click start.bat to launch AutoDL Fresh.
echo Edge is selected by default; no extra browser download is required.
pause
exit /b 0
:error
echo Setup failed. Please check Python and network access.
pause
exit /b 1
