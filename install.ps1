# AutoKeeper 一键安装（Windows PowerShell）：
#   irm https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/install.ps1 | iex
# 没有 uv 就先装 uv，再用 uv tool 装好 autokeeper 命令；再运行一次就是更新。
# 已经有 uv 的话，这个脚本做的就是：uv tool install autokeeper，然后 autokeeper setup。
# 可选环境变量同 install.sh：AUTOKEEPER_VERSION、AUTOKEEPER_EXTRAS、AUTOKEEPER_FROM（pypi 或 git）、
# AUTOKEEPER_REF、AUTOKEEPER_NO_MODIFY_PATH。
$ErrorActionPreference = "Stop"
$Repo = if ($env:AUTOKEEPER_REPO) { $env:AUTOKEEPER_REPO } else { "https://github.com/satoshinji2992/autokeeper" }
$Ref = if ($env:AUTOKEEPER_REF) { $env:AUTOKEEPER_REF } else { "main" }
$From = if ($env:AUTOKEEPER_FROM) { $env:AUTOKEEPER_FROM } else { "pypi" }
$Version = $env:AUTOKEEPER_VERSION
$Extras = $env:AUTOKEEPER_EXTRAS

function Fail($message) { throw $message }  # not exit: under irm | iex that would close the window

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "安装 uv（Python 环境管理）…"
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    if ($LASTEXITCODE -ne 0) { Fail "uv 没装好：上面是它的报错。检查网络后再运行一次。" }
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Fail "uv 装好了但找不到，开一个新终端再运行一次" }
}
if (-not (Get-Command node -ErrorAction SilentlyContinue) -or -not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Warning "没找到 Node.js：伤害、理智这些骰子要用它。winget install OpenJS.NodeJS.LTS 装好后运行 autokeeper setup。"
}

$Name = if ($Extras) { "autokeeper[$Extras]" } else { "autokeeper" }
if ($From -eq "pypi") {
    try { Invoke-WebRequest -UseBasicParsing -Method Head "https://pypi.org/pypi/autokeeper/json" | Out-Null }
    catch { Write-Warning "PyPI 上暂时没有 autokeeper，改从 GitHub 装（$Ref）。"; $From = "git" }
}
if ($From -eq "git") {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "从 GitHub 装需要 git：winget install Git.Git，装好后开新终端再运行" }
    $Spec = "$Name @ git+$Repo@$Ref"
} elseif ($Version) {
    $Spec = "$Name==$Version"
} else {
    $Spec = $Name
}

Write-Host "安装 autokeeper 命令（第一次要下载依赖，几分钟）…"
uv tool install --force --refresh-package autokeeper --python 3.12 $Spec
if ($LASTEXITCODE -ne 0) { Fail "autokeeper 没装好：上面是 uv 的报错。检查网络后再运行一次。" }
$Bin = (uv tool dir --bin).Trim()

Write-Host "初始化…"
& (Join-Path $Bin "autokeeper.exe") setup
if ($LASTEXITCODE -ne 0) { Fail "初始化没完成：上面是报错。解决后运行 autokeeper setup。" }

if (-not $env:AUTOKEEPER_NO_MODIFY_PATH) {
    try { uv tool update-shell *> $null; $ok = $LASTEXITCODE -eq 0 } catch { $ok = $false }  # Windows PowerShell 5 throws on a redirected stderr line
    if (-not $ok) { Write-Warning "没能把 $Bin 加进 PATH：在「编辑系统环境变量」里把它加到用户的 Path。" }
}
Write-Host "装好了。开一个新终端，在任意目录输入 autokeeper 开始。"
