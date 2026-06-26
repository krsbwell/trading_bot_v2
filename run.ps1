# Trading Bot launcher — auto-restarts on crash, max 50 attempts
# Usage:  .\run.ps1
# Stop cleanly with Ctrl+C (exit code 0 = no restart)
param([int]$MaxRestarts = 50, [int]$RestartDelaySecs = 15)

$python  = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
$script  = Join-Path $PSScriptRoot "main.py"
$attempt = 0

while ($attempt -lt $MaxRestarts) {
    $attempt++
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Write-Host "$ts  [run.ps1] Starting bot — attempt $attempt / $MaxRestarts" -ForegroundColor Cyan
    & $python $script
    $exit = $LASTEXITCODE
    if ($exit -eq 0) {
        Write-Host "$(Get-Date -Format 'HH:mm:ss')  [run.ps1] Bot exited cleanly (code 0). Not restarting." -ForegroundColor Green
        break
    }
    Write-Host "$(Get-Date -Format 'HH:mm:ss')  [run.ps1] Bot exited with code $exit — restarting in ${RestartDelaySecs}s ..." -ForegroundColor Yellow
    Start-Sleep -Seconds $RestartDelaySecs
}
if ($attempt -ge $MaxRestarts) {
    Write-Host "$(Get-Date -Format 'HH:mm:ss')  [run.ps1] Max restarts ($MaxRestarts) reached. Manual intervention required." -ForegroundColor Red
}
