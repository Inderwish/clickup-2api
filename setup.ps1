#requires -Version 7.0
[CmdletBinding()]
param(
    [string]$ApiKey,
    [string]$WorkspaceId,
    [string]$Jwt,
    [string]$EnvPath,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false, $true)
$OutputEncoding = $Utf8NoBom
[Console]::InputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom
$HasExplicitInput = (
    $PSBoundParameters.ContainsKey("ApiKey") -or
    $PSBoundParameters.ContainsKey("WorkspaceId") -or
    $PSBoundParameters.ContainsKey("Jwt")
)

if (-not $EnvPath) {
    $EnvPath = Join-Path $Root ".env"
}
elseif (-not [System.IO.Path]::IsPathRooted($EnvPath)) {
    $EnvPath = Join-Path $Root $EnvPath
}

function Read-EnvMap {
    param([string]$Path)
    $map = [ordered]@{}
    if (-not (Test-Path -LiteralPath $Path)) {
        return $map
    }
    $content = [System.IO.File]::ReadAllText($Path, $Utf8NoBom)
    foreach ($line in $content -split "\r?\n") {
        if ($line -match "^([A-Za-z_][A-Za-z0-9_]*)=(.*)$") {
            $map[$Matches[1]] = $Matches[2]
        }
    }
    return $map
}

function Get-MapValue {
    param($Map, [string]$Name)
    if ($Map.Contains($Name)) { return [string]$Map[$Name] }
    return ""
}

function Read-HiddenText {
    param([string]$Prompt)
    $secure = Read-Host $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function New-ApiKey {
    $bytes = [Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Test-Configured {
    param($Map)
    $hasApiKey = -not [string]::IsNullOrWhiteSpace((Get-MapValue $Map "API_KEY"))
    $hasMulti = (
        -not [string]::IsNullOrWhiteSpace((Get-MapValue $Map "CLICKUP_ACCOUNTS")) -or
        -not [string]::IsNullOrWhiteSpace((Get-MapValue $Map "CLICKUP_ACCOUNTS_JSON"))
    )
    $hasSingle = (
        -not [string]::IsNullOrWhiteSpace((Get-MapValue $Map "CLICKUP_JWT")) -and
        -not [string]::IsNullOrWhiteSpace((Get-MapValue $Map "CLICKUP_WORKSPACE_ID"))
    )
    return $hasApiKey -and ($hasMulti -or $hasSingle)
}

function Assert-SafeValue {
    param([string]$Name, [string]$Value)
    if ($Value -match "[\r\n]") {
        throw "$Name 不能包含换行符"
    }
}

function Write-EnvAtomically {
    param($Map, [string]$Path)
    $order = @(
        "CLICKUP_ACCOUNTS_JSON",
        "CLICKUP_ACCOUNTS",
        "CLICKUP_JWT",
        "CLICKUP_WORKSPACE_ID",
        "CLICKUP_REFRESH_JWT",
        "CLICKUP_TOKEN",
        "CLICKUP_COOKIE",
        "CLICKUP_EXTRA_HEADERS",
        "CLICKUP_BASE_URL",
        "CLICKUP_GRAPHQL_PATH",
        "CLICKUP_ANONYMOUS_ID",
        "CLICKUP_CLIENT_VERSION",
        "CLICKUP_LOCALE",
        "CLICKUP_SURFACE",
        "CLICKUP_TZ_OFFSET",
        "CLICKUP_SD_TAB_ID",
        "API_KEY",
        "CLICKUP_MOCK",
        "HOST",
        "PORT"
    )
    $lines = [Collections.Generic.List[string]]::new()
    [void]$lines.Add("# clickup-2api 配置（UTF-8，请勿提交）")
    [void]$lines.Add("# 首次配置可重新运行：pwsh ./setup.ps1 -Force")
    [void]$lines.Add("")
    $written = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($name in $order) {
        if ($Map.Contains($name)) {
            [void]$lines.Add("$name=$($Map[$name])")
            [void]$written.Add($name)
        }
    }
    foreach ($entry in $Map.GetEnumerator()) {
        if (-not $written.Contains([string]$entry.Key)) {
            [void]$lines.Add("$($entry.Key)=$($entry.Value)")
        }
    }
    $content = ($lines -join "`n") + "`n"
    $tempPath = "$Path.setup-tmp"
    try {
        [System.IO.File]::WriteAllText($tempPath, $content, $Utf8NoBom)
        $verified = Read-EnvMap $tempPath
        foreach ($entry in $Map.GetEnumerator()) {
            if (-not $verified.Contains($entry.Key) -or [string]$verified[$entry.Key] -cne [string]$entry.Value) {
                throw ".env 写入校验失败：$($entry.Key)"
            }
        }
        [System.IO.File]::Move($tempPath, $Path, $true)
    }
    finally {
        Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-Setup {
    $map = Read-EnvMap $EnvPath
    if (-not $Force -and -not $HasExplicitInput -and (Test-Configured $map)) {
        Write-Host "首次配置已完成，继续使用现有 .env（不会覆盖）。"
        return
    }

    $defaults = [ordered]@{
        CLICKUP_ACCOUNTS_JSON = ""
        CLICKUP_ACCOUNTS = ""
        CLICKUP_JWT = ""
        CLICKUP_WORKSPACE_ID = ""
        CLICKUP_REFRESH_JWT = ""
        CLICKUP_TOKEN = ""
        CLICKUP_COOKIE = ""
        CLICKUP_EXTRA_HEADERS = ""
        CLICKUP_BASE_URL = "https://frontdoor-search.clickup-prod.com"
        CLICKUP_GRAPHQL_PATH = "/graphql/gateway"
        CLICKUP_ANONYMOUS_ID = ""
        CLICKUP_CLIENT_VERSION = "4.13.3"
        CLICKUP_LOCALE = "en-US"
        CLICKUP_SURFACE = "client"
        CLICKUP_TZ_OFFSET = "-480"
        CLICKUP_SD_TAB_ID = ""
        API_KEY = ""
        CLICKUP_MOCK = "0"
        HOST = "127.0.0.1"
        PORT = "8787"
    }
    foreach ($entry in $defaults.GetEnumerator()) {
        if (-not $map.Contains($entry.Key)) {
            $map[$entry.Key] = $entry.Value
        }
    }

    $hasMulti = (
        -not [string]::IsNullOrWhiteSpace((Get-MapValue $map "CLICKUP_ACCOUNTS")) -or
        -not [string]::IsNullOrWhiteSpace((Get-MapValue $map "CLICKUP_ACCOUNTS_JSON"))
    )
    if ($Force -or -not $hasMulti) {
        if (-not $Jwt) {
            $Jwt = Read-HiddenText "请输入 ClickUp JWT（输入时隐藏）"
        }
        if ($Jwt -notmatch "^[^.\s]+\.[^.\s]+\.[^.\s]+$") {
            throw "ClickUp JWT 格式不正确，应为三段点分隔字符串"
        }
        if (-not $WorkspaceId) {
            $WorkspaceId = Read-Host "请输入 ClickUp Workspace ID"
        }
        if ($WorkspaceId -notmatch "^[0-9]+$") {
            throw "Workspace ID 应为纯数字"
        }
        Assert-SafeValue "CLICKUP_JWT" $Jwt
        Assert-SafeValue "CLICKUP_WORKSPACE_ID" $WorkspaceId
        $map["CLICKUP_JWT"] = $Jwt
        $map["CLICKUP_WORKSPACE_ID"] = $WorkspaceId
        if ($Force) {
            $map["CLICKUP_ACCOUNTS"] = ""
            $map["CLICKUP_ACCOUNTS_JSON"] = ""
        }
    }

    if (-not $ApiKey) {
        $existingApiKey = Get-MapValue $map "API_KEY"
        if (-not $Force -and $existingApiKey) {
            $ApiKey = $existingApiKey
        }
        else {
            $ApiKey = Read-Host "请输入本服务 API_KEY（留空自动生成）"
            if (-not $ApiKey) {
                $ApiKey = New-ApiKey
                Write-Host "已生成随机 API_KEY；配置完成后可在 .env 中查看。"
            }
        }
    }
    Assert-SafeValue "API_KEY" $ApiKey
    if ([string]::IsNullOrWhiteSpace($ApiKey)) {
        throw "API_KEY 不能为空"
    }
    if ($ApiKey -match "[\s#]") {
        throw "API_KEY 不能包含空白字符或 #"
    }
    $map["API_KEY"] = $ApiKey

    Write-EnvAtomically $map $EnvPath
    if (-not (Test-Configured (Read-EnvMap $EnvPath))) {
        throw "首次配置校验失败"
    }
    Write-Host "首次配置完成：$EnvPath"
}

$failure = $null
try {
    Invoke-Setup
}
catch {
    $failure = $_
    Write-Host "配置失败：$($_.Exception.Message)" -ForegroundColor Red
}
finally {
    Write-Host "DONE"
}
if ($failure) {
    throw $failure
}
