function Invoke-ProcessWithTimeout {
    param(
        [Parameter(Mandatory)] [string]$FilePath,
        [Parameter(Mandatory)] [string[]]$ArgumentList,
        [int]$TimeoutSeconds = 600,
        [switch]$AllowFailure,
        [switch]$Quiet
    )

    $utf8 = [System.Text.UTF8Encoding]::new($false)
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $FilePath
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.StandardOutputEncoding = $utf8
    $startInfo.StandardErrorEncoding = $utf8
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
