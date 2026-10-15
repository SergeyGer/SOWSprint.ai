<#
    Start the SOWSprint.ai stack on Windows.

        .\scripts\start.ps1              start and wait until the app answers
        .\scripts\start.ps1 -Build       rebuild the image first (after changing src\)
        .\scripts\start.ps1 -NoWait      return as soon as the containers are created

    Deliberately not automatic. The containers are created with `restart: "no"`, so a reboot
    leaves them stopped and this is the one command that brings them back. That matters on a
    shared machine: the stack holds 8000, 6333 and 6334, and a project that starts itself is
    a project that breaks another one.

    Safe to run twice. If the stack is already up it says so and does nothing.

    If PowerShell refuses to run the script, either unblock it once —

        Unblock-File .\scripts\start.ps1

    — or run it in a session started with:

        powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
#>
param(
    [switch]$Build,
    [switch]$NoWait
)

. "$PSScriptRoot\_common.ps1"

Write-Head "Starting SOWSprint.ai"
Assert-Docker

if (-not (Test-Path (Join-Path $ProjectRoot "docker-compose.yml"))) {
    Write-Bad "docker-compose.yml not found — run this from the project directory."
    exit 1
}
if (-not (Test-Path (Join-Path $ProjectRoot ".env"))) {
    Write-Warn2 ".env is missing; the app will start with defaults and no API keys."
    Write-Info "copy .env.example to .env and fill it in if you need live integrations."
}

$running = & docker ps --filter "name=sowsprint-app" --format "{{.Names}}" 2>$null
if ($running -and -not $Build) {
    Write-Ok "already running"
    Write-ProjectSummary
    exit 0
}

# Ports are checked before anything is created, so a conflict costs nothing to resolve
# rather than leaving a half-started stack and a bind error in the logs.
Write-Head "Checking ports"
if (-not (Assert-PortsAvailable)) {
    Write-Host ""
    Write-Bad "Refusing to start: another project holds a port this stack needs."
    Write-Say "     Stop that project first, or change SOWSPRINT_HOST_PORT in .env."
    Write-Say "     To see what is running:  .\scripts\status.ps1"
    exit 1
}
Write-Ok "ports $($script:AllPorts -join ', ') are free"

if ($Build) {
    Write-Head "Building the image"
    Write-Info "src\ is copied into the image, so code changes need a rebuild."
    $null = Invoke-Compose build
}

Write-Head "Starting services"
$null = Invoke-Compose up -d

if (-not $NoWait) {
    Write-Head "Waiting for the application"
    $null = Wait-ForHealth -TimeoutSeconds 240
}

Write-ProjectSummary
