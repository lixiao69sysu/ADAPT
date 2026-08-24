$ErrorActionPreference = "Stop"

$env:VITA_MODEL_CONFIG_PATH = (Resolve-Path models_adapt.yaml).Path
$env:VITA_MEMORY_CONFIG_PATH = (Resolve-Path memory_adapt.yaml).Path

python -m pytest agent/tests -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python -m compileall -q agent
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& "$PSScriptRoot/check_vitabench_clean.ps1"
