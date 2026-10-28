param([string]$SourceRepository = 'https://github.com/NanmiCoder/MediaCrawler.git')
$ErrorActionPreference = 'Stop'
$WorkspacePath = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$RuntimePath = Join-Path $WorkspacePath 'work\xhs-local-data\runtime\MediaCrawler'
$PinnedRevision = '68d9ef1c1da22eab75940dfd7cec259907006538'
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw '请先安装 uv，再运行此脚本。' }
if (-not (Test-Path -LiteralPath $RuntimePath)) {
    git clone --no-checkout $SourceRepository $RuntimePath
    if ($LASTEXITCODE -ne 0) { throw '下载笔记采集源码失败。' }
    git -C $RuntimePath checkout --detach $PinnedRevision
    if ($LASTEXITCODE -ne 0) { throw '固定源码版本失败。' }
}
$CurrentRevision = git -C $RuntimePath rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $CurrentRevision.Trim() -ne $PinnedRevision) { throw '已有采集源码版本不同，未覆盖。' }
uv sync --frozen --python 3.11 --project $RuntimePath
if ($LASTEXITCODE -ne 0) { throw '笔记采集依赖安装失败。' }
Write-Output '笔记采集环境已安装。重启本地工作台后生效。'
