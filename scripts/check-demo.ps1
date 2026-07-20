param([switch]$Full)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Push-Location $root

try {
    $live = Invoke-RestMethod "http://localhost:18080/health/live" -TimeoutSec 5
    $ready = Invoke-RestMethod "http://localhost:18080/health/ready" -TimeoutSec 5
    $web = Invoke-WebRequest "http://localhost:13000" -UseBasicParsing -TimeoutSec 5
    $documents = Invoke-RestMethod "http://localhost:18080/api/documents" -TimeoutSec 10
    $wiki = Invoke-RestMethod "http://localhost:18080/api/wiki/pages" -TimeoutSec 10
    $graphNodes = "skipped (use -Full)"
    $graphEdges = "skipped (use -Full)"
    if ($Full) {
        $graph = Invoke-RestMethod "http://localhost:18080/api/wiki/graph" -TimeoutSec 90
        $graphNodes = @($graph.nodes).Count
        $graphEdges = @($graph.edges).Count
    }

    $deviceRequests = docker inspect --format "{{json .HostConfig.DeviceRequests}}" simulink-assistant-ollama 2>$null
    $gpuMode = $deviceRequests -and $deviceRequests -ne "null" -and $deviceRequests -ne "[]"

    [pscustomobject]@{
        web                 = $web.StatusCode
        api                  = $ready.status
        ollama               = $ready.services.ollama
        qdrant               = $ready.services.qdrant
        sqlite               = $ready.services.sqlite
        gpu_compose          = $gpuMode
        documents            = @($documents).Count
        wiki_pages           = @($wiki).Count
        graph_nodes          = $graphNodes
        graph_edges          = $graphEdges
        application_version  = $live.version
    } | Format-List

    if ($web.StatusCode -ne 200 -or $ready.status -ne "ready") {
        throw "Demo health check failed."
    }
    Write-Host "Demo delivery check passed." -ForegroundColor Green
} finally {
    Pop-Location
}
