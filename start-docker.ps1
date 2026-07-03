#requires -Version 7.0
[CmdletBinding()]
param(
    [switch]$NoBuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvPath = Join-Path $Root ".env"
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = $Utf8NoBom
[Console]::InputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom

function Invoke-ProcessWithTimeout {
    param(
        [Parameter(Mandatory)] [string]$FilePath,
        [Parameter(Mandatory)] [string[]]$ArgumentList,
        [int]$TimeoutSeconds = 600,
        [switch]$AllowFailure,
        [switch]$Quiet
    )

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $FilePath
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.StandardOutputEncoding = $Utf8NoBom
    $startInfo.StandardErrorEncoding = $Utf8NoBom
    foreach ($argument in $ArgumentList) {
        [void]$startInfo.ArgumentList.Add($argument)
    }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) {
            throw "无法启动进程: $FilePath"
        }
        $stdoutLines = [System.Collections.Generic.List[string]]::new()
        $stderrLines = [System.Collections.Generic.List[string]]::new()
        $stdoutClosed = $false
        $stderrClosed = $false
        $stdoutTask = $process.StandardOutput.ReadLineAsync()
        $stderrTask = $process.StandardError.ReadLineAsync()
        $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

        while (-not ($process.HasExited -and $stdoutClosed -and $stderrClosed)) {
            while (-not $stdoutClosed -and $stdoutTask.IsCompleted) {
                $line = $stdoutTask.GetAwaiter().GetResult()
                if ($null -eq $line) {
                    $stdoutClosed = $true
                }
                else {
                    [void]$stdoutLines.Add($line)
                    if (-not $Quiet) { Write-Host $line }
                    $stdoutTask = $process.StandardOutput.ReadLineAsync()
                }
            }
            while (-not $stderrClosed -and $stderrTask.IsCompleted) {
                $line = $stderrTask.GetAwaiter().GetResult()
                if ($null -eq $line) {
                    $stderrClosed = $true
                }
                else {
                    [void]$stderrLines.Add($line)
                    if (-not $Quiet) { Write-Host $line }
                    $stderrTask = $process.StandardError.ReadLineAsync()
                }
            }
            if (-not $process.HasExited -and $stopwatch.Elapsed.TotalSeconds -ge $TimeoutSeconds) {
                $process.Kill($true)
                $process.WaitForExit()
                throw "命令执行超时（${TimeoutSeconds}s）: $FilePath $($ArgumentList -join ' ')"
            }
            Start-Sleep -Milliseconds 50
        }
        $process.WaitForExit()
        $stdout = ($stdoutLines -join [Environment]::NewLine).Trim()
        $stderr = ($stderrLines -join [Environment]::NewLine).Trim()
        if ($process.ExitCode -ne 0 -and -not $AllowFailure) {
            throw "命令失败（exit $($process.ExitCode)）: $FilePath $($ArgumentList -join ' ')`n$stderr"
        }
        return [pscustomobject]@{
            ExitCode = $process.ExitCode
            Stdout = $stdout
            Stderr = $stderr
        }
    }
    finally {
        $process.Dispose()
    }
}

function Get-ConfiguredPort {
    $content = [System.IO.File]::ReadAllText($EnvPath, [System.Text.Encoding]::UTF8)
    $match = [regex]::Match($content, "(?m)^[\t ]*PORT[\t ]*=([0-9]+)[\t ]*$")
    if ($match.Success) { return $match.Groups[1].Value }
    return "8787"
}

$succeeded = $false
Push-Location $Root
try {
    & (Join-Path $Root "setup.ps1")
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "找不到 docker，请先安装并启动 Docker Desktop。"
    }
    [void](Invoke-ProcessWithTimeout -FilePath "docker" -ArgumentList @("version", "--format", "{{.Server.Version}}") -TimeoutSeconds 30 -Quiet)
    [void](Invoke-ProcessWithTimeout -FilePath "docker" -ArgumentList @("compose", "version") -TimeoutSeconds 30 -Quiet)

    $arguments = @("compose", "up", "-d", "--remove-orphans")
    if (-not $NoBuild) {
        $arguments += "--build"
    }
    [void](Invoke-ProcessWithTimeout -FilePath "docker" -ArgumentList $arguments -TimeoutSeconds 900)

    $deadline = [DateTime]::UtcNow.AddSeconds(75)
    $status = ""
    do {
        $inspectParameters = @{
            FilePath = "docker"
            ArgumentList = @(
                "inspect",
                "--format",
                "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
                "clickup-2api"
            )
            TimeoutSeconds = 15
            AllowFailure = $true
            Quiet = $true
        }
        $result = Invoke-ProcessWithTimeout @inspectParameters
        $status = $result.Stdout.Trim()
        if ($status -in @("healthy", "running")) { break }
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)

    if ($status -notin @("healthy", "running")) {
        [void](Invoke-ProcessWithTimeout -FilePath "docker" -ArgumentList @("compose", "logs", "--tail", "80", "api") -TimeoutSeconds 30 -AllowFailure)
        throw "容器未能进入健康状态，当前状态: $status"
    }

    [void](Invoke-ProcessWithTimeout -FilePath "docker" -ArgumentList @("compose", "ps") -TimeoutSeconds 30)
    $port = Get-ConfiguredPort
    Write-Host "启动成功：http://127.0.0.1:$port"
    Write-Host "API_KEY 统一读取自 .env；客户端必须使用该值。"
    $succeeded = $true
}
catch {
    Write-Host "启动失败：$($_.Exception.Message)" -ForegroundColor Red
}
finally {
    Pop-Location
    Write-Host "DONE"
}

if (-not $succeeded) {
    exit 1
}
