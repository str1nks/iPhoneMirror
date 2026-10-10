@echo off
cd /d "%~dp0"
title Building iPhoneMirror.exe

if exist icon.ico goto have_icon
echo icon.ico not found next to this file. Put icon.ico here and run again.
pause
exit /b 1

:have_icon
echo [1/3] Installing build tools...
python -m pip install --upgrade pyinstaller PyQt6 PyQt6-Fluent-Widgets PyQt6-Frameless-Window
if errorlevel 1 goto fail

echo [2/3] Building exe...
python -m PyInstaller --noconfirm --clean --windowed --name iPhoneMirror --icon icon.ico --add-data "icon.ico;." --collect-all qfluentwidgets --collect-all qframelesswindow airplay_mirror.py
if errorlevel 1 goto fail

echo [3/3] Copying uxplay folder and icon...
copy /Y icon.ico "dist\iPhoneMirror\icon.ico" >nul
if exist uxplay xcopy "uxplay" "dist\iPhoneMirror\uxplay" /E /I /Y >nul

echo.
echo ============================================================
echo  DONE: dist\iPhoneMirror\iPhoneMirror.exe
echo  Move the whole dist\iPhoneMirror folder wherever you like
echo  and run iPhoneMirror.exe once - autostart is enabled itself.
echo ============================================================
pause
exit /b 0

:fail
echo.
echo BUILD FAILED. Send me the last lines of the output above.
pause
exit /b 1
