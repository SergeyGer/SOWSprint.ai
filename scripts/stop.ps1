<#
    Stop the SOWSprint.ai stack on Windows.

        .\scripts\stop.ps1              stop and remove this project's containers
        .\scripts\stop.ps1 -Keep        stop without removing (starts faster next time)
        .\scripts\stop.ps1 -All         also stop other containers holding our ports

    Only this project's containers are touched. `docker compose down` is scoped to the compose
    project in docker-compose.yml (name: sowsprint), so another project's services are not
    affected — which is the point when several of them share a laptop.

    Named volumes are kept. Stopping is not deleting: the Qdrant index and the generated
    artefacts survive, so the next start is seconds rather than a full re-ingest.
#>
param(
    [switch]$Keep,
    [switch]$All
)

. "$PSScriptRoot\_common.ps1"

Write-Head "Stopping SOWSprint.ai"
Assert-Docker

$ours = & docker ps --filter "label=com.docker.compose.project=sowsprint" --format "{{.Names}}" 2>$null
if (-not $ours) {
    Write-Ok "nothing of this project is running"
} else {
    Write-Say "  stopping: $($ours -join ' ')"
    if ($Keep) { $null = Invoke-Compose stop } else { $null = Invoke-Compose down --remove-orphans }
    Write-Ok "stopped"
}

# Anything else still holding the ports. Reported, never stopped without -All: a container
# belonging to another project is that project's business.
$leftovers = @()
foreach ($port in $script:AllPorts) {
    $holder = Get-PortHolder -Port $port
    if ($holder) { $leftovers += [pscustomobject]@{ Port = $port; Name = $holder.Name } }
}

if ($leftovers.Count -gt 0) {
    Write-Host ""
    Write-Warn2 "these ports are still held by other containers:"
    foreach ($item in $leftovers) {
        Write-Say ("       port {0,-12} {1}" -f $item.Port, $item.Name)
    }
    if ($All) {
        Write-Host ""
        foreach ($item in $leftovers) {
            Write-Info "stopping $($item.Name)"
            $null = & docker stop $item.Name 2>$null
        }
        Write-Ok "done"
    } else {
        Write-Info "leave them alone:   .\scripts\stop.ps1        (they belong to other projects)"
        Write-Info "or stop them too:   .\scripts\stop.ps1 -All"
    }
} else {
    Write-Ok "ports $($script:AllPorts -join ', ') are free"
}

Write-Host ""
Write-Info "the Qdrant index and generated artefacts were kept"
Write-Info "to remove them as well:  docker compose down -v"
