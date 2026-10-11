# Install the local backend as a per-user scheduled task that starts at logon (Windows). Run from the repo root:
#   $env:MODEL_REVISION="<sha>"; $env:CLOUD_URL="https://..."; $env:CLOUD_ENROLL_KEY="..."; .\installer\windows\install.ps1
$ErrorActionPreference = "Stop"
if (-not $env:MODEL_REVISION) { throw "set MODEL_REVISION to the full commit sha of the Tier 2 model to pin" }
$root = (Resolve-Path "$PSScriptRoot\..\..").Path
$app  = if ($env:DOPPEL_HOME) { $env:DOPPEL_HOME } else { Join-Path $env:LOCALAPPDATA "Doppel" }
New-Item -ItemType Directory -Force -Path $app | Out-Null

python -m venv "$app\venv"
& "$app\venv\Scripts\pip.exe" install --quiet -r "$root\local-backend\requirements.txt" huggingface_hub
if (Test-Path "$app\app") { Remove-Item -Recurse -Force "$app\app" }
New-Item -ItemType Directory -Force -Path "$app\app" | Out-Null
Copy-Item -Recurse "$root\local-backend\app", "$root\local-backend\dlp_core" "$app\app"
Get-ChildItem -Recurse "$app\app" -Include "test_*.py" | Remove-Item -Force   # runtime only
Get-ChildItem -Recurse "$app\app" -Directory | Where-Object { $_.Name -eq "tests" -or $_.FullName -like "*\dlp_core\eval" } | Remove-Item -Recurse -Force
& "$app\venv\Scripts\python.exe" "$root\scripts\fetch_model.py" --revision $env:MODEL_REVISION --dest "$app\model"

# The launcher sets the environment; it lives in the user's profile (ACL: owner only) because it holds the enrollment key.
$launcher = "$app\run.cmd"
@"
@echo off
set DLP_TIER2_MODEL=$app\model
set DLP_EXTENSION_IDS=$($env:DLP_EXTENSION_IDS)
set CLOUD_URL=$($env:CLOUD_URL)
set CLOUD_ENROLL_KEY=$($env:CLOUD_ENROLL_KEY)
set HF_HUB_OFFLINE=1
cd /d "$app\app"
"$app\venv\Scripts\uvicorn.exe" app.main:app --host 127.0.0.1 --port 8765
"@ | Set-Content -Encoding ASCII $launcher
icacls $launcher /inheritance:r /grant:r "$($env:USERNAME):(R)" | Out-Null

$action  = New-ScheduledTaskAction -Execute $launcher
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$set     = New-ScheduledTaskSettingsSet -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
Register-ScheduledTask -TaskName "Doppel backend" -Action $action -Trigger $trigger -Settings $set -Force | Out-Null
Start-ScheduledTask -TaskName "Doppel backend"
Write-Host "Doppel backend running on 127.0.0.1:8765 (task: Doppel backend)"
