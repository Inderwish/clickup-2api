#requires -Version 7.0
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidPath = Join-Path $Root ".python-server.pid"
$failure = $null
try {
    if (-not (Test-Path -LiteralPath $PidPath)) {
        Write-Host "没有记录正在运行的 Python 服务。"
    }
    else {
        $pidText = [System.IO.File]::ReadAllText($PidPath, [System.Text.Encoding]::UTF8).Trim()
        $process = if ($pidText -match "^[0-9]+$") {
            Get-Process -Id ([int]$pidText) -ErrorAction SilentlyContinue
        } else { $null }
        if ($process) {
            $process.Kill($true)
            if (-not $process.WaitForExit(10000)) {
                throw "停止 Python 服务超时（PID $pidText）"
            }
            $process.Dispose()
            Write-Host "Python 服务已停止（PID $pidText）。"
        }
        else {
            Write-Host "PID 文件已过期，未发现运行中的进程。"
        }
        Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
    }
}
catch {
    $failure = $_
    Write-Host "停止失败：$($_.Exception.Message)" -ForegroundColor Red
}
finally {
    Write-Host "DONE"
}
if ($failure) {
    throw $failure
}
