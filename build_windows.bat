@echo off
setlocal
cd /d "%~dp0"

if not exist ".build-venv\Scripts\python.exe" (
    py -3 -m venv .build-venv
    if errorlevel 1 exit /b 1
)

".build-venv\Scripts\python.exe" -m pip install -r requirements-build.txt
if errorlevel 1 exit /b 1

".build-venv\Scripts\python.exe" -m PyInstaller --clean --noconfirm Fallout4VoiceRandomizer.spec
if errorlevel 1 exit /b 1

echo.
echo Build complete: dist\Fallout4VoiceRandomizer\Fallout4VoiceRandomizer.exe
echo Copy or run the entire Fallout4VoiceRandomizer folder.

pause
