@echo off
cd /d "%~dp0"
echo Starting TransScribe. The first launch downloads Python and the score tools.
".\runtime\uv.exe" run --locked --no-dev --python 3.12 transscribe ui --port 0 %*
if errorlevel 1 pause
