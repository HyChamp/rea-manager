@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PY="
set "LAUNCHER="
set "CHECK=import argparse, ssl, subprocess, urllib.request, winreg, sys; assert sys.version_info >= (3, 10)"
set "ENTRY=%~dp0scripts\doctor.py"
if not exist "%ENTRY%" goto missingfiles
if not defined REA_PYTHON goto launcher
call :probe "%REA_PYTHON%"
if defined PY goto run
 echo REA_PYTHON is not a working Python 3.10+ executable. Trying other locations.
:launcher
for /f "delims=" %%P in ('where.exe py.exe 2^>nul') do if not defined LAUNCHER call :probe_launcher "%%P"
if defined LAUNCHER goto run_launcher
for /f "delims=" %%P in ('where.exe python.exe 2^>nul') do if not defined PY call :probe "%%P"
if defined PY goto run
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do if not defined PY call :probe "%%~fD\python.exe"
if defined PY goto run
if defined IDADIR call :probe "%IDADIR%\python311\python.exe"
if defined PY goto run
 echo No system Python 3.10+ found. Auto-downloading portable Python (10.6 MB, no admin needed)...
set "PSBOOT=%~dp0scripts\bootstrap-python.ps1"
if not exist "%PSBOOT%" goto nopython
for /f "usebackq delims=" %%P in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%PSBOOT%"`) do set "PY=%%P"
if defined PY if exist "%PY%" goto run
:nopython
 echo [!] Portable Python bootstrap failed (see messages above).
 echo     Manual fallback: install Python 3.10+ from https://www.python.org/downloads/windows/
 echo     (enable "Add python.exe to PATH"), then run this file again.
 echo     Or set REA_PYTHON to the full path of an existing python.exe.
set "RC=1"
goto finish
:run
 echo Using Python: "%PY%"
"%PY%" -u "%ENTRY%" %*
set "RC=%ERRORLEVEL%"
goto finish
:run_launcher
 echo Using Python launcher: "%LAUNCHER%"
"%LAUNCHER%" -3 -u "%ENTRY%" %*
set "RC=%ERRORLEVEL%"
goto finish
:missingfiles
 echo Installer files are missing. Extract the entire ZIP before running this file.
set "RC=1"
:finish
 echo Exit code: %RC%
if "%~1"=="" pause
exit /b %RC%
:probe
if not exist "%~1" exit /b 0
set "CANDIDATE=%~1"
if /i not "%CANDIDATE:\WindowsApps\=%"=="%CANDIDATE%" exit /b 0
"%~1" -c "%CHECK%" >nul 2>&1
if not errorlevel 1 set "PY=%~1"
exit /b 0
:probe_launcher
set "CANDIDATE=%~1"
if /i not "%CANDIDATE:\WindowsApps\=%"=="%CANDIDATE%" exit /b 0
"%~1" -3 -c "%CHECK%" >nul 2>&1
if not errorlevel 1 set "LAUNCHER=%~1"
exit /b 0
