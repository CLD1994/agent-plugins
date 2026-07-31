$ErrorActionPreference = 'Stop'
$Runtime = Join-Path $PSScriptRoot 'runtime'
$Entry = Join-Path $PSScriptRoot 'draft'
$Skill = Join-Path (Split-Path $PSScriptRoot -Parent) 'SKILL.md'
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    [Console]::Error.WriteLine('runtime.uv_unavailable: 请从 https://docs.astral.sh/uv/ 安装 uv')
    exit 4
}
$previous = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $Runtime 'src'
    & $uv.Source run --no-project --python 3.14 python -m draft.entry `
        --entry-path $Entry --skill-path $Skill --runtime-path $Runtime -- @args
    exit $LASTEXITCODE
} finally {
    $env:PYTHONPATH = $previous
}
