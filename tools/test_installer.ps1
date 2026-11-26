param(
    [Parameter(Mandatory=$true)][string]$Installer,
    [Parameter(Mandatory=$true)][string]$SourceExe,
    [Parameter(Mandatory=$true)][switch]$IsolatedEnvironment,
    [string]$OutputDirectory = "logs\installer-acceptance"
)
$ErrorActionPreference = 'Stop'
if (-not $IsolatedEnvironment) { throw 'Run only in a disposable Windows VM or CI runner.' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'An elevated isolated session is required for the all-users installer.' }
$resolvedInstaller = (Resolve-Path -LiteralPath $Installer).Path
$sourceHash = (Get-FileHash -LiteralPath $SourceExe -Algorithm SHA256).Hash
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$outputRoot = (Resolve-Path -LiteralPath $OutputDirectory).Path
$appFolder = Join-Path $env:ProgramFiles 'CrossTrans 2'
$appExe = Join-Path $appFolder 'CrossTrans2.exe'
$uninstaller = Join-Path $appFolder 'unins000.exe'
if (Test-Path -LiteralPath $appFolder) { throw 'Use a clean VM; CrossTrans 2 already exists.' }
$profile2 = Join-Path $env:APPDATA 'CrossTrans2'
$profile1 = Join-Path $env:APPDATA 'CrossTrans'
if ((Test-Path -LiteralPath $profile2) -or (Test-Path -LiteralPath $profile1)) { throw 'Use a clean profile without CrossTrans data.' }
New-Item -ItemType Directory -Path $profile1,$profile2 -Force | Out-Null
$marker = [guid]::NewGuid().ToString('N')
[IO.File]::WriteAllText((Join-Path $profile1 'acceptance-marker.txt'), $marker)
[IO.File]::WriteAllText((Join-Path $profile2 'acceptance-marker.txt'), $marker)
$checks = [ordered]@{}
# Process.MainWindowHandle omits owned Tk toplevels on a withdrawn root.
Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class CandidateWindows {
    public delegate bool Callback(IntPtr window, IntPtr data);
    [DllImport("user32.dll")] static extern bool EnumWindows(Callback callback, IntPtr data);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr window, StringBuilder text, int count);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr window, StringBuilder text, int count);
    [DllImport("user32.dll", SetLastError=true)] static extern IntPtr SendMessageTimeout(IntPtr window, uint message, UIntPtr word, IntPtr data, uint flags, uint timeout, out UIntPtr result);
    public static bool Responsive(long handle) {
        UIntPtr result;
        return SendMessageTimeout(new IntPtr(handle), 0, UIntPtr.Zero, IntPtr.Zero, 2, 3000, out result) != IntPtr.Zero;
    }
    public static string[] Inspect(int[] ids) {
        var lines = new List<string>();
        EnumWindows((window, data) => {
            uint pid; GetWindowThreadProcessId(window, out pid);
            if (Array.IndexOf(ids, (int)pid) >= 0) {
                var title = new StringBuilder(1024); GetWindowText(window, title, title.Capacity);
                var kind = new StringBuilder(256); GetClassName(window, kind, kind.Capacity);
                lines.Add(pid + "|" + IsWindowVisible(window) + "|" + kind + "|" + title + "|" + window.ToInt64());
            }
            return true;
        }, IntPtr.Zero);
        return lines.ToArray();
    }
}
"@
function Get-CandidateWindows {
    $ids = @(Get-Process CrossTrans2 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
    if ($ids.Count -eq 0) { return @() }
    return [CandidateWindows]::Inspect([int[]]$ids)
}
function Run-Setup([string]$Name, [string[]]$Extra) {
    $log = Join-Path $outputRoot ($Name + '.log')
    $arguments = @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-',('/LOG="' + $log + '"')) + $Extra
    Write-Output ('Acceptance start: ' + $Name)
    $process = Start-Process -FilePath $resolvedInstaller -ArgumentList $arguments -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(120000)) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        throw "$Name timed out after 120 seconds."
    }
    if ($process.ExitCode -ne 0) { throw "$Name failed: $($process.ExitCode)" }
    if ((Get-FileHash -LiteralPath $appExe -Algorithm SHA256).Hash -ne $sourceHash) { throw "$Name installed different bytes." }
    $checks[$Name] = 'passed'
    Write-Output ('Acceptance passed: ' + $Name)
}
function Stop-Candidate {
    Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $appExe } | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
}
try {
    Run-Setup 'clean-install' @('/TASKS=desktopicon')
    if (-not (Test-Path -LiteralPath $uninstaller)) { throw 'Uninstaller missing.' }
    $startLink = Join-Path ([Environment]::GetFolderPath('CommonPrograms')) 'CrossTrans 2.lnk'
    $desktopLink = Join-Path ([Environment]::GetFolderPath('CommonDesktopDirectory')) 'CrossTrans 2.lnk'
    if (-not ((Test-Path -LiteralPath $startLink) -and (Test-Path -LiteralPath $desktopLink))) { throw 'All-users shortcuts missing.' }
    $checks.shortcuts = 'passed'
    # The GUI is the visible subject of this disposable-runner test, not a background helper.
    $process = Start-Process -FilePath $appExe -WindowStyle Normal -PassThru
    $deadline = (Get-Date).AddSeconds(45)
    $shown = $false
    do {
        Start-Sleep -Milliseconds 500
        $candidates = Get-Process CrossTrans2 -ErrorAction SilentlyContinue
        $visibleTk = @(Get-CandidateWindows | Where-Object { $_ -match '\|True\|TkTopLevel\|' })
        if ($visibleTk.Count -gt 0) {
            $visibleTk | Set-Content -LiteralPath (Join-Path $outputRoot 'first-launch-windows.txt') -Encoding utf8
            $shown = $true
            break
        }
    } while ((Get-Date) -lt $deadline)
    if (-not $shown) { throw 'Frozen application did not show its first-run window.' }
    $checks.frozenFirstLaunch = 'passed'
    Start-Sleep -Seconds 1
    $firstWindowHandle = ($visibleTk[0] -split '\|')[-1]
    if (-not [CandidateWindows]::Responsive([long]$firstWindowHandle)) { throw 'First-run UI is not responding.' }
    $checks.frozenUIResponsive = 'passed'
    Write-Output 'Acceptance passed: visible and responsive first-run UI'
    $captureTool = Join-Path $PSScriptRoot 'capture_installed_window.py'
    $captureArguments = @(('"' + $captureTool + '"'),$firstWindowHandle,('"' + (Join-Path $outputRoot 'first-setup.png') + '"'))
    $captureProcess = Start-Process -FilePath (Get-Command python).Source -ArgumentList $captureArguments -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $outputRoot 'capture-stdout.txt') -RedirectStandardError (Join-Path $outputRoot 'capture-stderr.txt')
    if (-not $captureProcess.WaitForExit(15000)) {
        Stop-Process -Id $captureProcess.Id -Force -ErrorAction SilentlyContinue
        $checks.firstSetupCapture = 'unavailable: capture timeout'
    } elseif ($captureProcess.ExitCode -eq 0) {
        $checks.firstSetupCapture = 'passed'
    } else {
        $checks.firstSetupCapture = 'unavailable: native capture failed'
    }
    if (-not [CandidateWindows]::Responsive([long]$firstWindowHandle)) { throw 'First-run UI became unresponsive during capture.' }
    Write-Output ('Acceptance capture: ' + $checks.firstSetupCapture)
    $readyFile = Join-Path $outputRoot 'installer-ready.txt'
    $readyToken = [guid]::NewGuid().ToString('N')
    $waiting = @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-','/UPDATE=1',('/WAITPID=' + $process.Id),('/READYFILE="' + $readyFile + '"'),('/READYTOKEN=' + $readyToken),('/LOG="' + (Join-Path $outputRoot 'update.log') + '"'))
    Write-Output 'Acceptance start: update readiness handshake'
    $update = Start-Process -FilePath $resolvedInstaller -ArgumentList $waiting -WindowStyle Hidden -PassThru
    $readyDeadline = (Get-Date).AddSeconds(30)
    while (-not (Test-Path -LiteralPath $readyFile) -and (Get-Date) -lt $readyDeadline) {
        Start-Sleep -Milliseconds 100
    }
    if (-not (Test-Path -LiteralPath $readyFile)) { throw 'Installer never acknowledged readiness.' }
    if ([IO.File]::ReadAllText($readyFile) -ne $readyToken) { throw 'Installer readiness token did not match.' }
    if ($update.HasExited) { throw 'Installer exited before the application released its files.' }
    $checks.elevatedReadinessHandshake = 'passed'
    Stop-Candidate
    if (-not $update.WaitForExit(120000)) { throw 'Installer update did not finish.' }
    if ($update.ExitCode -ne 0) { throw 'Installer update failed.' }
    Start-Sleep -Seconds 8
    if (-not (Get-Process CrossTrans2 -ErrorAction SilentlyContinue)) { throw 'Update did not relaunch the candidate.' }
    Stop-Candidate
    $checks.updateAndRestart = 'passed'
    Write-Output 'Acceptance passed: update and relaunch'
    Run-Setup 'reinstall' @()
    Write-Output 'Acceptance start: uninstall'
    $remove = Start-Process -FilePath $uninstaller -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',('/LOG="' + (Join-Path $outputRoot 'uninstall.log') + '"')) -WindowStyle Hidden -PassThru
    if (-not $remove.WaitForExit(120000)) {
        Stop-Process -Id $remove.Id -Force -ErrorAction SilentlyContinue
        throw 'Uninstall timed out after 120 seconds.'
    }
    if ($remove.ExitCode -ne 0 -or (Test-Path -LiteralPath $appExe)) { throw 'Uninstall failed.' }
    if ((Test-Path -LiteralPath $startLink) -or (Test-Path -LiteralPath $desktopLink)) { throw 'Shortcuts survived uninstall.' }
    foreach ($folder in @($profile1,$profile2)) {
        if ([IO.File]::ReadAllText((Join-Path $folder 'acceptance-marker.txt')) -ne $marker) { throw 'Personal data was modified or removed.' }
    }
    $checks.uninstallPreservesBothProfiles = 'passed'
    Write-Output 'Acceptance passed: uninstall retains both profiles'
} finally {
    Get-CandidateWindows | Set-Content -LiteralPath (Join-Path $outputRoot 'last-windows.txt') -Encoding utf8
    $appLogs = Join-Path $profile2 'logs'
    if (Test-Path -LiteralPath $appLogs) {
        Copy-Item -LiteralPath $appLogs -Destination (Join-Path $outputRoot 'app-logs') -Recurse
    }
    Stop-Candidate
    $checks | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $outputRoot 'results.json') -Encoding utf8
}
