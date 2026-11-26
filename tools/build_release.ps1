param(
    [Parameter(Mandatory=$true)][string]$Compiler
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $repoRoot
try {
    New-Item -ItemType Directory -Path logs -Force | Out-Null
    python tools/verify_furigana_bundle.py --source
    if ($LASTEXITCODE -ne 0) { throw 'Furigana preflight failed.' }
    python -m PyInstaller --clean --noconfirm --distpath dist-release --workpath build-release CrossTrans.spec
    if ($LASTEXITCODE -ne 0) { throw 'Frozen build failed.' }
    python tools/verify_furigana_bundle.py --exe dist-release/CrossTrans2.exe
    if ($LASTEXITCODE -ne 0) { throw 'Furigana/codecs are missing from the executable.' }
    python tools/verify_ui_bundle.py --exe dist-release/CrossTrans2.exe --json logs/ui-bundle.json
    if ($LASTEXITCODE -ne 0) { throw 'Packaged UI resources or source do not match.' }
    & dist-release/CrossTrans2.exe --self-check logs/self-check.json
    if ($LASTEXITCODE -ne 0) { throw 'Frozen GUI/runtime self-check failed.' }
    & dist-release/CrossTrans2.exe --audio-self-check logs/audio-self-check.json
    if ($LASTEXITCODE -ne 0) { throw 'Frozen executable audio self-check failed.' }
    & (Join-Path $PSScriptRoot 'build_installer.ps1') -SourceExe dist-release/CrossTrans2.exe -Compiler $Compiler
    if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }
} finally {
    Pop-Location
}
