# Starts the analysis engine (port 8000) and the workbench UI (port 5173), then opens the browser.
#   ./start.ps1         this PC only
#   ./start.ps1 -Lan    also reachable from other PCs on your network (view only: BMS writes stay local)
param([switch]$Lan)
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($Lan) { $env:BDT_LAN = "1" }   # engine stays on 127.0.0.1; other PCs go through the UI server
Start-Process -FilePath "node" -ArgumentList "`"$root\scripts\engine.mjs`"" -WorkingDirectory $root
Start-Process -FilePath "npm.cmd" -ArgumentList "--prefix","`"$root\frontend`"","run","dev" -WorkingDirectory $root
Start-Sleep -Seconds 4
Start-Process "http://localhost:5173"
if ($Lan) {
  $ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.PrefixOrigin -in 'Dhcp','Manual' -and $_.IPAddress -notlike '169.*' } | Select-Object -First 1).IPAddress
  Write-Host "Other PCs on your network: http://${ip}:5173  (Windows Firewall may ask to allow Node.js)"
}
