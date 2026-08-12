# maplebot elevated worker (launched by admin_worker.bat via UAC)
# Watches cmd.ps1 in the queue dir; runs it elevated; writes output back.
# Close this window to stop the collaboration.
#
# The queue lives at %TEMP%\maplebot_worker by default -- a stable path that
# survives Claude Code session changes (session-specific scratchpad UUIDs
# rot on every new session). Pass -QueueDir to override.
param([string]$QueueDir = (Join-Path $env:TEMP "maplebot_worker"))
$ErrorActionPreference = "Continue"
$dir = $QueueDir
New-Item -ItemType Directory -Force $dir | Out-Null
("boot " + (Get-Date -Format o)) | Out-File "$dir\boot.txt" -Encoding utf8
Set-Location $PSScriptRoot

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Host ""
Write-Host "=== maplebot elevated worker ONLINE ==="
Write-Host ("admin: " + $admin)
Write-Host ("queue: " + $dir)
Write-Host "Claude drives key tests / bot runs through this window."
Write-Host "Close this window anytime to STOP."
Write-Host ""

while ($true) {
    ("alive " + (Get-Date -Format o) + " admin=" + $admin) | Out-File "$dir\heartbeat.txt" -Encoding utf8
    $cmdFile = "$dir\cmd.ps1"
    if (Test-Path $cmdFile) {
        $stamp = Get-Date -Format "HHmmss"
        Write-Host ("[" + $stamp + "] running command...")
        $tmp = "$dir\running_$stamp.ps1"
        Move-Item $cmdFile $tmp -Force
        try {
            & $tmp *> "$dir\out.txt"
        } catch {
            $_ | Out-File "$dir\out.txt" -Append
        }
        ("done " + $stamp) | Out-File "$dir\done.txt" -Encoding utf8
        Write-Host ("[" + $stamp + "] done.")
    }
    Start-Sleep -Milliseconds 800
}
