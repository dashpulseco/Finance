# Registers the Windows Task Scheduler task 'Finan Index Tracker'.
# Runs `py -3 <this folder>\tracker.py` daily at config.json -> notification_time
# as the current interactive user (no elevation), so the toast is visible.
# Re-run this script after changing notification_time in config.json.
$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$taskName = 'Finan Index Tracker'

$cfg = Get-Content -Raw "$root\config.json" | ConvertFrom-Json
$time = $cfg.notification_time
if ([string]::IsNullOrWhiteSpace($time)) { $time = '09:00' }

# Resolved at registration time rather than hard-coded: this file is committed to a public
# repository, so it must not contain a path naming this PC's user account.
$py = (Get-Command py -ErrorAction SilentlyContinue).Source
if (-not $py) {
    Write-Host "The Python launcher 'py' was not found on PATH."
    Write-Host "Install Python 3 from https://www.python.org/downloads/windows/ (keep the"
    Write-Host "'py launcher' option ticked), reopen PowerShell and run this script again."
    exit 1
}

$action = New-ScheduledTaskAction -Execute $py -Argument "-3 `"$root\tracker.py`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

Register-ScheduledTask -TaskName $taskName -Description 'Daily Finan index drawdown check and toast' `
    -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

$info = Get-ScheduledTaskInfo -TaskName $taskName
Write-Host "Registered '$taskName' (daily at $time, python: $py)"
Write-Host "Next run time: $($info.NextRunTime)"
