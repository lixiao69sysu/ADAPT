# Per-user progress watcher: every time another user lands in the arm
# checkpoint, run the early look and print the comparison, until the runner is
# gone. This is what reports the second, third, ... user without anyone having to
# poll by hand.
#
# This shell is Windows PowerShell 5.1 (`pwsh` is not on PATH).
#
# Usage:
#   & scripts/_arm_progress_watch.ps1
#   & scripts/_arm_progress_watch.ps1 -Arm data/simulations/foo.json

param(
    [string]$Arm = 'data/simulations/adapt8_1t.json',
    [string]$Baseline = 'data/simulations/stock_avg4_8u.json',
    [int]$PollSeconds = 45
)

$ErrorActionPreference = 'Continue'
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Get-RunnerProcess {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like '*vitabench_runner*' }
}

function Get-Progress {
    if (-not (Test-Path $Arm)) { return -1 }
    $n = python -c "import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8')); print(len(d.get('simulations') or []))" $Arm 2>$null
    if ($LASTEXITCODE -ne 0) { return -1 }
    return [int]($n | Select-Object -Last 1)
}

$last = -1
Write-Host "[progress] $(Get-Date -Format 'HH:mm:ss') watching $Arm"
while (Get-RunnerProcess) {
    $now = Get-Progress
    if ($now -gt $last) {
        Write-Host ""
        Write-Host ("=" * 78)
        Write-Host "[progress] $(Get-Date -Format 'HH:mm:ss')  users written: $now"
        Write-Host ("=" * 78)
        python scripts/arm_early_look.py --arm $Arm --baseline $Baseline
        $last = $now
    }
    Start-Sleep -Seconds $PollSeconds
}

Write-Host ""
Write-Host "[progress] $(Get-Date -Format 'HH:mm:ss') runner gone; final state: $(Get-Progress) users"
python scripts/arm_early_look.py --arm $Arm --baseline $Baseline
