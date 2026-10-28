param([int]$Port = 8777)
$ErrorActionPreference = 'Stop'
$ProjectPath = $PSScriptRoot
$WorkspacePath = Split-Path (Split-Path $ProjectPath -Parent) -Parent
$DataPath = Join-Path $WorkspacePath 'work\xhs-local-data'
$PythonPath = Join-Path $DataPath 'runtime\Spider_XHS\.venv\Scripts\python.exe'
$PidPath = Join-Path $DataPath 'web-ui.pid'
if (-not (Test-Path -LiteralPath $PythonPath)) { throw '运行环境尚未安装，请先运行 Install-Local.ps1。' }
try {
    $Health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
    if ($Health.application -eq 'xhs-group-chat-insights-local') {
        Write-Output "界面已启动：http://127.0.0.1:$Port"
        return
    }
} catch {}
if (Test-Path -LiteralPath $PidPath) {
    $ExistingProcessId = 0
    if ([int]::TryParse((Get-Content -LiteralPath $PidPath -Raw).Trim(), [ref]$ExistingProcessId)) {
        $ExistingProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$ExistingProcessId" -ErrorAction SilentlyContinue
        if ($ExistingProcess -and $ExistingProcess.ExecutablePath -eq $PythonPath -and $ExistingProcess.CommandLine -like '*uvicorn webapp.app:create_app*') {
            throw '这个数据目录已有服务在运行。请先停止现有服务，再使用不同端口启动。'
        }
    }
}
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) { throw "端口 $Port 已被其他程序使用，请指定其他端口。" }
$env:XHS_LOCAL_DATA = $DataPath
$env:PYTHONUTF8 = '1'
$ProcessArgs = @('-m', 'uvicorn', 'webapp.app:create_app', '--factory', '--host', '127.0.0.1', '--port', $Port, '--no-access-log', '--no-proxy-headers')
$ServerProcess = Start-Process -FilePath $PythonPath -ArgumentList $ProcessArgs -WorkingDirectory $ProjectPath -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $DataPath 'web-ui.stdout.log') -RedirectStandardError (Join-Path $DataPath 'web-ui.stderr.log')
$ServerProcess.Id | Set-Content -LiteralPath $PidPath -Encoding ASCII
for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
    Start-Sleep -Milliseconds 300
    try {
        $Health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 1
        if ($Health.application -eq 'xhs-group-chat-insights-local') {
            Write-Output "界面已启动：http://127.0.0.1:$Port"
            return
        }
    } catch {}
    if ($ServerProcess.HasExited) { break }
}
throw "服务未能启动，请查看 $DataPath\web-ui.stderr.log。"
