param(
    [switch]$Cpu,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Push-Location $root

try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker CLI was not found. Install and start Docker Desktop first."
    }

    docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Engine is not ready. Start Docker Desktop first."
    }

    $compose = @("compose", "-f", "compose.yaml")
    if (-not $Cpu) {
        $compose += @("-f", "compose.gpu.yaml")
        Write-Host "Mode: NVIDIA GPU" -ForegroundColor Cyan
    } else {
        Write-Host "Mode: CPU (generation will be slower)" -ForegroundColor Yellow
    }

    & docker @compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw "Compose configuration validation failed." }

    $up = @("up", "-d")
    if (-not $SkipBuild) { $up += "--build" }
    & docker @compose @up
    if ($LASTEXITCODE -ne 0) { throw "Container startup failed." }

    $deadline = (Get-Date).AddSeconds(120)
    $ready = $null
    while ((Get-Date) -lt $deadline) {
        try {
            $ready = Invoke-RestMethod "http://localhost:18080/health/ready" -TimeoutSec 4
            if ($ready.status -eq "ready") { break }
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    if (-not $ready -or $ready.status -ne "ready") {
        throw "API did not become ready within 120 seconds. Run scripts\check-demo.ps1."
    }

    $models = docker exec simulink-assistant-ollama ollama list 2>$null
    $modelText = $models -join "`n"
    if ($modelText -notmatch "qwen3\.5:2b") { Write-Warning "Chat model qwen3.5:2b is missing." }
    if ($modelText -notmatch "qwen3-embedding:0\.6b") { Write-Warning "Embedding model qwen3-embedding:0.6b is missing." }

    Write-Host "Demo is ready" -ForegroundColor Green
    Write-Host "Web: http://localhost:13000"
    Write-Host "API: http://localhost:18080/docs"
} finally {
    Pop-Location
}
