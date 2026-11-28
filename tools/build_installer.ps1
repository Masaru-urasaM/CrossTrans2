param(
    [Parameter(Mandatory=$true)][string]$SourceExe,
    [Parameter(Mandatory=$true)][string]$Compiler,
    [string]$OutputDirectory,
    [string]$AppVersion = "2.0.1"
)
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$resolvedExe = (Resolve-Path -LiteralPath $SourceExe).Path
$resolvedCompiler = (Resolve-Path -LiteralPath $Compiler).Path
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repoRoot "dist-release" }
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$resolvedOutput = (Resolve-Path -LiteralPath $OutputDirectory).Path
& $resolvedCompiler /Qp "/DAppVersion=$AppVersion" "/DSourceExe=$resolvedExe" "/O$resolvedOutput" (Join-Path $repoRoot "installer\CrossTrans2.iss")
if ($LASTEXITCODE -ne 0) { throw "Installer compiler failed with exit code $LASTEXITCODE" }
$installer = Join-Path $resolvedOutput "CrossTrans2Setup_v$AppVersion.exe"
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) { throw "Installer output is missing" }
$checksum = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText((Join-Path $resolvedOutput 'SHA256SUMS.txt'), "$checksum  $(Split-Path -Leaf $installer)`r`n", (New-Object System.Text.UTF8Encoding($false)))
Write-Output $installer
