# Weekly local ingest — invoked by Windows Task Scheduler (task: ds-corpus-weekly).
# Pulls every enabled source with schedule: weekly into the local library
# (settings.local_library_dir). Logs to the library's _runs directory.
#
# Kill switch: create a file named HALT in the library root; runs abort at
# the next task boundary. Delete it to resume.

$ErrorActionPreference = "Stop"
$repo = "C:\Users\marcu\ds-corpus"
$logDir = "C:\Users\marcu\ds-corpus-library\_runs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$log = Join-Path $logDir "scheduled-$stamp.log"

& "$repo\.venv\Scripts\ds-corpus.exe" run --schedule weekly --local *>> $log
exit $LASTEXITCODE
