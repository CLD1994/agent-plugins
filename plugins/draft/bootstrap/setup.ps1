# draft 插件平台 setup（PowerShell）。
# Codex 只读取 PLUGIN_DATA；Claude Code 只读取 CLAUDE_PLUGIN_DATA。
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('codex', 'claude')]
    [string]$Platform
)
$ErrorActionPreference = 'Stop'

function Protect-SingleQuote([string]$Value) {
    return "'" + ($Value -replace "'", "''") + "'"
}

$PluginRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Runtime = Join-Path $PluginRoot 'runtime'
$Skill = Join-Path $PluginRoot 'skills/draft/SKILL.md'
$Data = if ($Platform -eq 'codex') { $env:PLUGIN_DATA } else { $env:CLAUDE_PLUGIN_DATA }
if (-not $Data -or -not (Test-Path -PathType Container $Data)) {
    throw '平台数据目录不可用'
}
$Data = (Resolve-Path $Data).Path

$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Write-Error '未找到 uv。请先安装：https://docs.astral.sh/uv/getting-started/installation/'
    exit 1
}

New-Item -ItemType Directory -Force -Path (Join-Path $Data 'logs'), (Join-Path $Data 'bin') | Out-Null

Write-Host '==> 同步隔离环境（Python 3.14，按锁文件）'
$env:UV_PROJECT_ENVIRONMENT = Join-Path $Data 'venv'
uv sync --project $Runtime --locked --python 3.14

$Version = (uv run --project $Runtime --frozen --no-sync draft --version).Trim()
$LockDigest = (Get-FileHash -Algorithm SHA256 (Join-Path $Runtime 'uv.lock')).Hash.ToLower()
$PythonVersion = ((uv run --project $Runtime --frozen --no-sync python --version) -split ' ')[1]

@{
    runtime_version = $Version
    lock_digest = $LockDigest
    python_version = $PythonVersion
    runtime_path = $Runtime
} | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $Data 'installed-runtime-manifest.json')

$Venv = Join-Path $Data 'venv'

$CliLauncher = Join-Path $Data 'bin/draft.cmd'
@"
@echo off
set "DRAFT_DATA=$Data"
set "DRAFT_SKILL_PATH=$Skill"
set "UV_PROJECT_ENVIRONMENT=$Venv"
uv run --project "$Runtime" --frozen --no-sync draft %*
"@ | Set-Content -Encoding ascii $CliLauncher

$HookLauncher = Join-Path $Data 'bin/draft-hook-claude.cmd'
@"
@echo off
set "DRAFT_DATA=$Data"
set "DRAFT_SKILL_PATH=$Skill"
set "UV_PROJECT_ENVIRONMENT=$Venv"
uv run --project "$Runtime" --frozen --no-sync draft-hook-claude --data-dir "$Data" %*
"@ | Set-Content -Encoding ascii $HookLauncher

$VenvQ = Protect-SingleQuote $Venv
$RuntimeQ = Protect-SingleQuote $Runtime
$DataQ = Protect-SingleQuote $Data
$SkillQ = Protect-SingleQuote $Skill

$HookLauncherPS = Join-Path $Data 'bin/draft-hook-claude.ps1'
@"
`$env:DRAFT_DATA = $($DataQ)
`$env:DRAFT_SKILL_PATH = $($SkillQ)
`$env:UV_PROJECT_ENVIRONMENT = $($VenvQ)
uv run --project $($RuntimeQ) --frozen --no-sync draft-hook-claude --data-dir $($DataQ) `$args
"@ | Set-Content -Encoding utf8 $HookLauncherPS

& $CliLauncher --version | Out-Null

@{
    status = 'ok'
    runtime_version = $Version
    platform = $Platform
    data_dir = $Data
} | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $Data 'setup-status.json')

Write-Host "环境就绪：$Data（平台 $Platform）"
