$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw 'Please run setup.bat first.'
}
& $taskPython -m pip install -r requirements-release.txt
if ($LASTEXITCODE -ne 0) { throw 'Failed to install release dependencies.' }
& $taskPython -m PyInstaller --noconfirm --clean --distpath dist AutoDLFresh.spec
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
& $taskPython tools/package_release.py
if ($LASTEXITCODE -ne 0) { throw 'Release packaging failed.' }
Write-Host 'Built: dist\AutoDLFresh\AutoDLFresh.exe'
