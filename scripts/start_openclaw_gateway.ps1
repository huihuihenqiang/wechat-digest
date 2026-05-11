$ErrorActionPreference = "Stop"

$openclaw = Join-Path $env:APPDATA "npm\openclaw.cmd"
if (-not (Test-Path -LiteralPath $openclaw)) {
    throw "OpenClaw command not found: $openclaw"
}

$logDir = Join-Path $env:LOCALAPPDATA "Temp\openclaw"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# Remove stale lock files left behind by crashed gateway processes.
Get-ChildItem -LiteralPath $logDir -Filter "gateway.*.lock" -ErrorAction SilentlyContinue | ForEach-Object {
    try {
        $lockData = Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json
        $lockPid = [int]$lockData.pid
        if ($lockPid -gt 0) {
            $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $lockPid" -ErrorAction SilentlyContinue
            if (-not $proc) {
                Write-Host "Removing stale lock file (PID $lockPid no longer exists): $($_.Name)"
                Remove-Item -LiteralPath $_.FullName -Force
            } elseif ($proc.CommandLine -notmatch "openclaw") {
                Write-Host "Removing lock file for unrelated process (PID $lockPid): $($_.Name)"
                Remove-Item -LiteralPath $_.FullName -Force
            }
        } else {
            Write-Host "Removing lock file with invalid PID: $($_.Name)"
            Remove-Item -LiteralPath $_.FullName -Force
        }
    } catch {
        Write-Host "Removing unparseable lock file: $($_.Name)"
        Remove-Item -LiteralPath $_.FullName -Force
    }
}

# Check if gateway is already listening.
$existing = Get-NetTCPConnection -LocalPort 18789 -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "OpenClaw Gateway is already listening on port 18789 (PID $($existing.OwningProcess))."
    exit 0
}

# Kill zombie processes that hold port 18789 but aren't actually listening.
$zombies = Get-NetTCPConnection -LocalPort 18789 -ErrorAction SilentlyContinue | Where-Object { $_.State -ne "Listen" }
foreach ($zombie in $zombies) {
    $zombieProc = Get-CimInstance Win32_Process -Filter "ProcessId = $($zombie.OwningProcess)" -ErrorAction SilentlyContinue
    if ($zombieProc -and $zombieProc.CommandLine -match "openclaw") {
        Write-Host "Killing zombie OpenClaw process on port 18789 (PID $($zombie.OwningProcess), state=$($zombie.State))."
        Stop-Process -Id $zombie.OwningProcess -Force
    }
}

$stdout = Join-Path $logDir "openclaw-gateway-task.out.log"
$stderr = Join-Path $logDir "openclaw-gateway-task.err.log"

Write-Host "Starting OpenClaw Gateway..."
Start-Process `
    -FilePath $openclaw `
    -ArgumentList @("gateway", "run") `
    -WorkingDirectory (Split-Path -Parent $openclaw) `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr

$deadline = (Get-Date).AddSeconds(45)
do {
    Start-Sleep -Milliseconds 500
    $listener = Get-NetTCPConnection -LocalPort 18789 -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        Write-Host "OpenClaw Gateway started successfully (PID $($listener.OwningProcess))."
        exit 0
    }
} while ((Get-Date) -lt $deadline)

$errTail = ""
if (Test-Path -LiteralPath $stderr) {
    $errTail = (Get-Content -LiteralPath $stderr -Tail 20 | Out-String).Trim()
}
if (-not $errTail -and (Test-Path -LiteralPath $stdout)) {
    $errTail = (Get-Content -LiteralPath $stdout -Tail 20 | Out-String).Trim()
}
if ($errTail) {
    Write-Error "OpenClaw Gateway did not start listening on port 18789 within 45 seconds. Recent log:`n$errTail"
} else {
    Write-Error "OpenClaw Gateway did not start listening on port 18789 within 45 seconds. See $stderr"
}
