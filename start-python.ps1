#requires -Version 7.0
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = $Utf8NoBom
[Console]::InputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom
. (Join-Path $Root "scripts/process-utils.ps1")

$EnvPath = Join-Path $Root ".env"
$VenvPath = Join-Path $Root ".venv"
$PythonPath = Join-Path $VenvPath "Scripts/python.exe"
$PidPath = Join-Path $Root ".python-server.pid"
$LogDirectory = Join-Path $Root "logs"
$StdoutLog = Join-Path $LogDirectory "python-server.stdout.log"
$StderrLog = Join-Path $LogDirectory "python-server.stderr.log"
$RequirementsMarker = Join-Path $VenvPath ".requirements.sha256"

function Get-EnvValue {
    param([string]$Name, [string]$Default = "")
    $content = [System.IO.File]::ReadAllText($EnvPath, [System.Text.Encoding]::UTF8)
    $match = [regex]::Match(
        $content,
        "(?m)^[\t ]*$([regex]::Escape($Name))[\t ]*=(.*?)[\t ]*\r?$"
    )
    if ($match.Success) { return $match.Groups[1].Value }
    return $Default
}

function Test-ServiceHealth {
    param([string]$Url)
    try {
        $response = Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec 2
        return $response.status -eq "ok"
    }
    catch {
        return $false
    }
}

function Stop-StartedProcess {
    param([System.Diagnostics.Process]$Process)
    if ($Process -and -not $Process.HasExited) {
        $Process.Kill($true)
        [void]$Process.WaitForExit(5000)
    }
}

function Invoke-PythonStart {
    & (Join-Path $Root "setup.ps1")

    $port = Get-EnvValue "PORT" "8787"
    $healthUrl = "http://127.0.0.1:$port/health"
    if (Test-Path -LiteralPath $PidPath) {
        $existingPid = [System.IO.File]::ReadAllText($PidPath, [System.Text.Encoding]::UTF8).Trim()
        if ($existingPid -match "^[0-9]+$") {
            $existing = Get-Process -Id ([int]$existingPid) -ErrorAction SilentlyContinue
            if ($existing -and (Test-ServiceHealth $healthUrl)) {
                Write-Host "Python 服务已在运行：http://127.0.0.1:$port（PID $existingPid）"
                return
            }
        }
        Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
    }

    if (Test-ServiceHealth $healthUrl) {
        throw "端口 $port 已被其他服务占用；如果 Docker 正在运行，请先执行 docker compose down。"
    }
    $systemPython = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $systemPython) {
        throw "找不到 Python，请先安装 Python 3.10 或更高版本。"
    }

    if (-not (Test-Path -LiteralPath $PythonPath)) {
        Write-Host "正在创建 Python 虚拟环境..."
        [void](Invoke-ProcessWithTimeout -FilePath $systemPython -ArgumentList @("-m", "venv", $VenvPath) -TimeoutSeconds 180)
    }

    $requirementsHash = (Get-FileHash -LiteralPath (Join-Path $Root "requirements.txt") -Algorithm SHA256).Hash
    $installedHash = if (Test-Path -LiteralPath $RequirementsMarker) {
        [System.IO.File]::ReadAllText($RequirementsMarker, [System.Text.Encoding]::UTF8).Trim()
    } else { "" }
    if ($requirementsHash -ne $installedHash) {
        Write-Host "正在安装或更新 Python 依赖..."
        [void](Invoke-ProcessWithTimeout -FilePath $PythonPath -ArgumentList @("-m", "pip", "install", "-r", (Join-Path $Root "requirements.txt")) -TimeoutSeconds 900)
        [System.IO.File]::WriteAllText($RequirementsMarker, $requirementsHash + "`n", $Utf8NoBom)
    }

    New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
    Remove-Item -LiteralPath $StdoutLog, $StderrLog -Force -ErrorAction SilentlyContinue
    $startParameters = @{
        FilePath = $PythonPath
        ArgumentList = @("main.py")
        WorkingDirectory = $Root
        RedirectStandardOutput = $StdoutLog
        RedirectStandardError = $StderrLog
        WindowStyle = "Hidden"
        PassThru = $true
    }
    $previousPythonUtf8 = $env:PYTHONUTF8
    try {
        $env:PYTHONUTF8 = "1"
        $process = Start-Process @startParameters
    }
    finally {
        if ($null -eq $previousPythonUtf8) {
            Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue
        }
        else {
            $env:PYTHONUTF8 = $previousPythonUtf8
        }
    }
    [System.IO.File]::WriteAllText($PidPath, "$($process.Id)`n", $Utf8NoBom)

    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        if ($process.HasExited) {
            $stderr = if (Test-Path $StderrLog) { Get-Content -LiteralPath $StderrLog -Raw -Encoding UTF8 } else { "" }
            throw "Python 服务启动失败：$stderr"
        }
        if (Test-ServiceHealth $healthUrl) {
            Write-Host "Python 服务启动成功：http://127.0.0.1:$port（PID $($process.Id)）"
            Write-Host "日志目录：$LogDirectory"
            return
        }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)

    Stop-StartedProcess $process
    Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
    throw "Python 服务未在 30 秒内通过健康检查"
}

$failure = $null
try {
    Invoke-PythonStart
}
catch {
    $failure = $_
    Write-Host "启动失败：$($_.Exception.Message)" -ForegroundColor Red
}
finally {
    Write-Host "DONE"
}
if ($failure) {
    throw $failure
}
