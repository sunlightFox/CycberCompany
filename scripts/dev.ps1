$ErrorActionPreference = "Stop"

$root = (Resolve-Path "$PSScriptRoot\..").Path
$paths = @(
  "$root\apps\local-api",
  "$root\packages\core-types",
  "$root\services\asset-broker",
  "$root\services\brain",
  "$root\services\capability-graph",
  "$root\services\chat-runtime",
  "$root\services\context-gateway",
  "$root\services\heart",
  "$root\services\memory",
  "$root\services\persona-engine",
  "$root\services\response-composer",
  "$root\services\safety",
  "$root\services\shell-runtime",
  "$root\services\skill-engine",
  "$root\services\task-engine",
  "$root\services\tools",
  "$root\services\trace"
)

$env:CYCBER_ROOT = $root
$env:PYTHONPATH = ($paths -join [System.IO.Path]::PathSeparator)

python -m uvicorn app.main:app --app-dir "$root\apps\local-api" --host 127.0.0.1 --port 8765
