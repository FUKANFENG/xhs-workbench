param([ValidateRange(1024,65535)][int]$Port = 8779)
$ErrorActionPreference = 'Stop'
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw '请先安装并启动 Docker Desktop（Linux containers）。' }
docker info --format '{{.OSType}}'
if ($LASTEXITCODE -ne 0) { throw 'Docker 服务尚未就绪。' }
$PreviousPort = $env:XHS_PORT
try {
    $env:XHS_PORT = [string]$Port
    docker compose --project-directory $PSScriptRoot up -d --build --wait --wait-timeout 120
    if ($LASTEXITCODE -ne 0) { throw '安装未完成，请检查上面的构建或启动错误。' }
    Write-Output "已启动：http://127.0.0.1:$Port"
} finally { $env:XHS_PORT = $PreviousPort }
