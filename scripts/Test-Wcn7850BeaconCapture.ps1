#Requires -RunAsAdministrator
param([switch]$PingControl, [switch]$ScanControl)
if ($PingControl -and $ScanControl) { throw "请一次选择一种实验。" }
$ErrorActionPreference = 'Stop'
$nic = Get-NetAdapter -Name WLAN
if ($nic.Status -ne 'Up' -or $nic.InterfaceDescription -notlike '*FastConnect*7800*') {
    throw '请先用 Surface 的 FastConnect 7800 连接 Wi-Fi。'
}

# Match the associated AP without printing its hardware address.
$interfaces = (netsh wlan show interfaces) -join "`n"
$section = ($interfaces -split '(?m)(?=^\s*Name\s*:)') | Where-Object { $_ -match [regex]::Escape($nic.InterfaceGuid.ToString().Trim('{}')) }
$ap = [regex]::Match([string]$section, '(?im)^\s*(?:AP )?BSSID\s*:\s*([0-9a-f:]{17})\s*$').Groups[1].Value
$session = 'SurfaceBeacon-' + [guid]::NewGuid().ToString('N')
$etl = Join-Path $env:TEMP "$session.etl"
$filters = @('Wifi.Type=Management')
if ($PingControl) {
    $gateway = (Get-NetIPConfiguration -InterfaceIndex $nic.ifIndex).IPv4DefaultGateway.NextHop | Select-Object -First 1
    $source = Get-NetIPAddress -InterfaceIndex $nic.ifIndex -AddressFamily IPv4 |
        Where-Object { $_.AddressState -eq 'Preferred' -and -not $_.SkipAsSource } |
        Select-Object -First 1 -ExpandProperty IPAddress
    if (-not $gateway -or -not $source) { throw '未找到 WLAN 的 IPv4 地址或网关。' }
    $filters = @("IPv4.Address=$gateway", 'Protocol=1')
}
$startLog = netsh trace start "sessionname=$session" capture=yes capturetype=physical "CaptureInterface=$($nic.InterfaceGuid)" @filters report=disabled persistent=no perfMerge=no maxSize=16 "traceFile=$etl"
if ($LASTEXITCODE) { throw "Windows 未能启动采集。 $startLog" }
try {
    if ($PingControl) {
        Write-Host '正在用 WLAN 向路由器发送 4 次 ping。'
        ping.exe -4 -S $source -n 4 -w 1000 $gateway | Out-Null
        $pingExit = $LASTEXITCODE
        Start-Sleep -Seconds 1
    } elseif ($ScanControl) {
        Write-Host '正在采集管理帧并请求一次 WLAN 扫描，无需操作手机。'
        python -B (Join-Path $PSScriptRoot 'scan_wcn7850_once.py') $nic.InterfaceGuid
        $scanExit = $LASTEXITCODE
        Start-Sleep -Seconds 2
    } else {
        Write-Host '正在观察无线管理帧，约 15 秒，无需操作手机。'
        Start-Sleep -Seconds 15
    }
} finally {
    $stopLog = netsh trace stop "sessionname=$session"
    if ($LASTEXITCODE) { throw "未能停止会话。$stopLog 请运行 netsh trace stop sessionname=$session" }
}

$targetFragments = 0
$ethernetFragments = 0
$wirelessFragments = 0
$allFragments = 0
$native = 0
$management = 0
$probeResponses = 0
$beacons = 0
$matched = 0
$errorsSeen = 0
Get-WinEvent -Path $etl -Oldest | ForEach-Object {
    $evt = $_
    if ($evt.ProviderName -ne 'Microsoft-Windows-NDIS-PacketCapture') { return }
    if ($evt.Id -in 2001,2002,2003) { $errorsSeen++; return }
    if ($evt.Id -ne 1001) { return }
    $allFragments++
    $fields = $evt.Properties
    if ($fields[0].Value -ne $nic.ifIndex -and $fields[1].Value -ne $nic.ifIndex) { return }
    $targetFragments++
    if ($evt.Keywords -band 1) { $ethernetFragments++ }
    if ($evt.Keywords -band 0x10000) { $wirelessFragments++ }
    if ($PingControl) { return }
    # Native802.11, PacketStart and ReceivePath flags from the local ETW manifest.
    $flags = $evt.Keywords
    if (($flags -band 0x10000) -eq 0 -or ($flags -band 0x40000000) -eq 0 -or ($flags -band 8589934592) -eq 0) { return }
    $native++
    $frame = $fields[3].Value
    if ($frame -isnot [byte[]]) { throw '此系统返回了未预期的帧数据格式。' }
    if ($frame.Length -lt 24 -or ($frame[0] -band 0x0f) -ne 0) { return }
    $management++
    if ($frame[0] -eq 0x50 -and $frame.Length -ge 36) { $probeResponses++ }
    if ($frame[0] -ne 0x80 -or $frame.Length -lt 36) { return }
    $beacons++
    $bssid = [BitConverter]::ToString($frame, 16, 6).Replace('-', ':')
    if ($ap -and $bssid -eq $ap) { $matched++ }
}
if ($PingControl) {
    Write-Output "ping退出码=$pingExit 全部包片段=$allFragments WLAN包片段=$targetFragments 以太网格式=$ethernetFragments 原生无线格式=$wirelessFragments 采集错误记录=$errorsSeen"
    return
}
if ($ScanControl) { Write-Output "扫描退出码=$scanExit WLAN包片段=$targetFragments 收到的探测响应=$probeResponses" }
Write-Output "原生无线接收记录=$native 管理帧=$management 信标=$beacons 驱动采集错误记录=$errorsSeen"
if ($ap) { Write-Output "与开始时连接的路由器匹配的信标=$matched" }
else { Write-Output '未读取到当前路由器标识，无法核对信标来源。' }
Write-Output "跟踪文件仅保存在本机：$etl"
