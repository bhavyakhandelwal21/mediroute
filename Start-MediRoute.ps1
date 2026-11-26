$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
$venvPython = Join-Path $backend '.venv\Scripts\python.exe'

Write-Host 'MediRoute first-run setup' -ForegroundColor Green
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
  Write-Host 'Node.js (including npm) is required. Install the current Node.js LTS version, then run this launcher again.' -ForegroundColor Red
  Start-Process 'https://nodejs.org/en/download'
  Read-Host 'Press Enter to close'
  exit 1
}

if (-not (Test-Path $venvPython)) {
  if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 -m venv (Join-Path $backend '.venv')
  } elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python -m venv (Join-Path $backend '.venv')
  } else {
    Write-Host 'Python 3 is required. Install Python 3.10 or newer, then run this launcher again.' -ForegroundColor Red
    Start-Process 'https://www.python.org/downloads/'
    Read-Host 'Press Enter to close'
    exit 1
  }
  if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
}

if (-not (Test-Path (Join-Path $backend '.venv\Mediroute-dependencies-installed'))) {
  Write-Host 'Installing backend packages (first run only)...' -ForegroundColor Cyan
  & $venvPython -m pip install -r (Join-Path $backend 'requirements.txt')
  if ($LASTEXITCODE -ne 0) { throw 'Backend package installation failed.' }
  New-Item -ItemType File -Path (Join-Path $backend '.venv\Mediroute-dependencies-installed') -Force | Out-Null
}

if (-not (Test-Path (Join-Path $frontend 'node_modules'))) {
  Write-Host 'Installing website packages (first run only)...' -ForegroundColor Cyan
  Push-Location $frontend
  try { & npm install; if ($LASTEXITCODE -ne 0) { throw 'Website package installation failed.' } }
  finally { Pop-Location }
}

Write-Host 'Opening MediRoute servers...' -ForegroundColor Green
$backendCommand = "Set-Location '$backend'; & '$venvPython' -m uvicorn app.main:app --host 127.0.0.1 --port 8001"
Start-Process powershell.exe -ArgumentList '-NoExit','-ExecutionPolicy','Bypass','-Command',$backendCommand -WorkingDirectory $backend
$frontendCommand = "Set-Location '$frontend'; npm run dev -- --host 127.0.0.1 --port 5173"
Start-Process powershell.exe -ArgumentList '-NoExit','-ExecutionPolicy','Bypass','-Command',$frontendCommand -WorkingDirectory $frontend
Start-Sleep -Seconds 5
Start-Process 'http://127.0.0.1:5173'
Write-Host 'MediRoute is opening at http://127.0.0.1:5173' -ForegroundColor Green
Write-Host 'Keep both server windows open while using the app. Close them to stop MediRoute.'
Read-Host 'Press Enter to close this setup window'
