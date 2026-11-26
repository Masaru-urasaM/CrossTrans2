@echo off
setlocal
cd /d "%~dp0"
title Build CrossTrans 2 EXE
python tools\make_icons.py --check
if not errorlevel 1 goto :icons_ok
python tools\make_icons.py
if errorlevel 1 goto :icons_failed
:icons_ok
python tools\verify_furigana_bundle.py --source
if errorlevel 1 exit /b 1
python -m PyInstaller CrossTrans.spec --clean --noconfirm --distpath dist-release --workpath build-release
if errorlevel 1 goto :build_failed
if not exist "dist-release\CrossTrans2.exe" goto :build_failed
python tools\verify_furigana_bundle.py --exe "dist-release\CrossTrans2.exe"
if errorlevel 1 goto :bundle_failed
python tools\verify_ui_bundle.py --exe "dist-release\CrossTrans2.exe" --json "logs\ui-bundle.json"
if errorlevel 1 goto :bundle_failed
"dist-release\CrossTrans2.exe" --self-check "logs\self-check.json"
if errorlevel 1 goto :bundle_failed
echo SUCCESS! Verified dist-release\CrossTrans2.exe
echo Build the installer with tools\build_release.ps1 and the Inno compiler.
exit /b 0
:icons_failed
echo ERROR: Icon preparation failed.
exit /b 1
:build_failed
echo ERROR: CrossTrans 2 EXE build failed. No release is verified.
exit /b 1
:bundle_failed
echo ERROR: Packaged data or code did not pass verification.
exit /b 1
