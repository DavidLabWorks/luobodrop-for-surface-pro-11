@echo off
setlocal
set "app=%~dp0build\desktop\app\AirDropDesktop.exe"
if not exist "%app%" (
    echo AirDropDesktop.exe was not found in build\desktop\app.
    exit /b 2
)
start "" "%app%" "%~dp0."
