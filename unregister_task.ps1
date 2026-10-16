# Removes the Windows Task Scheduler task 'Finan Index Tracker'.
$ErrorActionPreference = 'Stop'
$taskName = 'Finan Index Tracker'

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host 'Removed'
} else {
    Write-Host 'Task not found'
}
