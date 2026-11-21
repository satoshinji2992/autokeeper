# AutoKeeper installer for Windows PowerShell.
#   irm https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/install.ps1 | iex
# Run again to update. For optional environment variables, see install.sh.
# Keep this file ASCII so Windows PowerShell 5 can run it directly or through irm.
$ErrorActionPreference = "Stop"
$Version = $env:AUTOKEEPER_VERSION
$Extras = $env:AUTOKEEPER_EXTRAS
$InstallOptions = @()
if ($env:AUTOKEEPER_REGION -eq "cn") {
    $InstallOptions = @("--default-index", "https://pypi.tuna.tsinghua.edu.cn/simple", "--python-preference", "system")
}

function Fail($message) { throw $message }  # not exit: under irm | iex that would close the window

function Test-NodeReady {
    if (-not (Get-Command node -ErrorAction SilentlyContinue) -or -not (Get-Command npm -ErrorAction SilentlyContinue)) { return $false }
    try {
        $NodeVersion = & node --version
        return ($LASTEXITCODE -eq 0 -and $NodeVersion -match '^v(\d+)\.' -and [int]$Matches[1] -ge 22)
    } catch { return $false }
}

if (-not (Test-NodeReady)) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Fail "Install Node.js 22 or newer with npm from https://nodejs.org, then reopen PowerShell and run this installer again."
    }
    Write-Host "Installing Node.js LTS for dice rolls. Windows may ask for permission."
    winget install --id OpenJS.NodeJS.LTS --exact --source winget --accept-package-agreements --accept-source-agreements --disable-interactivity
    if ($LASTEXITCODE -notin @(0, 3010)) { Fail "Node.js installation failed with exit code $LASTEXITCODE. Check the output above before retrying." }
    $MachinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$MachinePath;$UserPath;$env:Path"
    if (-not (Test-NodeReady)) { Fail "Reopen PowerShell. Check that node --version reports 22 or newer and npm --version works before retrying." }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv to manage the Python environment."
    if ($env:AUTOKEEPER_NO_MODIFY_PATH) { $env:UV_NO_MODIFY_PATH = "1" }
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    if ($LASTEXITCODE -ne 0) { Fail "uv installation failed. Check the output above before retrying." }
    $UvInstallDir = if ($env:UV_INSTALL_DIR) { $env:UV_INSTALL_DIR } else { "$env:USERPROFILE\.local\bin" }
    $env:Path = "$UvInstallDir;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Fail "uv is not on PATH. Reopen PowerShell and run this installer again." }
}
# setup checks the renderer after Python dependencies (including chafa.py) are installed.

$Name = if ($Extras) { "autokeeper[$Extras]" } else { "autokeeper" }
if ($Version) {
    $Version = $Version -replace '^v', ''
    if ($Version -notmatch '^\d+\.\d+\.\d+([.a-zA-Z0-9+-]*)$') { Fail "Invalid AUTOKEEPER_VERSION." }
    $Spec = "$Name==$Version"
} else {
    $Spec = $Name
}

Write-Host "Installing AutoKeeper. The first installation downloads dependencies."
uv tool install --force --refresh-package autokeeper --python 3.12 @InstallOptions $Spec
if ($LASTEXITCODE -ne 0) { Fail "AutoKeeper installation failed. Check the uv output above before retrying." }
$Bin = (uv tool dir --bin).Trim()

Write-Host "Setting up AutoKeeper."
$SetupOptions = @()
if ($env:AUTOKEEPER_NO_LOGIN) { $SetupOptions = @("--no-login") }
& (Join-Path $Bin "autokeeper.exe") setup @SetupOptions
if ($LASTEXITCODE -ne 0) { Fail "Setup failed. Check the output above, then run autokeeper setup." }

if (-not $env:AUTOKEEPER_NO_MODIFY_PATH) {
    try { uv tool update-shell *> $null; $ok = $LASTEXITCODE -eq 0 } catch { $ok = $false }  # Windows PowerShell 5 throws on a redirected stderr line
    if (-not $ok) { Write-Warning "Could not update PATH. Add $Bin to your user Path in Windows Environment Variables." }
}
Write-Host "Installed. Open a new terminal and run autokeeper."
