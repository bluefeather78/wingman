# Restart the wingman dev server safely on Windows.
#
# Why this exists: an earlier session accumulated 26 zombie `server.py` processes because
# restarts were done with Bash `python server.py &` + `pkill -f "python server.py"`. Two
# things go wrong there:
#
#   1. Git Bash's pkill does not see native Windows python processes, so the kill silently
#      matched nothing and the old process kept holding port 8000. Every "restart" appeared
#      to succeed while the browser kept being served stale code by the original process.
#   2. The WindowsApps `python.exe` is an App Execution Alias: it launches a CHILD process
#      that actually binds the socket. Killing the PID that Start-Process returns kills the
#      shim and leaves the real listener running.
#
# So: kill by whoever actually OWNS port 8000 (via Get-NetTCPConnection), verify the port is
# free before starting, and record the resulting listener's PID — not the launcher's.

param([int]$Port = 8000)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $root ".server.pid"

function Get-ListenerPids([int]$p) {
    # Cast to [int]: Get-NetTCPConnection yields OwningProcess as UInt32, which does not
    # bind cleanly to Get-Process/Stop-Process -Id and made an earlier version of this
    # script silently skip its own kill loop.
    @(Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess) |
        Sort-Object -Unique | ForEach-Object { [int]$_ }
}

# --- stop whatever currently owns the port (authoritative), plus any recorded PID ---
# The @() is load-bearing: a single listener makes Get-ListenerPids return a scalar int,
# and `$targets += [int]$recorded` would then be integer ADDITION rather than an append —
# producing a nonsense PID that kills nothing while the real server keeps the port.
$targets = @(Get-ListenerPids $Port)
if (Test-Path $pidFile) {
    $recorded = (Get-Content $pidFile -Raw).Trim()
    if ($recorded -match '^\d+$') { $targets += [int]$recorded }
}
foreach ($procId in ($targets | Sort-Object -Unique)) {
    try {
        Stop-Process -Id ([int]$procId) -Force -ErrorAction Stop
        Write-Output "Stopped PID $procId"
    } catch {
        # Already gone is the common, harmless case (e.g. the recorded PID from a prior
        # run). Anything else is worth seeing rather than swallowing.
        if (Get-Process -Id ([int]$procId) -ErrorAction SilentlyContinue) {
            Write-Output "Could not stop PID ${procId}: $($_.Exception.Message)"
        }
    }
}

# --- confirm the port is actually free before starting anything ---
# Give this a generous window: the listening socket can linger for a few seconds after the
# owning process exits, and starting early is exactly how the zombie pile-up happened.
for ($i = 0; $i -lt 40; $i++) {
    if (-not (Get-ListenerPids $Port)) { break }
    Start-Sleep -Milliseconds 250
}
if (Get-ListenerPids $Port) {
    Write-Error "Port $Port is still bound by PID(s): $((Get-ListenerPids $Port) -join ', '). Not starting a second instance."
    exit 1
}

# --- start, then record the PID that genuinely holds the socket ---
Start-Process -FilePath "python" -ArgumentList "server.py" -WorkingDirectory $root `
    -RedirectStandardOutput (Join-Path $root "server.log") `
    -RedirectStandardError  (Join-Path $root "server_err.log") `
    -WindowStyle Hidden | Out-Null

$listener = $null
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 400
    $listener = (Get-ListenerPids $Port | Select-Object -First 1)
    if ($listener) { break }
}
if (-not $listener) {
    Write-Error "Server did not start listening on $Port. Check server_err.log."
    exit 1
}

$listener | Out-File -FilePath $pidFile -Encoding ascii -NoNewline
Write-Host "Server listening on port $Port (PID $listener, recorded in .server.pid)"
