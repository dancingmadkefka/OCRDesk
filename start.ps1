# Start the OCR Benchmark server
# Double-click this file from File Explorer.
# It will open a console window (you can minimize it immediately). 
# Close the window when you want to stop the server.

$ErrorActionPreference = "SilentlyContinue"
Set-Location $PSScriptRoot

Write-Host "=== OCR Benchmark starter ==="
Write-Host "Killing any previous server on port 8877 (so we get the latest code)..."

# Kill only listeners on the exact local port (netstat is reliable)
netstat -ano | findstr ":8877" | ForEach-Object {
    $parts = ($_ -split '\s+') | Where-Object { $_ }
    if ($parts.Count -lt 5 -or $parts[-2] -ne "LISTENING") { return }
    if ($parts[1] -notmatch '^(127\.0\.0\.1|0\.0\.0\.0|\[::1\]|\[::\]):8877$') { return }
    $portPid = $parts[-1]
    if ($portPid -match '^\d+$') {
        Write-Host "  Killing PID $portPid (port holder)"
        Stop-Process -Id $portPid -Force
    }
}

# Also hunt for python processes that are clearly this app under this repo path.
$repoRoot = (Resolve-Path $PSScriptRoot).Path
Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -like 'python*') -and (
        ($_.CommandLine -like "*$repoRoot*") -and (
            ($_.CommandLine -like '*run.py*') -or
            ($_.CommandLine -like '*backend.server*') -or
            ($_.CommandLine -like '*uvicorn*backend.server*')
        )
    )
} | ForEach-Object {
    Write-Host "  Killing python process $($_.ProcessId)"
    Stop-Process -Id $_.ProcessId -Force
}

Start-Sleep -Milliseconds 600

$env:PATH = "C:\Program Files\nodejs;" + $env:PATH

if (-not (Test-Path "frontend\dist\index.html")) {
    Write-Host "Building frontend..."
    Push-Location frontend
    npm install
    npm run build
    Pop-Location
}

Write-Host ""
Write-Host "Starting fresh server at http://127.0.0.1:8877"
Write-Host "(This window is running the server. Minimize it if you like; close it to stop the server.)"
Write-Host ""

python run.py
