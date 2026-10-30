param([string]$SourceRepository = 'https://github.com/dreammis/social-auto-upload.git')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$PublishRuntimePath = Join-Path $DataPath 'runtime\SocialAutoUpload'
$PublishPythonPath = Join-Path $PublishRuntimePath '.venv\Scripts\python.exe'
$PublishRevision = '3847fff01f5434a8a32829833163cd6723cabc59'
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw '请先安装 uv，再运行此脚本。' }
if (-not (Test-Path -LiteralPath (Join-Path $PublishRuntimePath '.git'))) {
    if (Test-Path -LiteralPath $PublishRuntimePath) { throw '发布目录已存在但不是 Git 仓库，未覆盖。' }
    git init $PublishRuntimePath
    if ($LASTEXITCODE -ne 0) { throw '创建发布源码目录失败。' }
    git -C $PublishRuntimePath remote add origin $SourceRepository
    if ($LASTEXITCODE -ne 0) { throw '设置发布源码来源失败。' }
}
$CurrentPublishRevision = git -C $PublishRuntimePath rev-parse --verify HEAD 2>$null
if ($LASTEXITCODE -ne 0) {
    $PendingPublishFiles = git -C $PublishRuntimePath status --porcelain
    if ($LASTEXITCODE -ne 0 -or $PendingPublishFiles) { throw '未完成的发布源码目录包含其他文件，未覆盖。' }
    $ExistingPublishOrigin = git -C $PublishRuntimePath remote get-url origin 2>$null
    if ($LASTEXITCODE -ne 0) {
        git -C $PublishRuntimePath remote add origin $SourceRepository
        if ($LASTEXITCODE -ne 0) { throw '设置发布源码来源失败。' }
    }
    git -C $PublishRuntimePath fetch --depth=1 origin $PublishRevision
    if ($LASTEXITCODE -ne 0) { throw '下载固定版本发布源码失败。' }
    git -C $PublishRuntimePath checkout --detach FETCH_HEAD
    if ($LASTEXITCODE -ne 0) { throw '检出固定版本发布源码失败。' }
    $CurrentPublishRevision = git -C $PublishRuntimePath rev-parse HEAD
}
if ($LASTEXITCODE -ne 0 -or $CurrentPublishRevision.Trim() -ne $PublishRevision) { throw '已有发布源码版本不同，未覆盖。请检查运行环境目录。' }
if (-not (Test-Path -LiteralPath $PublishPythonPath)) {
    uv venv --python 3.11 (Join-Path $PublishRuntimePath '.venv')
    if ($LASTEXITCODE -ne 0) { throw '创建发布运行环境失败。' }
}
uv pip install --python $PublishPythonPath -r (Join-Path $ProjectPath 'requirements-publish.lock')
if ($LASTEXITCODE -ne 0) { throw '安装发布依赖失败。' }
$PublishConfigPath = Join-Path $PublishRuntimePath 'conf.py'
if (-not (Test-Path -LiteralPath $PublishConfigPath)) {
    Copy-Item -LiteralPath (Join-Path $PublishRuntimePath 'conf.example.py') -Destination $PublishConfigPath
}
& $PublishPythonPath -m patchright install chromium
if ($LASTEXITCODE -ne 0) { throw '安装后台浏览器失败。' }
Write-Output '发布环境已安装。重启工作台后打开 /publish，登录信息与采集分别保存。'
