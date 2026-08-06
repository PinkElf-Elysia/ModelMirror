param(
    [string]$Python = "python",
    [string]$OutputRoot = "output/coding-project-host"
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$outputPath = Join-Path $repoRoot $OutputRoot
$workPath = Join-Path $outputPath "build"
$distPath = Join-Path $outputPath "dist"
$specPath = Join-Path $outputPath "spec"
$entry = Join-Path $repoRoot "server/coding_project_host/windows_helper.py"
$requirements = Join-Path $repoRoot "server/coding_project_host/requirements.txt"

& $Python -m pip install --requirement $requirements
if ($LASTEXITCODE -ne 0) { throw "Unable to install the pinned project host build dependencies." }

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --noupx `
    --name "ModelMirrorProjectHost" `
    --paths $repoRoot `
    --distpath $distPath `
    --workpath $workPath `
    --specpath $specPath `
    --collect-all websockets `
    $entry
if ($LASTEXITCODE -ne 0) { throw "Unable to build the Windows project host." }

$artifact = Join-Path $outputPath "ModelMirrorProjectHost-windows-x64.zip"
if (Test-Path -LiteralPath $artifact) { Remove-Item -LiteralPath $artifact -Force }
Compress-Archive -LiteralPath (Join-Path $distPath "ModelMirrorProjectHost") -DestinationPath $artifact -CompressionLevel Optimal
$size = (Get-Item -LiteralPath $artifact).Length
if ($size -gt 41943040) { throw "The compressed project host exceeds the 40 MiB budget." }
Get-FileHash -LiteralPath $artifact -Algorithm SHA256
