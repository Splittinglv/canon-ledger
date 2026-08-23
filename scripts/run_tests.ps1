param(
    [ValidateSet("smoke", "full")]
    [string]$Mode = "smoke",
    [string]$ProjectRoot = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
} else {
    $ProjectRoot = (Resolve-Path $ProjectRoot).Path
}

$testPython = "python"
if (-not [string]::IsNullOrWhiteSpace($env:CANON_LEDGER_TEST_PYTHON)) {
    $testPython = $env:CANON_LEDGER_TEST_PYTHON
}

& $testPython (Join-Path $PSScriptRoot "run_acceptance.py") `
    --mode $Mode `
    --project-root $ProjectRoot
exit $LASTEXITCODE
