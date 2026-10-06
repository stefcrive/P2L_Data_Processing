param(
    [int]$BackendPort = 8200,
    [int]$FrontendPort = 3200,
    [switch]$Production,
    [switch]$Demo,
    [switch]$Operational,
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$webDirectory = Join-Path $projectRoot 'apps\web'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { $pythonPath = Join-Path $projectRoot 'ven\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run setup.bat first to install the Python environment.' }
if (-not (Get-Command node.exe -ErrorAction SilentlyContinue)) { throw 'Node.js is required. Install it and run setup.bat.' }
if (-not (Test-Path -LiteralPath (Join-Path $webDirectory 'node_modules'))) { throw 'Run npm install in apps\web before starting.' }
function Get-FreePort([int]$Requested, [int]$Excluded = -1) {
    if ($Requested -lt 1024 -or $Requested -gt 65535) { throw 'Ports must be between 1024 and 65535.' }
    for ($candidate = $Requested; $candidate -le 65535; $candidate++) {
        if ($candidate -eq $Excluded) { continue }
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $candidate)
        try { $listener.Start(); return $candidate } catch [System.Net.Sockets.SocketException] { } finally { $listener.Stop() }
    }
    throw 'No free local port is available.'
}
$BackendPort = Get-FreePort $BackendPort
$FrontendPort = Get-FreePort $FrontendPort $BackendPort
$dataFolder = if ($Operational) { '.data\metrology-operational' } elseif ($Demo) { '.data\metrology-demo' } else { '.data\metrology' }
$env:IRMS_METROLOGY_DATA_DIR = Join-Path $projectRoot $dataFolder
$env:IRMS_METROLOGY_DEMO = if ($Demo -or $Operational) { '1' } else { '0' }
if ($Demo -or $Operational) {
    Push-Location -LiteralPath $projectRoot
    try {
        $seedModule = if ($Operational) { 'services.irms_api.metrology.operational' } else { 'services.irms_api.metrology.demo' }
        & $pythonPath -m $seedModule --data-dir $env:IRMS_METROLOGY_DATA_DIR
        if ($LASTEXITCODE -ne 0) { throw 'Demo initialization failed; existing data has not been overwritten.' }
    } finally { Pop-Location }
}
$runtimeDirectory = Join-Path $env:IRMS_METROLOGY_DATA_DIR 'runtime'
New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
$env:IRMS_API_DATA_DIR = Join-Path $env:IRMS_METROLOGY_DATA_DIR 'exploratory-sessions'
$env:IRMS_API_PROXY_TARGET = "http://127.0.0.1:$BackendPort"
$env:NEXT_PUBLIC_IRMS_API_URL = ''
$env:NEXT_PUBLIC_API_BASE_URL = ''
$env:NEXT_DIST_DIR = ".next-metrology-$FrontendPort"
$launchId = Get-Date -Format 'yyyyMMdd-HHmmss'
$backend = $null
function Stop-MetrologyProcessTree([int]$ProcessId) {
    Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue | ForEach-Object { Stop-MetrologyProcessTree $_.ProcessId }
    Stop-Process -Id $ProcessId -ErrorAction SilentlyContinue
}
try {
    $backendArgs = @('-m', 'uvicorn', 'services.irms_api.api.main:app', '--host', '127.0.0.1', '--port', "$BackendPort")
    if (-not $Production) { $backendArgs += @('--reload', '--reload-dir', (Join-Path $projectRoot 'services')) }
    $backend = Start-Process -FilePath $pythonPath -ArgumentList $backendArgs -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeDirectory "backend-$launchId.log") -RedirectStandardError (Join-Path $runtimeDirectory "backend-$launchId.error.log")
    Write-Host "IRMS Metrology Station: http://127.0.0.1:$FrontendPort/metrology"
    Write-Host "Backend: http://127.0.0.1:$BackendPort | Data: $env:IRMS_METROLOGY_DATA_DIR"
    Write-Host 'Keep this window open. Press Ctrl+C to stop this instance.'
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if ($backend.HasExited) { throw "Backend stopped. See $runtimeDirectory" }
        try { $response = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$BackendPort/metrology/state" -TimeoutSec 2; if ($response.StatusCode -eq 200) { $ready = $true; break } } catch { }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) { throw "Backend did not become ready. See $runtimeDirectory" }
    Set-Location -LiteralPath $webDirectory
    if ($Production) {
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    }
    if (-not $NoBrowser) {
        # A small hidden helper opens the browser only after the route is reachable.
        $browserScript = Join-Path $PSScriptRoot 'open_metrology_when_ready.ps1'
        Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -ArgumentList @('-NoProfile', '-File', "`"$browserScript`"", '-Port', "$FrontendPort") | Out-Null
    }
    if ($Production) { & npm.cmd run start -- --hostname 127.0.0.1 --port $FrontendPort }
    else { & npm.cmd run dev -- --hostname 127.0.0.1 --port $FrontendPort }
    if ($LASTEXITCODE -ne 0) { throw 'Frontend stopped with an error.' }
} finally {
    if ($backend -and -not $backend.HasExited) { Stop-MetrologyProcessTree $backend.Id }
    Set-Location -LiteralPath $projectRoot
}
