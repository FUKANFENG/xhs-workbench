$ErrorActionPreference = 'Stop'
$WorkspacePath = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$DataPath = Join-Path $WorkspacePath 'work\xhs-local-data'
$PidPath = Join-Path $DataPath 'web-ui.pid'
$PythonPath = Join-Path $DataPath 'runtime\Spider_XHS\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $PidPath)) { Write-Output '没有找到本地服务进程记录。'; return }
$ServerProcessId = [int](Get-Content -LiteralPath $PidPath -Raw)
$ServerProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$ServerProcessId" -ErrorAction SilentlyContinue
if ($ServerProcess -and $ServerProcess.ExecutablePath -eq $PythonPath -and $ServerProcess.CommandLine -like '*uvicorn webapp.app:create_app*') {
    & taskkill.exe /PID $ServerProcessId /T /F | Out-Null
    Write-Output '本地服务已停止。'
} elseif ($ServerProcess) { throw '进程信息不匹配，未停止任何程序。' }
Remove-Item -LiteralPath $PidPath -ErrorAction SilentlyContinue
