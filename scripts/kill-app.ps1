# Stop the BoardClip instance(s) belonging to THIS checkout, and nothing else.
# Called by kill.bat (so also by start.bat and update.bat).
#
# Two paths, exact first:
#  1. boardclip.pid - written by main.js right after it takes the single-instance
#     lock. Pure Get-Process (no WMI): the pid must still point at this checkout's
#     electron.exe and its start time must match the record, so a recycled pid can
#     never hit another process.
#  2. A WMI sweep for any other instance of this checkout (one that predates the
#     pid file, or a duplicate), run in a background job with a wall-clock cap.
#     A wedged winmgmt (2026-09-07: a machine-wide memory crunch) made every
#     Get-CimInstance hang forever, and with it kill.bat, start.bat and the user's
#     attempts to reopen the app. Now the sweep is skipped after the cap and the
#     pid-file path has already done the real work.
#
# The MCP helper (same electron.exe, `boardclip-mcp.js` on its command line) is
# never touched: it is spawned and owned by an AI client, which cannot re-spawn
# a stdio child that dies after connecting.
param(
  # kill.bat passes the dir as BOARDCLIP_APP_DIR: a "%~dp0" argument ends in a
  # backslash that escapes the closing quote on PowerShell's command line.
  [string]$AppDir = $env:BOARDCLIP_APP_DIR,
  [int]$SweepTimeoutSec = 8
)
$ErrorActionPreference = 'SilentlyContinue'
if (-not $AppDir) { Write-Host 'ERROR: kill-app.ps1 needs -AppDir or BOARDCLIP_APP_DIR'; exit 1 }
$appDir = [IO.Path]::GetFullPath($AppDir)
$target = [IO.Path]::GetFullPath((Join-Path $appDir 'node_modules\electron\dist\electron.exe'))
$pidFile = Join-Path $appDir 'boardclip.pid'

function Test-Ours($proc) {
  if (-not $proc) { return $false }
  try { return ($proc.Path -and ([IO.Path]::GetFullPath($proc.Path) -ieq $target)) } catch { return $false }
}
function Wait-Gone($ids, $seconds) {
  $deadline = (Get-Date).AddSeconds($seconds)
  while ((Get-Date) -lt $deadline) {
    $alive = @($ids | ForEach-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
    if ($alive.Count -eq 0) { return $true }
    Start-Sleep -Milliseconds 300
  }
  return $false
}

$killed = @()

# ---- 1. pid file (exact, WMI-free) ----
if (Test-Path $pidFile) {
  $rec = $null
  try { $rec = Get-Content $pidFile -Raw | ConvertFrom-Json } catch { $rec = $null }
  if ($rec -and $rec.pid) {
    $p = Get-Process -Id ([int]$rec.pid) -ErrorAction SilentlyContinue
    $startOk = $true
    if ($p -and $rec.startedAt) {
      try {
        $recorded = [DateTimeOffset]::FromUnixTimeMilliseconds([long]$rec.startedAt).LocalDateTime
        $startOk = [math]::Abs(($p.StartTime - $recorded).TotalSeconds) -lt 120
      } catch { $startOk = $false }
    }
    if ((Test-Ours $p) -and $startOk) {
      Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
      $killed += $p.Id
    }
  }
}

# ---- 2. WMI sweep with a wall-clock cap ----
if ($SweepTimeoutSec -gt 0) {
  $job = Start-Job -ScriptBlock {
    param($t)
    Get-CimInstance Win32_Process -Filter "Name='electron.exe'" -ErrorAction SilentlyContinue |
      Where-Object { $_.ExecutablePath -and ($_.CommandLine -notlike '*boardclip-mcp.js*') -and $(try { [IO.Path]::GetFullPath($_.ExecutablePath) -ieq $t } catch { $false }) } |
      ForEach-Object { $_.ProcessId }
  } -ArgumentList $target
  if ($job -and (Wait-Job $job -Timeout $SweepTimeoutSec)) {
    $ids = @(Receive-Job $job | ForEach-Object { [int]$_ })
    foreach ($id in $ids) {
      if ($killed -notcontains $id) {
        Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        $killed += $id
      }
    }
  } else {
    if ($job) { Stop-Job $job -ErrorAction SilentlyContinue }
    Write-Host "kill: WMI did not answer within ${SweepTimeoutSec}s; sweep skipped (pid file already handled the app)."
  }
  if ($job) { Remove-Job $job -Force -ErrorAction SilentlyContinue }
} else {
  Write-Host 'kill: WMI sweep disabled; pid-file path only.'
}

if ($killed.Count -gt 0) {
  if (-not (Wait-Gone $killed 10)) {
    Write-Host "ERROR: BoardClip processes still alive: $($killed -join ', ')"
    exit 1
  }
  # A forced stop never runs the app's exit hook, so drop the record ourselves.
  if ($rec -and $rec.pid -and ($killed -contains [int]$rec.pid)) { Remove-Item $pidFile -Force -ErrorAction SilentlyContinue }
  Write-Host "BoardClip stopped ($($killed -join ', '))."
} else {
  Write-Host 'BoardClip was not running.'
}
exit 0
