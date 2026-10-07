# Edit timings or fire the site health report job.
# From repo root:
#   .\backend\scripts\site_report_schedule.ps1 show
#   .\backend\scripts\site_report_schedule.ps1 fire --project-id 404
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CliArgs
)
$backend = Split-Path -Parent $PSScriptRoot
Set-Location $backend
& "$backend\.venv\Scripts\python.exe" -m app.scripts.site_report_schedule @CliArgs
exit $LASTEXITCODE
