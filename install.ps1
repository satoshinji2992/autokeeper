# AutoKeeper 一键安装（Windows PowerShell）：
#   irm https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/install.ps1 | iex
# 再运行一次就是更新。可选环境变量同 install.sh：AUTOKEEPER_DIR（默认 %LOCALAPPDATA%\autokeeper）、
# AUTOKEEPER_REF、AUTOKEEPER_EXTRAS、AUTOKEEPER_NO_MODIFY_PATH。
$ErrorActionPreference = "Stop"
$Repo = if ($env:AUTOKEEPER_REPO) { $env:AUTOKEEPER_REPO } else { "https://github.com/satoshinji2992/autokeeper" }
$Ref = if ($env:AUTOKEEPER_REF) { $env:AUTOKEEPER_REF } else { "main" }
$Dir = if ($env:AUTOKEEPER_DIR) { $env:AUTOKEEPER_DIR } else { Join-Path $env:LOCALAPPDATA "autokeeper" }
$Extras = if ($env:AUTOKEEPER_EXTRAS) { $env:AUTOKEEPER_EXTRAS } else { "claude,graphrag" }

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "需要 git：winget install Git.Git，装好后开新终端再运行" }
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "安装 uv（Python 环境管理）…"
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "uv 装好了但找不到，开一个新终端再运行一次" }
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Warning "没找到 Node.js：掷伤害和理智的骰子要用它。winget install OpenJS.NodeJS.LTS 装好后运行 autokeeper setup。"
}

if (Test-Path (Join-Path $Dir ".git")) {
    Write-Host "更新 $Dir …"
    git -C $Dir fetch --quiet --tags origin
    git -C $Dir checkout --quiet $Ref
    git -C $Dir pull --quiet --ff-only origin $Ref 2>$null  # a tag has nothing to pull
} else {
    Write-Host "下载到 $Dir …"
    git clone --quiet --branch $Ref $Repo $Dir
}

Write-Host "安装 autokeeper 命令（第一次要下载依赖，几分钟）…"
uv tool install --quiet --force --python 3.12 --editable "$Dir[$Extras]"
$Bin = (uv tool dir --bin).Trim()

Write-Host "初始化…"
& (Join-Path $Bin "autokeeper.exe") setup

if (-not $env:AUTOKEEPER_NO_MODIFY_PATH) { uv tool update-shell *> $null }
Write-Host "装好了。开一个新终端，在任意目录输入 autokeeper 开始。"
