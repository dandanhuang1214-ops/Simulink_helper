$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Push-Location $root

try {
    $container = "simulink-assistant-api"
    $running = docker inspect --format "{{.State.Running}}" $container 2>$null
    if ($LASTEXITCODE -ne 0 -or $running -ne "true") {
        throw "API container is not running. Start the Demo before creating a backup."
    }
    docker cp "scripts/create_demo_backup.py" "${container}:/tmp/create_demo_backup.py"
    if ($LASTEXITCODE -ne 0) { throw "Could not copy backup helper into the API container." }
    docker exec -w /app $container python /tmp/create_demo_backup.py
    if ($LASTEXITCODE -ne 0) { throw "Backup failed." }
    Write-Host "Backup completed under knowledge\backups." -ForegroundColor Green
    Write-Host "Copy knowledge\raw separately if you need protection against host disk loss."
} finally {
    Pop-Location
}
