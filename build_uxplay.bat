@echo off
title Building UxPlay
echo ============================================================
echo  Automatic UxPlay build for Windows via MSYS2
echo  Takes 10-20 minutes, internet required. Do not close window.
echo ============================================================
echo.

if exist C:\msys64\usr\bin\bash.exe goto have_msys
echo [1/4] Installing MSYS2...
winget install -e --id MSYS2.MSYS2 --accept-package-agreements --accept-source-agreements
if exist C:\msys64\usr\bin\bash.exe goto have_msys
echo.
echo Could not install MSYS2 automatically.
echo Download it from https://www.msys2.org and install to C:\msys64, then run this file again.
pause
exit /b 1

:have_msys
set "MSYS2ENV=C:\msys64\usr\bin\env.exe MSYSTEM=UCRT64 /usr/bin/bash -lc"

echo [2/4] Updating MSYS2...
%MSYS2ENV% "pacman -Syu --noconfirm"
%MSYS2ENV% "pacman -Syu --noconfirm"

echo [3/4] Installing compiler and libraries...
%MSYS2ENV% "pacman -S --needed --noconfirm git mingw-w64-ucrt-x86_64-cmake mingw-w64-ucrt-x86_64-ninja mingw-w64-ucrt-x86_64-gcc mingw-w64-ucrt-x86_64-openssl mingw-w64-ucrt-x86_64-libplist mingw-w64-ucrt-x86_64-gstreamer mingw-w64-ucrt-x86_64-gst-plugins-base mingw-w64-ucrt-x86_64-gst-plugins-good mingw-w64-ucrt-x86_64-gst-plugins-bad mingw-w64-ucrt-x86_64-gst-libav"

echo [4/4] Building UxPlay...
%MSYS2ENV% "cd ~ && rm -rf UxPlay && git clone https://github.com/FDH2/UxPlay && cd UxPlay && mkdir build && cd build && cmake .. -G Ninja && ninja && cmake --install . --prefix /ucrt64"

echo.
if exist C:\msys64\ucrt64\bin\uxplay.exe goto ok
echo BUILD FAILED. Send me the last lines of the output above.
pause
exit /b 1

:ok
echo ============================================================
echo  DONE: C:\msys64\ucrt64\bin\uxplay.exe
echo  Now run:  python airplay_mirror.py
echo ============================================================
pause
