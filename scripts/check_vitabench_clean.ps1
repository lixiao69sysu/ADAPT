$ErrorActionPreference = "Stop"

git -C evaluation/vitabench diff --exit-code HEAD -- src/vita
if ($LASTEXITCODE -ne 0) {
    throw "VitaBench src/vita contains tracked modifications."
}

Write-Host "VitaBench tracked source is pristine."
