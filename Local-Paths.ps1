# Keep existing installations where they are; new checkouts use their own data directory.
$ProjectPath = $PSScriptRoot
$WorkspacePath = Split-Path (Split-Path $ProjectPath -Parent) -Parent
$LegacyDataPath = Join-Path $WorkspacePath 'work\xhs-local-data'
$DataPath = if ($env:XHS_LOCAL_DATA) {
    [System.IO.Path]::GetFullPath($env:XHS_LOCAL_DATA)
} elseif (Test-Path -LiteralPath $LegacyDataPath) {
    $LegacyDataPath
} else {
    Join-Path $ProjectPath 'data'
}
