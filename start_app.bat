@echo off
setlocal

for %%I in ("%~dp0.") do set "ROOT_DIR=%%~fI"
set "WEB_DIR=%ROOT_DIR%\apps\web"
set "BACKEND_LAUNCHER=%ROOT_DIR%\scripts\launch_backend.bat"
set "VENV_ACTIVATE=%ROOT_DIR%\.venv\Scripts\activate.bat"
set "LEGACY_VENV_ACTIVATE=%ROOT_DIR%\ven\Scripts\activate.bat"
set "VENV_PYTHON=%ROOT_DIR%\.venv\Scripts\python.exe"
set "LEGACY_VENV_PYTHON=%ROOT_DIR%\ven\Scripts\python.exe"
set "RUN_MODE=dev"
set "BACKEND_PORT="
set "FRONTEND_PORT="
set "SHOW_HELP="

call :parse_args %*
if errorlevel 1 exit /b 1
if defined SHOW_HELP exit /b 0

if not defined BACKEND_PORT set "BACKEND_PORT=8000"
if not defined FRONTEND_PORT set "FRONTEND_PORT=3000"

call :validate_port "%BACKEND_PORT%"
if errorlevel 1 (
  echo Invalid value for --backend-port: %BACKEND_PORT%
  exit /b 1
)

call :validate_port "%FRONTEND_PORT%"
if errorlevel 1 (
  echo Invalid value for --frontend-port: %FRONTEND_PORT%
  exit /b 1
)

set "REQUESTED_BACKEND_PORT=%BACKEND_PORT%"
set "REQUESTED_FRONTEND_PORT=%FRONTEND_PORT%"

call :find_free_port %BACKEND_PORT% BACKEND_PORT
if errorlevel 1 (
  echo Could not find a free backend port.
  exit /b 1
)

set "FRONTEND_PORT_CANDIDATE=%FRONTEND_PORT%"
if "%FRONTEND_PORT_CANDIDATE%"=="%BACKEND_PORT%" set /a FRONTEND_PORT_CANDIDATE+=1
call :find_free_port %FRONTEND_PORT_CANDIDATE% FRONTEND_PORT
if errorlevel 1 (
  echo Could not find a free frontend port.
  exit /b 1
)

if not "%REQUESTED_BACKEND_PORT%"=="%BACKEND_PORT%" (
  echo Backend port %REQUESTED_BACKEND_PORT% is busy. Using %BACKEND_PORT% instead.
)

if not "%REQUESTED_FRONTEND_PORT%"=="%FRONTEND_PORT%" (
  echo Frontend port %REQUESTED_FRONTEND_PORT% is busy or unavailable. Using %FRONTEND_PORT% instead.
)

echo Starting IRMS backend and Next.js application in %RUN_MODE% mode...
echo Backend:  http://127.0.0.1:%BACKEND_PORT%
echo Frontend: http://127.0.0.1:%FRONTEND_PORT%
echo.

rem Ensure required directories exist.
if not exist "%WEB_DIR%" (
  echo Could not find apps\web directory.
  exit /b 1
)

rem Ensure Node.js and npm are available.
where node >nul 2>&1
if errorlevel 1 (
  echo Node.js was not found in PATH. Install Node.js and try again.
  exit /b 1
)

where npm >nul 2>&1
if errorlevel 1 (
  echo npm was not found in PATH. Install Node.js/npm and try again.
  exit /b 1
)

rem Ensure Python environment exists for backend.
if not exist "%VENV_ACTIVATE%" (
  if exist "%LEGACY_VENV_ACTIVATE%" (
    set "VENV_ACTIVATE=%LEGACY_VENV_ACTIVATE%"
    set "VENV_PYTHON=%LEGACY_VENV_PYTHON%"
  )
)

if not exist "%VENV_PYTHON%" (
  if exist "%LEGACY_VENV_PYTHON%" (
    set "VENV_ACTIVATE=%LEGACY_VENV_ACTIVATE%"
    set "VENV_PYTHON=%LEGACY_VENV_PYTHON%"
  )
)

if not exist "%VENV_ACTIVATE%" if not exist "%VENV_PYTHON%" (
  echo Python virtual environment not found. Running setup...
  call "%ROOT_DIR%\setup.bat"
  if errorlevel 1 (
    echo setup.bat failed.
    exit /b 1
  )
  if not exist "%VENV_ACTIVATE%" if not exist "%VENV_PYTHON%" (
    if exist "%LEGACY_VENV_ACTIVATE%" if exist "%LEGACY_VENV_PYTHON%" (
      set "VENV_ACTIVATE=%LEGACY_VENV_ACTIVATE%"
      set "VENV_PYTHON=%LEGACY_VENV_PYTHON%"
    )
  )
  if not exist "%VENV_PYTHON%" (
    echo Python virtual environment still not found after setup.
    exit /b 1
  )
)

if not exist "%VENV_PYTHON%" (
  echo Python executable was not found in virtual environment: %VENV_PYTHON%
  exit /b 1
)

rem Install frontend dependencies if node_modules is missing.
pushd "%WEB_DIR%" || (
  echo Could not switch to apps\web.
  exit /b 1
)

if not exist "node_modules" (
  echo Dependencies not found. Running npm install...
  npm install
  if errorlevel 1 (
    echo npm install failed.
    popd
    exit /b 1
  )
)

rem Launch backend in a separate terminal window.
if not exist "%BACKEND_LAUNCHER%" (
  echo Could not find backend launcher script: %BACKEND_LAUNCHER%
  popd
  exit /b 1
)

if /I "%RUN_MODE%"=="prod" (
  start "IRMS Backend :%BACKEND_PORT%" cmd /k ""%BACKEND_LAUNCHER%" "%ROOT_DIR%" "%VENV_PYTHON%" "%BACKEND_PORT%" "prod""
) else (
  start "IRMS Backend :%BACKEND_PORT%" cmd /k ""%BACKEND_LAUNCHER%" "%ROOT_DIR%" "%VENV_PYTHON%" "%BACKEND_PORT%" "dev""
)

set "NEXT_PUBLIC_IRMS_API_URL=http://127.0.0.1:%BACKEND_PORT%"

if /I "%RUN_MODE%"=="prod" (
  if not exist ".next" (
    echo Production build not found. Running npm run build...
    npm run build
    if errorlevel 1 (
      echo npm run build failed.
      popd
      exit /b 1
    )
  )
  npm run start -- --port %FRONTEND_PORT%
) else (
  rem Launch Next.js in development mode in this window.
  npm run dev -- --port %FRONTEND_PORT%
)

popd
pause

goto :eof

:parse_args
if "%~1"=="" exit /b 0
if /I "%~1"=="--prod" (
  set "RUN_MODE=prod"
  shift
  goto :parse_args
)
if /I "%~1"=="--dev" (
  set "RUN_MODE=dev"
  shift
  goto :parse_args
)
if /I "%~1"=="--backend-port" (
  if "%~2"=="" (
    echo Missing value for --backend-port
    exit /b 1
  )
  set "BACKEND_PORT=%~2"
  shift
  shift
  goto :parse_args
)
if /I "%~1"=="--frontend-port" (
  if "%~2"=="" (
    echo Missing value for --frontend-port
    exit /b 1
  )
  set "FRONTEND_PORT=%~2"
  shift
  shift
  goto :parse_args
)
if /I "%~1"=="--help" (
  call :show_help
  set "SHOW_HELP=1"
  exit /b 0
)
echo Unknown option: %~1
call :show_help
exit /b 1

:show_help
echo Usage: start_app.bat [--dev^|--prod] [--backend-port PORT] [--frontend-port PORT]
echo.
echo   --dev             Start in development mode ^(default^)
echo   --prod            Start in production mode
echo   --backend-port    Preferred backend port ^(default 8000^)
echo   --frontend-port   Preferred frontend port ^(default 3000^)
echo.
echo If a preferred port is busy, the script automatically picks the next free port.
exit /b 0

:validate_port
setlocal
set "PORT_VALUE=%~1"
if not defined PORT_VALUE endlocal & exit /b 1
echo(%PORT_VALUE%| findstr /R "^[0-9][0-9]*$" >nul || (endlocal & exit /b 1)
set /a "PORT_NUM=%PORT_VALUE%"
if %PORT_NUM% lss 1 endlocal & exit /b 1
if %PORT_NUM% gtr 65535 endlocal & exit /b 1
endlocal & exit /b 0

:find_free_port
setlocal EnableDelayedExpansion
set /a "CANDIDATE=%~1"
:find_free_port_loop
call :is_port_in_use !CANDIDATE!
if errorlevel 1 (
  endlocal & set "%~2=%CANDIDATE%" & exit /b 0
)
set /a CANDIDATE+=1
if !CANDIDATE! gtr 65535 (
  endlocal & exit /b 1
)
goto :find_free_port_loop

:is_port_in_use
setlocal
set "IRMS_CHECK_PORT=%~1"
powershell -NoLogo -NoProfile -Command "$port=[int]$env:IRMS_CHECK_PORT; $existing=@(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue); if ($existing.Count -gt 0) { exit 0 }; foreach ($addr in @([System.Net.IPAddress]::Loopback, [System.Net.IPAddress]::IPv6Loopback)) { $listener=$null; try { $listener=[System.Net.Sockets.TcpListener]::new($addr, $port); $listener.Start() } catch { exit 0 } finally { if ($listener -ne $null) { $listener.Stop() } } }; exit 1" >nul 2>&1
set "PS_EXIT=%ERRORLEVEL%"
endlocal & exit /b %PS_EXIT%
