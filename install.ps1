# AutoKeeper 一键安装（Windows PowerShell）：
#   irm https://raw.githubusercontent.com/satoshinji2992/autokeeper/main/install.ps1 | iex
# 没有 uv 就先装 uv，再用 uv tool 装好 autokeeper 命令；再运行一次就是更新。
# 已经有 uv 的话，这个脚本做的就是：uv tool install autokeeper，然后 autokeeper setup。
# 可选环境变量同 install.sh：AUTOKEEPER_VERSION、AUTOKEEPER_EXTRAS、AUTOKEEPER_FROM（auto / pypi / github / git）、
# AUTOKEEPER_REF、AUTOKEEPER_SHA256、AUTOKEEPER_NO_MODIFY_PATH。
$ErrorActionPreference = "Stop"
$Repo = if ($env:AUTOKEEPER_REPO) { $env:AUTOKEEPER_REPO } else { "https://github.com/satoshinji2992/autokeeper" }
$Ref = $env:AUTOKEEPER_REF
$From = if ($env:AUTOKEEPER_FROM) { $env:AUTOKEEPER_FROM } else { "auto" }
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

if (-not (Get-Command chafa -ErrorAction SilentlyContinue)) {  # photos as coloured characters
    if (Get-Command scoop -ErrorAction SilentlyContinue) {
        Write-Host "安装 chafa（把照片画成彩色字符）…"
        scoop install chafa
    }
    if (-not (Get-Command chafa -ErrorAction SilentlyContinue)) {
        Write-Warning "chafa 没装上：场景照片会改用手绘的字符画。想看照片的话，装 Scoop（https://scoop.sh）后运行 scoop install chafa。"
    }
}

$Name = if ($Extras) { "autokeeper[$Extras]" } else { "autokeeper" }
if ($From -notin @("auto", "pypi", "github", "git")) { Fail "AUTOKEEPER_FROM 只能是 auto、pypi、github 或 git。" }
if ($Version) {
    $Version = $Version -replace '^v', ''
    if ($Version -notmatch '^\d+\.\d+\.\d+([.a-zA-Z0-9+-]*)$') { Fail "AUTOKEEPER_VERSION 格式无效。" }
}
if ($From -eq "auto" -and $Ref) { $From = "git" }
if ($From -in @("auto", "github")) {
    if (-not $Version) {
        $Api = $Repo -replace '^https://github.com/', 'https://api.github.com/repos/'
        try { $Release = Invoke-RestMethod "$Api/releases/latest" -Headers @{ 'User-Agent' = 'AutoKeeper-installer' } }
        catch { Fail "无法确认最新发行版。检查网络，或用 AUTOKEEPER_VERSION 指定版本。" }
        $Tag = $Release.tag_name
        $Version = $Tag -replace '^v', ''
        if (-not $Tag -or $Version -notmatch '^\d+\.\d+\.\d+([.a-zA-Z0-9+-]*)$') { Fail "未取得有效的发行版标签。" }
    } else { $Tag = "v$Version" }
    $Available = $false
    if ($From -eq "auto") {
        try { Invoke-WebRequest -UseBasicParsing -Method Head "https://pypi.org/pypi/autokeeper/$Version/json" | Out-Null; $Available = $true }
        catch { $Available = $false }
    }
    $From = if ($Available) { "pypi" } else { "github" }
}
if ($From -eq "git") {
    if ($Version) {
        if ($Ref -and $Ref -notin @($Version, "v$Version")) { Fail "AUTOKEEPER_VERSION 与 AUTOKEEPER_REF 不一致；请只指定一个版本。" }
        if (-not $Ref) { $Ref = "v$Version" }
    } elseif (-not $Ref) { $Ref = "main" }
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "从 GitHub 装需要 git：winget install Git.Git，装好后开新终端再运行" }
    $Spec = "$Name @ git+$Repo@$Ref"
} elseif ($From -eq "github") {
    $Spec = "$Name @ $Repo/releases/download/$Tag/autokeeper-$Version-py3-none-any.whl"
    if ($env:AUTOKEEPER_SHA256) {
        if ($env:AUTOKEEPER_SHA256 -notmatch '^[0-9a-fA-F]{64}$') { Fail "AUTOKEEPER_SHA256 必须是发行包的 SHA256。" }
        $Spec += "#sha256=$($env:AUTOKEEPER_SHA256)"
    }
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
