<#
.SYNOPSIS
    Bootstrap script for LocalMind.
    Finds Python 3.11+, creates a venv, installs dependencies, and starts the server.
    If no suitable Python is found on PATH, downloads a portable embeddable build
    into the project directory and uses that instead.
#>

param(
    [switch]$SkipServer,
    [string]$Host_ = "127.0.0.1",
    [int]$Port = 8766
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot  = Split-Path -Parent $MyInvocation.MyCommand.Definition
$VenvDir      = Join-Path $ProjectRoot ".venv"
$LocalPyDir   = Join-Path $ProjectRoot ".python"
$MinMajor     = 3
$MinMinor     = 11

$PY_VERSION   = "3.12.10"
$PY_ZIP_URL   = "https://www.python.org/ftp/python/$PY_VERSION/python-$PY_VERSION-embed-amd64.zip"
$PY_ZIP_NAME  = "python-$PY_VERSION-embed-amd64.zip"

# ── Helpers ──────────────────────────────────────────────────────────────────

function Write-Step($msg) { Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "   $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "   $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "   $msg" -ForegroundColor Red }

function Test-PythonVersion([string]$exe) {
    try {
        $raw = & $exe --version 2>&1
        if ($raw -match "Python (\d+)\.(\d+)") {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            return ($major -gt $MinMajor) -or ($major -eq $MinMajor -and $minor -ge $MinMinor)
        }
    } catch {}
    return $false
}

function Find-Python {
    # 1. Check if .venv already has a working python
    $venvPy = Join-Path $VenvDir "Scripts\python.exe"
    if (Test-Path $venvPy) {
        if (Test-PythonVersion $venvPy) {
            Write-Ok "Found existing venv Python: $venvPy"
            return $venvPy
        }
    }

    # 2. Check project-local portable Python
    $localPy = Join-Path $LocalPyDir "python.exe"
    if (Test-Path $localPy) {
        if (Test-PythonVersion $localPy) {
            Write-Ok "Found project-local Python: $localPy"
            return $localPy
        }
    }

    # 3. Search PATH via where.exe
    $candidates = @()
    try { $candidates += (where.exe python.exe 2>$null) -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ } } catch {}
    try { $candidates += (where.exe python3.exe 2>$null) -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ } } catch {}

    foreach ($c in $candidates) {
        if (-not (Test-Path $c)) { continue }
        if ($c -match "WindowsApps") { continue }
        if (Test-PythonVersion $c) {
            Write-Ok "Found Python on PATH: $c"
            return $c
        }
    }

    # 4. Check common install locations
    $searchPaths = @(
        "C:\Program Files\Python3*\python.exe",
        "C:\Program Files (x86)\Python3*\python.exe",
        "C:\Python3*\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe"
    )
    foreach ($pattern in $searchPaths) {
        $found = Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue | Sort-Object Name -Descending
        foreach ($f in $found) {
            if (Test-PythonVersion $f.FullName) {
                Write-Ok "Found Python at: $($f.FullName)"
                return $f.FullName
            }
        }
    }

    # 5. Check Windows registry
    $regPaths = @(
        "HKLM:\SOFTWARE\Python\PythonCore",
        "HKCU:\SOFTWARE\Python\PythonCore"
    )
    foreach ($rp in $regPaths) {
        if (-not (Test-Path $rp)) { continue }
        $versions = Get-ChildItem $rp -ErrorAction SilentlyContinue | Sort-Object Name -Descending
        foreach ($v in $versions) {
            $ipKey = Join-Path $v.PSPath "InstallPath"
            if (-not (Test-Path $ipKey)) { continue }
            $props = Get-ItemProperty $ipKey -ErrorAction SilentlyContinue
            $exePath = $null
            if ($props.PSObject.Properties["ExecutablePath"]) {
                $exePath = $props.ExecutablePath
            } elseif ($props.PSObject.Properties["(default)"]) {
                $exePath = Join-Path $props."(default)" "python.exe"
            } elseif ($props."(Default)") {
                $exePath = Join-Path $props."(Default)" "python.exe"
            }
            if ($exePath -and (Test-Path $exePath) -and (Test-PythonVersion $exePath)) {
                Write-Ok "Found Python via registry: $exePath"
                return $exePath
            }
        }
    }

    return $null
}

function Install-PortablePython {
    Write-Step "Downloading portable Python $PY_VERSION into project..."
    $zipPath = Join-Path $env:TEMP $PY_ZIP_NAME

    Invoke-WebRequest -Uri $PY_ZIP_URL -OutFile $zipPath -UseBasicParsing
    if (Test-Path $LocalPyDir) { Remove-Item $LocalPyDir -Recurse -Force }
    Expand-Archive -Path $zipPath -DestinationPath $LocalPyDir -Force
    Remove-Item $zipPath -ErrorAction SilentlyContinue

    # Enable site-packages by uncommenting "import site" in the ._pth file
    $pthFile = Get-ChildItem $LocalPyDir -Filter "python*._pth" | Select-Object -First 1
    if ($pthFile) {
        (Get-Content $pthFile.FullName) -replace '#import site','import site' | Set-Content $pthFile.FullName
        Write-Ok "Enabled site-packages in $($pthFile.Name)"
    }

    # Install pip
    Write-Step "Installing pip..."
    $getPip = Join-Path $env:TEMP "get-pip.py"
    Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip -UseBasicParsing
    & (Join-Path $LocalPyDir "python.exe") $getPip --no-warn-script-location 2>&1 | Out-Null
    Remove-Item $getPip -ErrorAction SilentlyContinue

    $exe = Join-Path $LocalPyDir "python.exe"
    Write-Ok "Portable Python ready: $exe"
    return $exe
}

function New-Venv([string]$pythonExe) {
    Write-Step "Creating virtual environment..."

    # Portable embeddable Python doesn't have venv — install virtualenv instead
    $savedPref = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $hasVenv = & $pythonExe -c "import venv; print('ok')" 2>$null
    $ErrorActionPreference = $savedPref

    if ($hasVenv -eq "ok") {
        & $pythonExe -m venv $VenvDir
    } else {
        Write-Warn "venv module not available, installing virtualenv..."
        $ErrorActionPreference = "Continue"
        & $pythonExe -m pip install virtualenv --no-warn-script-location 2>&1 | Out-Null
        & $pythonExe -m virtualenv $VenvDir 2>&1 | Out-Null
        $ErrorActionPreference = $savedPref
    }

    if (-not (Test-Path (Join-Path $VenvDir "Scripts\python.exe"))) {
        Write-Err "Failed to create virtual environment!"
        exit 1
    }
    Write-Ok "Virtual environment created at $VenvDir"
}

function Install-Dependencies {
    Write-Step "Installing project dependencies (this may take a few minutes)..."
    $py = Join-Path $VenvDir "Scripts\python.exe"

    $savedPref = $ErrorActionPreference
    $ErrorActionPreference = "Continue"

    & $py -m pip install --upgrade pip 2>&1 | Out-Null

    # llama-cpp-python needs a C++ compiler to build from source.
    # Use the pre-built wheels from the community index first.
    $llamaWheelIndex = "https://abetlen.github.io/llama-cpp-python/whl/cpu"
    Write-Host "   Installing llama-cpp-python (pre-built CPU wheel)..." -ForegroundColor DarkGray
    & $py -m pip install llama-cpp-python --extra-index-url $llamaWheelIndex --prefer-binary --no-warn-script-location 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Pre-built wheel not found, trying default index..."
        & $py -m pip install llama-cpp-python --prefer-binary --no-warn-script-location 2>&1 | Out-Null
    }

    & $py -m pip install -e "$ProjectRoot" --no-warn-script-location 2>&1 | ForEach-Object { Write-Host "   $_" -ForegroundColor DarkGray }

    $ErrorActionPreference = $savedPref

    if ($LASTEXITCODE -ne 0) {
        Write-Err "Dependency installation failed!"
        exit 1
    }
    Write-Ok "All dependencies installed."
}

# ── Main ─────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "========================================" -ForegroundColor Magenta
Write-Host "       LocalMind Setup & Launch         " -ForegroundColor Magenta
Write-Host "========================================" -ForegroundColor Magenta

# Step 1: Find or download Python
Write-Step "Looking for Python >= $MinMajor.$MinMinor ..."
$pythonExe = Find-Python

if (-not $pythonExe) {
    Write-Warn "No suitable Python found on this system."
    $pythonExe = Install-PortablePython
}

$pyVer = & $pythonExe --version 2>&1
Write-Ok "Using: $pyVer ($pythonExe)"

# Step 2: Create venv if needed
$venvPy = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    New-Venv $pythonExe
} else {
    if (Test-PythonVersion $venvPy) {
        Write-Ok "Virtual environment already exists."
    } else {
        Write-Warn "Existing venv has wrong Python version, recreating..."
        Remove-Item $VenvDir -Recurse -Force
        New-Venv $pythonExe
    }
}

# Step 3: Install dependencies if not already done
$savedPref = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$depsCheck = & (Join-Path $VenvDir "Scripts\python.exe") -c "import local_mind, faster_whisper; print('ok')" 2>$null
$ErrorActionPreference = $savedPref

if ($depsCheck -ne "ok") {
    Install-Dependencies
} else {
    Write-Ok "Dependencies already installed."
}

# Step 4: Start server
if (-not $SkipServer) {
    Write-Step "Starting LocalMind server on http://${Host_}:${Port} ..."
    Write-Host ""
    & (Join-Path $VenvDir "Scripts\python.exe") -m local_mind serve --host $Host_ --port $Port
} else {
    Write-Ok "Setup complete. Run the server with:"
    Write-Host "   .\.venv\Scripts\python.exe -m local_mind serve" -ForegroundColor White
}
