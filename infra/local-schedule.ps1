# Routine local ingest — invoked by Windows Task Scheduler.
#   ds-corpus-weekly  -> -Cadence weekly   (arxiv, gutenberg, internet_archive)
#   ds-corpus-monthly -> -Cadence monthly  (hathitrust, iiif)
#
# Runs every ENABLED source on the given cadence, sequentially, into the local
# library (settings.local_library_dir). Logs one file per run under the
# library's _runs directory, plus a rolling "last-<cadence>.log".
#
# Kill switch: create a file named HALT in the library root; runs abort at the
# next task boundary. Delete it to resume.
#
# NOTE: the ingest is launched with Start-Process + file redirection on purpose.
# In Windows PowerShell 5.1, piping a native exe's stderr through the shell (as
# `& $exe ... *>> $log` does) wraps each stderr line in a NativeCommandError and,
# under $ErrorActionPreference='Stop', aborts the whole script the moment
# ds-corpus writes a benign progress line to stderr. Start-Process captures the
# native streams to files directly and never does that.

param(
  [ValidateSet("weekly", "monthly")]
  [string]$Cadence = "weekly"
)

$repo    = "C:\Users\marcu\ds-corpus"
$library = "C:\Users\marcu\ds-corpus-library"
$exe     = Join-Path $repo ".venv\Scripts\ds-corpus.exe"
$logDir  = Join-Path $library "_runs"
New-Item -ItemType Directory -Force $logDir | Out-Null

$stamp   = Get-Date -Format "yyyyMMdd-HHmmss"
$log     = Join-Path $logDir "scheduled-$Cadence-$stamp.log"
$rolling = Join-Path $logDir "last-$Cadence.log"
$outTmp  = "$log.out"
$errTmp  = "$log.err"

if (-not (Test-Path $exe)) {
  "ds-corpus.exe not found at $exe - is the venv set up?" | Out-File -Encoding utf8 $log
  Copy-Item $log $rolling -Force
  exit 1
}

"=== ds-corpus $Cadence run @ $stamp ===" | Out-File -Encoding utf8 $log

$p = Start-Process -FilePath $exe `
  -ArgumentList "run", "--schedule", $Cadence, "--local" `
  -NoNewWindow -Wait -PassThru `
  -RedirectStandardOutput $outTmp -RedirectStandardError $errTmp
$code = $p.ExitCode

# Fold captured stdout + stderr into the run log, then drop the temp files.
Get-Content $outTmp, $errTmp -ErrorAction SilentlyContinue | Add-Content -Encoding utf8 $log
Remove-Item $outTmp, $errTmp -ErrorAction SilentlyContinue
"exit code: $code" | Add-Content -Encoding utf8 $log
Copy-Item $log $rolling -Force

# Keep the 12 most recent per-run logs for this cadence.
Get-ChildItem $logDir -Filter "scheduled-$Cadence-*.log" |
  Sort-Object LastWriteTime -Descending | Select-Object -Skip 12 |
  Remove-Item -Force -ErrorAction SilentlyContinue

exit $code
