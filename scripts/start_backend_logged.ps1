param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [ValidateRange(1, 65535)]
    [int]$LoggerPort = 8787
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw ".venv is missing. Complete the backend setup steps in README first."
}

$env:PYTHONPATH = Join-Path $projectRoot "backend"
$env:OPENAI_BASE_URL = "http://127.0.0.1:$LoggerPort/v1"

Write-Host "[backend] OpenAI requests -> $env:OPENAI_BASE_URL"
& $python -m uvicorn app.main:app --reload --port $Port
exit $LASTEXITCODE
