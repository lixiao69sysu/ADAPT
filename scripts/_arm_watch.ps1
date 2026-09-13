# Wait for the ADAPT arm's runner to exit, then produce the pre-registered report
# and the failure-mix attribution, both on disk.
#
# Why a separate waiter: the run is ~6-12 h and the session may be interrupted
# before it lands. This makes the two analysis artifacts exist on disk regardless
# of when anyone looks, and the job's own completion is the notification.
#
# Usage (this shell is Windows PowerShell 5.1; `pwsh` is not on PATH here):
#   & scripts/_arm_watch.ps1
#   & scripts/_arm_watch.ps1 -Arm data/simulations/foo.json -Baseline data/simulations/bar.json

param(
    [string]$Arm = 'data/simulations/adapt8_1t.json',
    [string]$Baseline = 'data/simulations/stock_avg4_8u.json',
    [string]$OutDir = 'data/simulations',
    [int]$PollSeconds = 60
)

$ErrorActionPreference = 'Continue'
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:VITA_MODEL_CONFIG_PATH = (Resolve-Path models_adapt.yaml).Path
$env:VITA_MEMORY_CONFIG_PATH = (Resolve-Path memory_adapt.yaml).Path

# PS 5.1's `-Encoding UTF8` writes a BOM, which breaks json.load on the .json
# artifact. Write BOM-less UTF-8 explicitly instead.
function Write-Utf8NoBom {
    param([string]$Path, [string]$Text)
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Text, $enc)
}

function Get-RunnerProcess {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like '*vitabench_runner*' }
}

$started = Get-Date
Write-Host "[watch] $(Get-Date -Format 'HH:mm:ss') waiting for the runner to exit"

while (Get-RunnerProcess) {
    Start-Sleep -Seconds $PollSeconds
}

# Give the final atomic checkpoint write a moment to land.
Start-Sleep -Seconds 10
$elapsed = ((Get-Date) - $started).TotalMinutes
Write-Host "[watch] $(Get-Date -Format 'HH:mm:ss') runner gone after $([math]::Round($elapsed,1)) min of waiting"

if (-not (Test-Path $Arm)) {
    Write-Host "[watch] ABORT: $Arm was never written"
    exit 3
}

# How many users actually landed? An exception in one user only skips that user
# (vitabench_runner.py:531), so an incomplete checkpoint is a real possibility and
# must be visible here rather than inferred later.
python -c @"
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
sims = d.get('simulations') or []
print('[watch] users completed: %d / %d' % (len({s['task_id'] for s in sims}), len(d.get('tasks') or [])))
print('[watch] tasks field:', d.get('tasks'))
print('[watch] evaluation_status:', {s.get('evaluation_status') for s in sims})
"@ $Arm

$report = Join-Path $OutDir 'adapt8_1t_report.txt'
Write-Host "[watch] writing $report"
$text = python scripts/adapt_arm_report.py --arm $Arm --baseline $Baseline 2>&1 | Out-String
$code = $LASTEXITCODE
Write-Utf8NoBom -Path $report -Text $text
Write-Host "[watch] arm report exit=$code"

$attrib = Join-Path $OutDir 'adapt8_1t_attribution.json'
Write-Host "[watch] writing $attrib"
$json = python scripts/mechanism_attribution.py $Arm --json 2>$null | Out-String
Write-Utf8NoBom -Path $attrib -Text $json
Write-Host "[watch] attribution exit=$LASTEXITCODE"

Write-Host "[watch] done; artifacts:"
Get-ChildItem $report, $attrib -ErrorAction SilentlyContinue |
    Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize
