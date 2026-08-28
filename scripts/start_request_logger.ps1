param(
    [switch]$App,
    [switch]$Force,
    [switch]$Update,
    [ValidateRange(1, 65535)]
    [int]$Port = 8787
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$toolsRoot = Join-Path $projectRoot ".tools"
$sourceRoot = Join-Path $toolsRoot "request-logger-source"
$proxyPath = Join-Path $sourceRoot "request-logger\proxy.ts"
$upstream = "https://github.com/ai-hero-dev/ai-coding-crash-course.git"

if (-not (Test-Path -LiteralPath $proxyPath)) {
    New-Item -ItemType Directory -Path $toolsRoot -Force | Out-Null
    Write-Host "[setup] Downloading request-logger from $upstream"
    & git clone --depth 1 $upstream $sourceRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Could not download the request-logger repository."
    }
}

if ($Update) {
    Write-Host "[setup] Updating request-logger"
    & git -C $sourceRoot pull --ff-only
    if ($LASTEXITCODE -ne 0) {
        throw "Could not update request-logger."
    }
}

if ($App) {
    $choicePath = Join-Path $sourceRoot "request-logger\.agent-choice.json"
    $choice = @{ agent = "codex"; provider = "openai" } | ConvertTo-Json
    [System.IO.File]::WriteAllText($choicePath, $choice, [System.Text.UTF8Encoding]::new($false))
    Write-Host "[setup] Logging OpenAI API requests from the SCL backend."
}

$tsx = Join-Path $projectRoot "node_modules\.bin\tsx.cmd"
if (-not (Test-Path -LiteralPath $tsx)) {
    throw "Run 'npm install' in the project root first."
}

$env:PORT = [string]$Port
$proxyArgs = @($proxyPath)
if ($Force) {
    $proxyArgs += "--force"
}

& $tsx @proxyArgs
exit $LASTEXITCODE
