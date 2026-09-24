# Starts the analysis engine (FastAPI, port 8000) and the workbench UI (Vite, port 5173).
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Start-Process -FilePath "$root\backend\.venv\Scripts\python.exe" -ArgumentList "-m","uvicorn","bdt.app:app","--app-dir","$root\backend","--port","8000" -WorkingDirectory $root
Start-Process -FilePath "npm.cmd" -ArgumentList "--prefix","$root\frontend","run","dev" -WorkingDirectory $root
Start-Sleep -Seconds 4
Start-Process "http://localhost:5173"
