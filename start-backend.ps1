$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$UnifiedRoot = Join-Path (Split-Path -Parent $ProjectRoot) "unified-collection-report"
$env:PYTHONPATH = $UnifiedRoot
python (Join-Path $ProjectRoot "backend\server.py")
