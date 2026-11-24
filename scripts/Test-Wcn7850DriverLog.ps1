#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
$nic = Get-NetAdapter -Name WLAN
if ($nic.Status -ne 'Up' -or $nic.InterfaceDescription -notlike '*FastConnect*7800*') {
    throw '请先用 FastConnect 7800 连接 Wi-Fi。'
}
$provider = Get-WinEvent -ListProvider WlanLogger
if ($provider.Id -ne [guid]'bb6f5b93-635c-47be-816f-e895e77064a8') { throw 'WlanLogger 提供程序不匹配。' }
$name = 'SurfaceWlanLog-' + [guid]::NewGuid().ToString('N')
$file = Join-Path $env:TEMP "$name.etl"
$startLog = netsh trace start "sessionname=$name" capture=no "provider={$($provider.Id)}" level=255 keywords=0xffffffffffffffff report=disabled persistent=no perfMerge=no maxSize=16 "traceFile=$file"
if ($LASTEXITCODE) { throw "启动失败：$startLog" }
try {
    Write-Host '正在记录 5 秒基线，再执行一次 WLAN 扫描。'
    Start-Sleep -Seconds 5
    $scanStart = Get-Date
    python -B (Join-Path $PSScriptRoot 'scan_wcn7850_once.py') $nic.InterfaceGuid
    $scanExit = $LASTEXITCODE
    Start-Sleep -Seconds 3
} finally {
    $stopLog = netsh trace stop "sessionname=$name"
    if ($LASTEXITCODE) { throw "停止失败：$stopLog 。请运行 netsh trace stop sessionname=$name" }
}
$events = @(Get-WinEvent -Path $file -Oldest | Where-Object ProviderId -eq $provider.Id)
$control = @($events | Where-Object TimeCreated -lt $scanStart)
$scan = @($events | Where-Object TimeCreated -ge $scanStart)
function Measure-FrameLog($items) {
    @($items | Where-Object {
        ($_.Properties.Value -join ' ') -match 'WMI_MGMT_RX|mgmt[ _-]*rx|management[ _-]*frame|action[ _-]*frame|probe[ _-]*resp|beacon'
    }).Count
}
Write-Output "扫描退出码=$scanExit 基线事件=$($control.Count) 基线帧关键词=$(Measure-FrameLog $control) 扫描及随后事件=$($scan.Count) 扫描及随后帧关键词=$(Measure-FrameLog $scan)"
Write-Output "本机日志=$file"
