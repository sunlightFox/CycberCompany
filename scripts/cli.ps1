param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]] $CliArgs
)

$ErrorActionPreference = "Stop"

$root = (Resolve-Path "$PSScriptRoot\..").Path
$paths = @(
  "$root\apps\local-cli",
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

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
  $pythonPath = $venvPython
} else {
  $python = Get-Command python -ErrorAction SilentlyContinue
  if ($null -eq $python) {
    $python = Get-Command py -ErrorAction Stop
  }
  $pythonPath = $python.Source
}

if ($CliArgs.Count -eq 0) {
  $CliArgs = @("chat", "--interactive", "--autostart")
}

& $pythonPath -m cycber_cli @CliArgs
exit $LASTEXITCODE
