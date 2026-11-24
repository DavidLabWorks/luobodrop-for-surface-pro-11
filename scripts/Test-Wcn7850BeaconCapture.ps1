#Requires -RunAsAdministrator
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
netsh trace start "sessionname=$session" capture=yes capturetype=physical "CaptureInterface=$($nic.InterfaceGuid)" Wifi.Type=Management report=disabled persistent=no perfMerge=no maxSize=16 "traceFile=$etl" | Out-Host
if ($LASTEXITCODE) { throw 'Windows 未能启动采集。' }
try {
    Write-Host '正在观察无线管理帧，约 15 秒，无需操作手机。'
    Start-Sleep -Seconds 15
} finally {
    netsh trace stop "sessionname=$session" | Out-Host
    if ($LASTEXITCODE) { throw "未能停止会话。请运行 netsh trace stop sessionname=$session" }
}

$native = 0
$management = 0
$beacons = 0
$matched = 0
$errorsSeen = 0
Get-WinEvent -Path $etl -Oldest | ForEach-Object {
    $evt = $_
    if ($evt.ProviderName -ne 'Microsoft-Windows-NDIS-PacketCapture') { return }
    if ($evt.Id -in 2001,2002,2003) { $errorsSeen++; return }
    if ($evt.Id -ne 1001) { return }
    $fields = $evt.Properties
    if ($fields[0].Value -ne $nic.ifIndex -and $fields[1].Value -ne $nic.ifIndex) { return }
    # Native802.11, PacketStart and ReceivePath flags from the local ETW manifest.
    $flags = $evt.Keywords
    if (($flags -band 0x10000) -eq 0 -or ($flags -band 0x40000000) -eq 0 -or ($flags -band 8589934592) -eq 0) { return }
    $native++
    $frame = $fields[3].Value
    if ($frame -isnot [byte[]]) { throw '此系统返回了未预期的帧数据格式。' }
    if ($frame.Length -lt 24 -or ($frame[0] -band 0x0f) -ne 0) { return }
    $management++
    if ($frame[0] -ne 0x80 -or $frame.Length -lt 36) { return }
    $beacons++
    $bssid = [BitConverter]::ToString($frame, 16, 6).Replace('-', ':')
    if ($ap -and $bssid -eq $ap) { $matched++ }
}
Write-Output "原生无线接收记录=$native 管理帧=$management 信标=$beacons 驱动采集错误记录=$errorsSeen"
if ($ap) { Write-Output "与开始时连接的路由器匹配的信标=$matched" }
else { Write-Output '未读取到当前路由器标识，无法核对信标来源。' }
Write-Output "跟踪文件仅保存在本机：$etl"
