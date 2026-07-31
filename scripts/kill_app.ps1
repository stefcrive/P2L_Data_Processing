param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path.TrimEnd(
    [IO.Path]::DirectorySeparatorChar,
    [IO.Path]::AltDirectorySeparatorChar
)
$escapedRoot = [regex]::Escape($projectRoot)
$backendPattern = "services\.irms_api\.api\.main:app"

function Get-IrmsProcesses {
    $matches = foreach ($process in @(Get-CimInstance Win32_Process)) {
        $commandLine = [string]$process.CommandLine
        if ([string]::IsNullOrWhiteSpace($commandLine)) {
            continue
        }

        $isProjectCommand = $commandLine -match $escapedRoot
        $isLauncher = $process.Name -ieq "cmd.exe" -and $isProjectCommand -and (
            $commandLine -match "\\start_app(?:_build)?\.bat" -or
            $commandLine -match "\\scripts\\launch_backend\.bat"
        )
        $isFrontend = $process.Name -ieq "node.exe" -and
            $isProjectCommand -and
            $commandLine -match "\\apps\\web\\" -and
            $commandLine -match "(?:next|npm(?:-cli)?\.js)"
        $isBackend = $process.Name -ieq "python.exe" -and
            $commandLine -match $backendPattern

        if ($isLauncher -or $isFrontend -or $isBackend) {
            [pscustomobject]@{
                ProcessId = [int]$process.ProcessId
                Name = [string]$process.Name
                CommandLine = $commandLine
                Priority = if ($isLauncher) { 0 } elseif ($isFrontend) { 1 } else { 2 }
            }
        }
    }

    @($matches | Sort-Object Priority, ProcessId -Unique)
}

$targets = @(Get-IrmsProcesses)
if ($targets.Count -eq 0) {
    Write-Host "No running IRMS application processes were found."
    exit 0
}

Write-Host "IRMS application processes:"
foreach ($target in $targets) {
    Write-Host ("  PID {0,-6} {1,-12} {2}" -f $target.ProcessId, $target.Name, $target.CommandLine)
}

if ($DryRun) {
    Write-Host ""
    Write-Host "Dry run only; no processes were stopped."
    exit 0
}

Write-Host ""
foreach ($target in $targets) {
    if (Get-Process -Id $target.ProcessId -ErrorAction SilentlyContinue) {
        Write-Host "Stopping PID $($target.ProcessId) and its child processes..."
        $savedErrorPreference = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        & taskkill.exe /PID $target.ProcessId /T /F 2>&1 | Out-Null
        $ErrorActionPreference = $savedErrorPreference
    }
}

Start-Sleep -Milliseconds 500
$remaining = @(Get-IrmsProcesses)
if ($remaining.Count -gt 0) {
    Write-Error "Some IRMS application processes could not be stopped: $($remaining.ProcessId -join ', ')"
    exit 1
}

Write-Host "All IRMS application processes were stopped."
