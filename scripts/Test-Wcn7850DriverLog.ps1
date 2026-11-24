#Requires -RunAsAdministrator
param([switch]$AirDropPanel)
$ErrorActionPreference = 'Stop'
$nic = Get-NetAdapter -Name WLAN
if ($nic.Status -ne 'Up' -or $nic.InterfaceDescription -notlike '*FastConnect*7800*') {
    throw '请先用 FastConnect 7800 连接 Wi-Fi。'
}
$provider = Get-WinEvent -ListProvider WlanLogger
if ($provider.Id -ne [guid]'bb6f5b93-635c-47be-816f-e895e77064a8') { throw 'WlanLogger 提供程序不匹配。' }
$name = 'SurfaceWlanLog-' + [guid]::NewGuid().ToString('N')
$file = Join-Path $env:TEMP "$name.etl"
$maxSize = 16
if ($AirDropPanel) {
    $maxSize = 128
    [void](Read-Host '请让苹果设备停在主屏幕，准备好后在 Surface 按回车')
}
$startLog = netsh trace start "sessionname=$name" capture=no "provider={$($provider.Id)}" level=255 keywords=0xffffffffffffffff report=disabled persistent=no perfMerge=no "maxSize=$maxSize" "traceFile=$file"
if ($LASTEXITCODE) { throw "启动失败：$startLog" }
try {
    if ($AirDropPanel) {
        $baselineStart = Get-Date
        Write-Host '第一段：保持主屏幕，等待 15 秒。'
        Start-Sleep -Seconds 15
        $panelStart = Get-Date
        Write-Host '第二段：现在打开照片 → 分享 → 隔空投送，停在选择接收人的页面。'
        Start-Sleep -Seconds 20
        $recoveryStart = Get-Date
        Write-Host '第三段：现在退出隔空投送，回到主屏幕。'
        Start-Sleep -Seconds 15
        $end = Get-Date
    } else {
        Write-Host '正在记录 5 秒基线，再执行一次 WLAN 扫描。'
        Start-Sleep -Seconds 5
        $scanStart = Get-Date
        python -B (Join-Path $PSScriptRoot 'scan_wcn7850_once.py') $nic.InterfaceGuid
        $scanExit = $LASTEXITCODE
        Start-Sleep -Seconds 3
    }
} finally {
    $stopLog = netsh trace stop "sessionname=$name"
    if ($LASTEXITCODE) { throw "停止失败：$stopLog 。请运行 netsh trace stop sessionname=$name" }
}
if ($AirDropPanel) {
    $rows = @(foreach ($phase in '主屏幕','隔空投送','退出后') {
        [pscustomobject]@{阶段=$phase; 日志=0; 管理帧接收事件=0; 动作帧=0; 类别127=0}
    })
    Get-WinEvent -Path $file -Oldest | ForEach-Object {
        if ($_.ProviderId -ne $provider.Id -or $_.TimeCreated -lt $baselineStart -or $_.TimeCreated -ge $end) { return }
        $index = if ($_.TimeCreated -lt $panelStart) { 0 } elseif ($_.TimeCreated -lt $recoveryStart) { 1 } else { 2 }
        $row = $rows[$index]
        $row.日志++
        $message = $_.Properties.Value -join ' '
        if ($message -match 'WMI_MGMT_RX_EVENTID') { $row.管理帧接收事件++ }
        if ($message -match 'Action mgt frame \(cat (0x[0-9a-f]+|[0-9]+),') {
            $row.动作帧++
            if ([int]$Matches[1] -eq 127) { $row.类别127++ }
        }
    }
    $rows | Format-Table -AutoSize
    Write-Output "本机日志=$file"
    return
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
