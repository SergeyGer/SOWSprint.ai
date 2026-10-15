<#
    Show what is running, and who holds the ports this project needs.

        .\scripts\status.ps1

    This is the script to reach for when something will not start. On a machine shared with
    other projects, "port already in use" is almost always another project's container, and
    the answer is a name, not a stack trace.
#>
. "$PSScriptRoot\_common.ps1"

Assert-Docker

Write-Head "SOWSprint.ai"
$ours = & docker ps -a --filter "label=com.docker.compose.project=sowsprint" `
                     --format "{{.Names}}|{{.Status}}" 2>$null
if (-not $ours) {
    Write-Info "no containers exist yet — start with .\scripts\start.ps1"
} else {
    foreach ($line in $ours) {
        $parts = $line -split '\|', 2
        if ($parts[1] -like "Up*") {
            Write-Host "  " -NoNewline; Write-Host "OK " -ForegroundColor Green -NoNewline
            Write-Host ("{0,-18} {1}" -f $parts[0], $parts[1])
        } else {
            Write-Host ("  -  {0,-18} {1}" -f $parts[0], $parts[1]) -ForegroundColor DarkGray
        }
    }
}

Write-Head "Ports"
foreach ($port in $script:AllPorts) {
    $holder = Get-PortHolder -Port $port
    if (-not $holder) {
        if (Test-PortFree -Port $port) {
            Write-Host "  " -NoNewline; Write-Host "OK " -ForegroundColor Green -NoNewline
            Write-Host ("{0,-6} free" -f $port)
        } else {
            Write-Host "  " -NoNewline; Write-Host "!  " -ForegroundColor Yellow -NoNewline
            Write-Host ("{0,-6} held outside Docker" -f $port)
        }
    } elseif ($holder.Project -eq "sowsprint") {
        Write-Host "  " -NoNewline; Write-Host "OK " -ForegroundColor Green -NoNewline
        Write-Host ("{0,-6} {1}  (this project)" -f $port, $holder.Name)
    } else {
        Write-Host "  " -NoNewline; Write-Host "X  " -ForegroundColor Red -NoNewline
        Write-Host ("{0,-6} {1}  (project: {2})" -f $port, $holder.Name, $holder.Project)
    }
}

# Every other compose project on the machine, so switching between them is a decision rather
# than a discovery.
$others = & docker ps --format '{{.Label "com.docker.compose.project"}}' 2>$null |
          Where-Object { $_ -and $_ -ne "sowsprint" } | Sort-Object -Unique
if ($others) {
    Write-Head "Other projects running on this machine"
    foreach ($project in $others) {
        $names = & docker ps --filter "label=com.docker.compose.project=$project" --format "{{.Names}}" 2>$null
        Write-Host ("  {0,-24} {1} container(s)" -f $project, $names.Count)
        foreach ($n in $names) { Write-Host "      $n" }
    }
}
