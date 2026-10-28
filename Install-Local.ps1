$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Local-Paths.ps1')
$RuntimePath = Join-Path $DataPath 'runtime\Spider_XHS'
$CookiePath = Join-Path $DataPath 'auth\xhs-cookie.txt'
$InstallerPath = Join-Path $PSScriptRoot 'skills\xhs-group-chat-insights\scripts\setup_capture.py'
& py -3.11 $InstallerPath --runtime-root $RuntimePath --cookie-file $CookiePath
if ($LASTEXITCODE -ne 0) { throw '采集环境安装未完成。' }
$PythonPath = Join-Path $RuntimePath '.venv\Scripts\python.exe'
& $PythonPath -m pip install -r (Join-Path $PSScriptRoot 'requirements-web.txt')
if ($LASTEXITCODE -ne 0) { throw 'Web 界面依赖安装未完成。' }
Write-Output '安装完成。运行 Start-WebUI.ps1 启动本地界面。'
