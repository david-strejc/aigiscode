# AigisCode Installer for Windows
# Usage: irm https://raw.githubusercontent.com/david-strejc/aigiscode/main/install.ps1 | iex
#    or: Invoke-WebRequest -Uri https://raw.githubusercontent.com/david-strejc/aigiscode/main/install.ps1 -UseBasicParsing | Invoke-Expression

$ErrorActionPreference = "Stop"

$VERSION = if ($env:AIGISCODE_VERSION) { $env:AIGISCODE_VERSION } else { "0.1.0" }
$INSTALL_DIR = if ($env:AIGISCODE_DIR) { $env:AIGISCODE_DIR } else { "$env:USERPROFILE\.aigiscode" }
$REPO = "david-strejc/aigiscode"
$MIN_PYTHON_MINOR = 12

function Write-Info { param($msg) Write-Host "  > " -ForegroundColor Blue -NoNewline; Write-Host $msg }
function Write-Ok { param($msg) Write-Host "  ✓ " -ForegroundColor Green -NoNewline; Write-Host $msg }
function Write-Warn { param($msg) Write-Host "  ! " -ForegroundColor Yellow -NoNewline; Write-Host $msg }
function Write-Err { param($msg) Write-Host "  ✗ " -ForegroundColor Red -NoNewline; Write-Host $msg }

function Show-Banner {
    Write-Host ""
    Write-Host "    ╔═══════════════════════════════════════╗" -ForegroundColor Magenta
    Write-Host "    ║     AigisCode Installer  v$VERSION       ║" -ForegroundColor Magenta
    Write-Host "    ║     AI-Powered Code Guardian          ║" -ForegroundColor Magenta
    Write-Host "    ╚═══════════════════════════════════════╝" -ForegroundColor Magenta
    Write-Host ""
}

function Find-Python {
    $candidates = @("python3.13", "python3.12", "python3", "python", "py")
    foreach ($cmd in $candidates) {
        try {
            $ver = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($ver) {
                $parts = $ver.Split(".")
                if ([int]$parts[0] -ge 3 -and [int]$parts[1] -ge $MIN_PYTHON_MINOR) {
                    return $cmd
                }
            }
        } catch { }
    }
    # Try py launcher
    try {
        $ver = & py -3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($ver) {
            $parts = $ver.Split(".")
            if ([int]$parts[0] -ge 3 -and [int]$parts[1] -ge $MIN_PYTHON_MINOR) {
                return "py -3"
            }
        }
    } catch { }
    return $null
}

function Install-WithUv {
    Write-Info "Installing with uv (fastest)..."
    try {
        & uv tool install "aigiscode==$VERSION" 2>$null
        if ($LASTEXITCODE -ne 0) { throw "PyPI failed" }
    } catch {
        & uv tool install "git+https://github.com/$REPO.git"
        if ($LASTEXITCODE -ne 0) { throw "uv install failed" }
    }
}

function Install-WithPipx {
    Write-Info "Installing with pipx..."
    try {
        & pipx install "aigiscode==$VERSION" 2>$null
        if ($LASTEXITCODE -ne 0) { throw "PyPI failed" }
    } catch {
        & pipx install "git+https://github.com/$REPO.git"
        if ($LASTEXITCODE -ne 0) { throw "pipx install failed" }
    }
}

function Install-WithVenv {
    param($PythonCmd)

    Write-Info "Installing into isolated venv at $INSTALL_DIR..."
    New-Item -ItemType Directory -Force -Path $INSTALL_DIR | Out-Null

    & $PythonCmd -m venv "$INSTALL_DIR\venv"
    $pip = "$INSTALL_DIR\venv\Scripts\pip.exe"

    & $pip install --upgrade pip --quiet 2>$null

    try {
        & $pip install "aigiscode==$VERSION" --quiet 2>$null
        if ($LASTEXITCODE -ne 0) { throw "PyPI failed" }
    } catch {
        & $pip install "git+https://github.com/$REPO.git" --quiet
        if ($LASTEXITCODE -ne 0) { throw "venv install failed" }
    }

    # Add to user PATH
    $binPath = "$INSTALL_DIR\venv\Scripts"
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -notlike "*$binPath*") {
        [Environment]::SetEnvironmentVariable("Path", "$binPath;$userPath", "User")
        Write-Ok "Added $binPath to user PATH"
        Write-Warn "Restart your terminal for PATH changes to take effect"
    }
}

# ── Main ──────────────────────────────────────────────────────────────────────

Show-Banner

Write-Info "Detected: Windows/$([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture)"

# Find Python
$pythonCmd = Find-Python
if (-not $pythonCmd) {
    Write-Err "Python 3.12+ is required but not found."
    Write-Host ""
    Write-Host "  Install Python from: https://python.org/downloads/" -ForegroundColor Yellow
    Write-Host "  Or with winget:  winget install Python.Python.3.12" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

$pyVer = & $pythonCmd --version 2>&1
Write-Ok "Found $pyVer"

# Try installers: uv > pipx > venv
$installed = $false
if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Ok "Found uv"
    Install-WithUv
    $installed = $true
} elseif (Get-Command pipx -ErrorAction SilentlyContinue) {
    Write-Ok "Found pipx"
    Install-WithPipx
    $installed = $true
} else {
    Write-Info "No uv or pipx found, using venv"
    Install-WithVenv -PythonCmd $pythonCmd
    $installed = $true
}

# Verify
try {
    $ver = & aigiscode --version 2>$null
    Write-Ok "AigisCode installed successfully! ($ver)"
} catch {
    Write-Ok "AigisCode installed. Restart your terminal to use it."
}

Write-Host ""
Write-Host "  Get started:" -ForegroundColor White
Write-Host "    aigiscode analyze .    " -ForegroundColor Green -NoNewline; Write-Host "# Analyze current directory"
Write-Host "    aigiscode --help       " -ForegroundColor Green -NoNewline; Write-Host "# See all commands"
Write-Host ""
Write-Host "    Docs:    https://aigiscode.com/docs" -ForegroundColor Blue
Write-Host "    GitHub:  https://github.com/$REPO" -ForegroundColor Blue
Write-Host ""
