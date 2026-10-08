$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PHOENIX_WORKING_DIR = Join-Path $projectRoot "data/phoenix"
$env:PHOENIX_HOST = "127.0.0.1"
$env:PYTHONIOENCODING = "utf-8"

rtk proxy uv tool run --python 3.12 --from "arize-phoenix==20.3.0" phoenix serve
exit $LASTEXITCODE
