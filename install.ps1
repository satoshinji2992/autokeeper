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

function Test-NodeReady {
    if (-not (Get-Command node -ErrorAction SilentlyContinue) -or -not (Get-Command npm -ErrorAction SilentlyContinue)) { return $false }
    try {
        $NodeVersion = & node --version
        return ($LASTEXITCODE -eq 0 -and $NodeVersion -match '^v(\d+)\.' -and [int]$Matches[1] -ge 18)
    } catch { return $false }
}

if (-not (Test-NodeReady)) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Fail "需要 Node.js 18+ 和 npm，但未找到 winget。请从 https://nodejs.org 安装 LTS 版，重新打开终端后再运行此脚本。"
    }
    Write-Host "安装 Node.js LTS（掷骰组件需要，系统可能请求安装权限）…"
    winget install --id OpenJS.NodeJS.LTS --exact --source winget --accept-package-agreements --accept-source-agreements --disable-interactivity
    if ($LASTEXITCODE -notin @(0, 3010)) { Fail "Node.js 安装未完成，错误码 $LASTEXITCODE。请查看上方的安装信息，解决后重新运行此脚本。" }
    $MachinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$MachinePath;$UserPath;$env:Path"
    if (-not (Test-NodeReady)) { Fail "Node.js 安装后仍无法使用。请重新打开终端，确认 node --version 不低于 18、npm --version 可运行，再运行此脚本。" }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "安装 uv（Python 环境管理）…"
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    if ($LASTEXITCODE -ne 0) { Fail "uv 没装好：上面是它的报错。检查网络后再运行一次。" }
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Fail "uv 装好了但找不到，开一个新终端再运行一次" }
}
# setup checks the renderer after Python dependencies (including chafa.py) are installed.

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
        try {
            $Release = Invoke-RestMethod "$Api/releases/latest" -Headers @{ 'User-Agent' = 'AutoKeeper-installer' } -TimeoutSec 20
            $Tag = $Release.tag_name
            $Version = $Tag -replace '^v', ''
            if (-not $Tag -or $Version -notmatch '^\d+\.\d+\.\d+([.a-zA-Z0-9+-]*)$') { throw "未取得有效的发行版标签。" }
        } catch {
            $GitHubError = $_.Exception.Message
            if ($From -ne "auto" -or $Repo.TrimEnd('/') -ne 'https://github.com/satoshinji2992/autokeeper') {
                Fail "无法查询 GitHub 发行版：$GitHubError 可用 AUTOKEEPER_VERSION 指定版本。"
            }
            try {
                $Package = Invoke-RestMethod 'https://pypi.org/pypi/autokeeper/json' -TimeoutSec 20
                $Version = $Package.info.version
                if ($Version -notmatch '^\d+\.\d+\.\d+([.a-zA-Z0-9+-]*)$') { throw "PyPI 未返回有效版本。" }
            } catch { Fail "无法查询正式版本。GitHub：$GitHubError PyPI：$($_.Exception.Message) 可用 AUTOKEEPER_VERSION 指定版本。" }
            $Tag = "v$Version"
            $From = "pypi"
            Write-Warning "GitHub 版本查询失败，改用 PyPI 的 $Version。原因：$GitHubError"
        }
    } else { $Tag = "v$Version" }
    if ($From -ne "pypi") {
        $Available = $false
        if ($From -eq "auto") {
            try { Invoke-WebRequest -UseBasicParsing -Method Head "https://pypi.org/pypi/autokeeper/$Version/json" | Out-Null; $Available = $true }
            catch { $Available = $false }
        }
        $From = if ($Available) { "pypi" } else { "github" }
    }
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
