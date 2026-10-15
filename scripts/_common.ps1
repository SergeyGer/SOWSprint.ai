<#
    Shared helpers for the Windows start / stop / status scripts.

    Dot-sourced, never run directly. The logic mirrors scripts/_common.sh deliberately: the
    same person switches between a Windows laptop and a Linux server, and two implementations
    that disagree about whether a port is free would be worse than having only one platform.

    Requires PowerShell 5.1 (shipped with Windows 10/11) or PowerShell 7+.
#>

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

# ---------------------------------------------------------------------------- output
function Write-Say   { param([string]$Text) Write-Host $Text }
function Write-Head  { param([string]$Text) Write-Host ""; Write-Host $Text -ForegroundColor White }
function Write-Ok    { param([string]$Text) Write-Host "  " -NoNewline; Write-Host "OK " -ForegroundColor Green -NoNewline; Write-Host $Text }
function Write-Warn2 { param([string]$Text) Write-Host "  " -NoNewline; Write-Host "!  " -ForegroundColor Yellow -NoNewline; Write-Host $Text }
function Write-Bad   { param([string]$Text) Write-Host "  " -NoNewline; Write-Host "X  " -ForegroundColor Red -NoNewline; Write-Host $Text }
function Write-Info  { param([string]$Text) Write-Host "  -  $Text" -ForegroundColor DarkGray }

# ---------------------------------------------------------------------------- compose
# `docker compose` (v2, a plugin) is what this project targets. `docker-compose` (v1) is
# accepted so an older Docker Desktop still works.
function Invoke-Compose {
    # `$Args` would shadow PowerShell's automatic argument array; `$ComposeArgs` does not.
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$ComposeArgs)
    $v2 = $null
    try { $null = & docker compose version 2>$null; $v2 = $true } catch { $v2 = $false }
    if ($v2) { & docker compose @ComposeArgs; return $LASTEXITCODE }
    if (Get-Command docker-compose -ErrorAction SilentlyContinue) {
        & docker-compose @ComposeArgs; return $LASTEXITCODE
    }
    Write-Bad "Neither 'docker compose' nor 'docker-compose' is available."
    Write-Say "     Install Docker Desktop: https://www.docker.com/products/docker-desktop/"
    exit 1
}

function Assert-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Bad "The 'docker' command was not found."
        Write-Say "     Install Docker Desktop: https://www.docker.com/products/docker-desktop/"
        exit 1
    }
    # Docker Desktop starts its engine lazily; on a cold laptop the first `docker info` can
    # take a few seconds and a single failure is not evidence of a problem.
    $ready = $false
    for ($i = 0; $i -lt 12; $i++) {
        try { $null = & docker info 2>$null; if ($LASTEXITCODE -eq 0) { $ready = $true; break } } catch { }
        if ($i -eq 0) { Write-Info "waiting for the Docker engine…" }
        Start-Sleep -Seconds 5
    }
    if (-not $ready) {
        Write-Bad "Docker is installed but not responding."
        Write-Say "     Start Docker Desktop and wait until the whale stops animating."
        exit 1
    }
}

# The published host ports, read from .env the way compose reads it.
function Get-HostPort {
    param([string]$Key, [string]$Default)
    $path = Join-Path $ProjectRoot ".env"
    if (Test-Path $path) {
        $line = Get-Content $path -ErrorAction SilentlyContinue |
                Where-Object { $_ -match "^$Key=" } | Select-Object -Last 1
        if ($line) {
            $value = ($line -split "=", 2)[1].Trim().Trim('"').Trim("'")
            if ($value) { return $value }
        }
    }
    return $Default
}

$script:AppPort         = Get-HostPort "SOWSPRINT_HOST_PORT" "8000"
$script:QdrantHttpPort  = Get-HostPort "SOWSPRINT_QDRANT_HTTP_PORT" "6333"
$script:QdrantGrpcPort  = Get-HostPort "SOWSPRINT_QDRANT_GRPC_PORT" "6334"
$script:AllPorts        = @($script:AppPort, $script:QdrantHttpPort, $script:QdrantGrpcPort)

# ---------------------------------------------------------------------------- ports
function Get-PortHolder {
    <#
        Which container publishes this port on the host, if any.

        Only mappings with "->" count. A bare "8000/tcp" is a port the container EXPOSES to
        the compose network and is not bound on the host — treating it as taken made the
        Linux script refuse to start over a container that was not in the way.
    #>
    param([string]$Port)
    $lines = & docker ps --format '{{.Names}}|{{.Label "com.docker.compose.project"}}|{{.Ports}}' 2>$null
    foreach ($line in $lines) {
        $parts = $line -split '\|', 3
        if ($parts.Count -lt 3) { continue }
        foreach ($mapping in ($parts[2] -split ', ')) {
            if ($mapping -notmatch '->') { continue }
            # NOT `$host`: that is a read-only automatic variable in PowerShell, and
            # assigning to it throws at runtime. The script parsed cleanly and would have
            # failed on the first `status.ps1` a Windows user ran.
            $bindPort = ($mapping -split '->')[0]
            $bindPort = ($bindPort -split ':')[-1]   # strip the bind address
            if ($bindPort -match '^(\d+)-(\d+)$') {
                if ([int]$Port -ge [int]$Matches[1] -and [int]$Port -le [int]$Matches[2]) {
                    return @{ Name = $parts[0]; Project = $parts[1] }
                }
            } elseif ($bindPort -eq $Port) {
                return @{ Name = $parts[0]; Project = $parts[1] }
            }
        }
    }
    return $null
}

function Test-PortFree {
    param([string]$Port)
    if (Get-PortHolder -Port $Port) { return $false }
    # Something outside Docker may hold it: a local service, a tunnel, another tool.
    try {
        $conn = Get-NetTCPConnection -LocalPort ([int]$Port) -State Listen -ErrorAction SilentlyContinue
        if ($conn) { return $false }
    } catch { }
    return $true
}

function Assert-PortsAvailable {
    $conflicts = 0
    foreach ($port in $script:AllPorts) {
        $holder = Get-PortHolder -Port $port
        if ($holder) {
            if ($holder.Project -eq "sowsprint") {
                Write-Info "port $port is held by this project ($($holder.Name))"
            } else {
                Write-Bad "port $port is held by container '$($holder.Name)' of project '$($holder.Project)'"
                $conflicts++
            }
        } elseif (-not (Test-PortFree -Port $port)) {
            $owner = $null
            try {
                $conn = Get-NetTCPConnection -LocalPort ([int]$port) -State Listen -ErrorAction SilentlyContinue |
                        Select-Object -First 1
                if ($conn) { $owner = (Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue).ProcessName }
            } catch { }
            Write-Bad "port $port is held outside Docker$(if ($owner) { " by '$owner'" })"
            $conflicts++
        }
    }
    return ($conflicts -eq 0)
}

# ---------------------------------------------------------------------------- health
function Wait-ForHealth {
    param([int]$TimeoutSeconds = 240)
    $waited = 0
    while ($waited -lt $TimeoutSeconds) {
        $status = "missing"
        try {
            $status = (& docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' sowsprint-app 2>$null)
        } catch { }
        switch ($status) {
            "healthy"   { Write-Ok "application is healthy"; return $true }
            "missing"   { Write-Bad "container sowsprint-app is not present"; return $false }
            "unhealthy" { Write-Bad "application reported unhealthy"; return $false }
        }
        Start-Sleep -Seconds 5
        $waited += 5
        if ($waited % 30 -eq 0) { Write-Info "waiting for the application… ${waited}s" }
    }
    Write-Warn2 "still '$status' after ${TimeoutSeconds}s — check: docker compose logs sowsprint-app"
    return $false
}

function Write-ProjectSummary {
    Write-Head "SOWSprint.ai"
    Write-Host "  Web interface   " -NoNewline; Write-Host "http://localhost:$script:AppPort" -ForegroundColor Cyan
    Write-Host "  Qdrant          " -NoNewline; Write-Host "http://localhost:$script:QdrantHttpPort" -ForegroundColor Cyan -NoNewline
    Write-Host "  (dashboard)"
    Write-Info "Logs            docker compose logs -f sowsprint-app"
}
