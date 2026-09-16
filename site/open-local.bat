@echo off
rem Double-click to open the map with its background map (see serve.ps1).
rem Opening index.html directly works too, but without the background map.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0serve.ps1" %*
if errorlevel 1 pause
