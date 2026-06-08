$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot

# Start backend
$backend = Start-Process -FilePath "powershell.exe" `
    -ArgumentList "-NoExit", "-Command", "& '$(Join-Path $ProjectRoot start-backend.ps1)'" `
    -WorkingDirectory $ProjectRoot -PassThru

# Start frontend
$frontend = Start-Process -FilePath "powershell.exe" `
    -ArgumentList "-NoExit", "-Command", "npm run dev" `
    -WorkingDirectory $ProjectRoot -PassThru

Write-Host "Both services started. Press Ctrl+C here to stop them."
Write-Host "Backend PID: $($backend.Id), Frontend PID: $($frontend.Id)"

# Wait for Ctrl+C to terminate both
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")

Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
Stop-Process -Id $frontend.Id -Force -ErrorAction SilentlyContinue
Write-Host "Stopped both services."
