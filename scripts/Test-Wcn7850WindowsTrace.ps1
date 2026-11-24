# Capture Windows-side wireless diagnostics alongside the known Qualcomm indication.
param([string]$AnalyzePath)
$ErrorActionPreference = 'Stop'
if ($AnalyzePath) {
    $file = (Get-Item -LiteralPath $AnalyzePath).FullName
} else {
    $admin = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw '请在管理员 PowerShell 中运行此采集。'
    }
    $nic = Get-NetAdapter -Name WLAN
    if ($nic.Status -ne 'Up' -or $nic.InterfaceDescription -notlike '*FastConnect*7800*') {
        throw '请先用 FastConnect 7800 连接 Wi-Fi。'
    }
    $name = 'SurfaceWifiCx-' + [guid]::NewGuid().ToString('N')
    $file = Join-Path $env:TEMP "$name.etl"
    $startLog = netsh trace start "sessionname=$name" scenario=wireless_dbg capture=no report=disabled persistent=no perfMerge=no maxSize=128 "traceFile=$file" provider=WlanLogger level=255 keywords=0xffffffffffffffff
    if ($LASTEXITCODE) { throw "启动失败：$startLog" }
    try {
        Write-Host '请保持 Wi-Fi 连接，等待 15 秒，无需操作手机。'
        Start-Sleep -Seconds 15
    } finally {
        $stopLog = netsh trace stop "sessionname=$name"
        if ($LASTEXITCODE) { throw "停止失败：$stopLog 。请运行 netsh trace stop sessionname=$name" }
    }
}

$qualcomm = [guid]'bb6f5b93-635c-47be-816f-e895e77064a8'
$controls = 0
$providers = @{}
Get-WinEvent -Path $file -Oldest | ForEach-Object {
    if ($_.ProviderId -eq $qualcomm) {
        if ($_.Properties.Count -and $_.Properties[0].Value -match 'WdiSendIndication:.*StatusCode:ACTION_FRAME_RECEIVED,.*StatusBufferSize\(\d+\)') {
            $controls++
        }
    } elseif ($_.ProviderId -and $_.ProviderId -ne [guid]::Empty) {
        $providers[$_.ProviderId] = $true
    }
}
Write-Output "高通动作帧上报=$controls 其他事件提供程序数=$($providers.Count)"
Write-Output "本机日志=$file"
