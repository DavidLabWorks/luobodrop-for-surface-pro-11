param(
    [ValidatePattern('^(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$')]
    [string]$SharedAdvertPeer,
    [switch]$SharedEcho,
    [switch]$WakePeer,
    [switch]$AirDropPreflight,
    [string]$SendFile,
    [switch]$SendFastProfile,
    [switch]$SendHe2Stream,
    [switch]$SendAmsdu3,
    [switch]$PrepareUploadEarly,
    [switch]$BatchRadioSend,
    [ValidateSet('16', '32', '64')][int]$RadioSendBatchSize = 16,
    [switch]$CompareSendBatches,
    [switch]$LargeWintunRing,
    [ValidateSet('256', '4096')][int]$WintunRingKiB,
    [ValidateRange(1, 10)][int]$SendRepeat = 1,
    [ValidateRange(0, 60)][double]$SendRepeatGap = 0,
    [ValidateSet('none', 'ba', 'peer')][string]$SendRepeatReset = 'none',
    [string]$ReceiveTo,
    [switch]$CoalesceReceive,
    [switch]$WinAwdlProfile,
    [switch]$Mtu1500,
    [ValidateSet('4', '8', '12', '16')][int]$ReceiveBatchSize = 4,
    [switch]$BatchRadioReceive,
    [switch]$PipelineReceive,
    [ValidateSet('8', '16', '32', '64')][int]$RadioBatchSize = 8,
    [switch]$AdvertiseVht,
    [switch]$AdvertiseHe,
    [switch]$UseRxReorder,
    [switch]$UseRxAmsdu,
    [switch]$PeerWcidAck,
    [switch]$ReceiveQueue256,
    [switch]$TraceUploadFlow,
    [switch]$DecodeDuringReceive,
    [switch]$MemoryArchive,
    [switch]$FastUploadTail,
    [ValidateSet('1', '2', '4', '8')][int]$ReceiveBufferMiB = 0,
    [switch]$TraceWlanCoexistence,
    [switch]$TraceStationIo,
    [switch]$TraceStationPackets,
    [ValidateRange(1, 2147483647)][int]$WiredControlInterfaceIndex,
    [switch]$CaptureWlanProbes,
    [switch]$ProbeLocalGateway,
    [switch]$TraceStartup,
    [switch]$SplitStartup,
    [switch]$QuicProbe,
    [switch]$WatchWifiAfter,
    [switch]$DesktopEvents
)

$ErrorActionPreference = 'Stop'
if ($QuicProbe) { throw 'The experimental QUIC probe is excluded from this snapshot.' }
if ($args.Count -gt 0) {
    throw "Unrecognized arguments: $($args -join ' ')"
}
if ($SendFastProfile -and (!$SendFile -or $ReceiveTo)) {
    throw '-SendFastProfile requires -SendFile.'
}
if ($SendHe2Stream -and (!$SendFastProfile -or !$SharedAdvertPeer)) {
    throw '-SendHe2Stream requires -SendFastProfile and -SharedAdvertPeer.'
}
if ($SendAmsdu3 -and (!$SendFastProfile -or !$SharedAdvertPeer)) {
    throw '-SendAmsdu3 requires -SendFastProfile and -SharedAdvertPeer.'
}
if ($PrepareUploadEarly -and (!$SendFastProfile -or !$SharedAdvertPeer)) {
    throw '-PrepareUploadEarly requires -SendFastProfile and -SharedAdvertPeer.'
}
if ($BatchRadioSend -and (!$SendFastProfile -or !$SharedAdvertPeer)) {
    throw '-BatchRadioSend requires -SendFastProfile and -SharedAdvertPeer.'
}
if ($PSBoundParameters.ContainsKey('RadioSendBatchSize') -and !$BatchRadioSend) {
    throw '-RadioSendBatchSize requires -BatchRadioSend.'
}
if ($CompareSendBatches -and (!$SendFile -or !$BatchRadioSend -or !$TraceUploadFlow -or
        $RadioSendBatchSize -ne 16 -or $SendRepeat -ne 1)) {
    throw '-CompareSendBatches requires one traced upload with the default send batch size.'
}
if ($LargeWintunRing -or $PSBoundParameters.ContainsKey('WintunRingKiB')) {
    if (!$SendFile -or !$SharedAdvertPeer) {
        throw 'A Wintun ring override requires -SendFile and -SharedAdvertPeer.'
    }
    if ($LargeWintunRing -and $PSBoundParameters.ContainsKey('WintunRingKiB')) {
        throw 'Choose either -LargeWintunRing or -WintunRingKiB.'
    }
}
if ($PSBoundParameters.ContainsKey('SendRepeat') -and !$SendFile) {
    throw '-SendRepeat requires -SendFile.'
}
if ($PSBoundParameters.ContainsKey('SendRepeatGap') -and $SendRepeat -le 1) {
    throw '-SendRepeatGap requires -SendRepeat greater than 1.'
}
if ($SendRepeatReset -ne 'none' -and $SendRepeat -le 1) {
    throw '-SendRepeatReset requires -SendRepeat greater than 1.'
}
if ($SendRepeatReset -ne 'none' -and !$SendFile) {
    throw '-SendRepeatReset requires -SendFile.'
}
if ($Mtu1500 -and !$WinAwdlProfile) {
    throw '-Mtu1500 requires -WinAwdlProfile.'
}
if ($WinAwdlProfile) {
    if (!$ReceiveTo -or $CoalesceReceive) {
        throw '-WinAwdlProfile requires -ReceiveTo and uses the WinAWDL TCP merger.'
    }
    $AdvertiseVht = $true
    $UseRxReorder = $true
    $BatchRadioReceive = $true
    $ReceiveQueue256 = $true
    if (!$PSBoundParameters.ContainsKey('RadioBatchSize')) {
        $RadioBatchSize = 32
    }
}
$transfer = [bool]($SendFile -or $ReceiveTo)
if ($TraceStationPackets) {
    $TraceWlanCoexistence = $true
    $TraceStationIo = $true
    $CaptureWlanProbes = $true
    $ProbeLocalGateway = $true
}
if ($TraceWlanCoexistence -and !$transfer) {
    throw '-TraceWlanCoexistence requires -SendFile or -ReceiveTo.'
}
if ($TraceStationIo -and !$TraceWlanCoexistence) {
    throw '-TraceStationIo requires -TraceWlanCoexistence.'
}
if ($TraceWlanCoexistence) {
    $WatchWifiAfter = $true
}
if ($WiredControlInterfaceIndex -and !$TraceWlanCoexistence) {
    throw '-WiredControlInterfaceIndex requires -TraceWlanCoexistence.'
}
if ($CaptureWlanProbes -and !$TraceWlanCoexistence) {
    throw '-CaptureWlanProbes requires -TraceWlanCoexistence.'
}
if ($ProbeLocalGateway -and !$TraceWlanCoexistence) {
    throw '-ProbeLocalGateway requires -TraceWlanCoexistence.'
}
if (($TraceStartup -or $SplitStartup) -and (!$TraceWlanCoexistence -or !$ReceiveTo)) {
    throw 'Startup diagnostics require -TraceWlanCoexistence and -ReceiveTo.'
}
if ($transfer -and (!$SharedAdvertPeer -or ($SendFile -and $ReceiveTo) -or $SharedEcho -or $AirDropPreflight)) {
    throw 'Choose one of -SendFile or -ReceiveTo with -SharedAdvertPeer for a shared transfer.'
}
if ($WatchWifiAfter -and !$transfer) {
    throw 'Use -WatchWifiAfter with -SendFile or -ReceiveTo.'
}
if ($CoalesceReceive -and !$ReceiveTo) {
    throw '-CoalesceReceive requires -ReceiveTo.'
}
if ($PSBoundParameters.ContainsKey('ReceiveBatchSize') -and !$CoalesceReceive) {
    throw '-ReceiveBatchSize requires -CoalesceReceive.'
}
if ($BatchRadioReceive -and !$ReceiveTo) {
    throw '-BatchRadioReceive requires -ReceiveTo.'
}
if ($PipelineReceive -and !$BatchRadioReceive) {
    throw '-PipelineReceive requires -BatchRadioReceive.'
}
if ($PSBoundParameters.ContainsKey('RadioBatchSize') -and !$BatchRadioReceive) {
    throw '-RadioBatchSize requires -BatchRadioReceive.'
}
if ($AdvertiseVht -and !$ReceiveTo) {
    throw '-AdvertiseVht requires -ReceiveTo.'
}
if ($UseRxReorder -and !$ReceiveTo) {
    throw '-UseRxReorder requires -ReceiveTo.'
}
if ($AdvertiseHe -and (!$ReceiveTo -or !$UseRxReorder)) {
    throw '-AdvertiseHe requires -ReceiveTo and -UseRxReorder.'
}
if ($UseRxAmsdu -and (!$ReceiveTo -or !$UseRxReorder)) {
    throw '-UseRxAmsdu requires -ReceiveTo and -UseRxReorder.'
}
if ($PeerWcidAck -and (!$ReceiveTo -or !$UseRxReorder)) {
    throw '-PeerWcidAck requires -ReceiveTo and -UseRxReorder.'
}
if ($ReceiveQueue256 -and !$ReceiveTo) {
    throw '-ReceiveQueue256 requires -ReceiveTo.'
}
if ($TraceUploadFlow -and !$ReceiveTo -and !$SendFile) {
    throw '-TraceUploadFlow requires -ReceiveTo or -SendFile.'
}
if ($DecodeDuringReceive -and !$ReceiveTo) {
    throw '-DecodeDuringReceive requires -ReceiveTo.'
}
if ($MemoryArchive -and !$DecodeDuringReceive) {
    throw '-MemoryArchive requires -DecodeDuringReceive.'
}
if ($FastUploadTail -and !$DecodeDuringReceive) {
    throw '-FastUploadTail requires -DecodeDuringReceive.'
}
if ($ReceiveBufferMiB -and !$ReceiveTo) {
    throw '-ReceiveBufferMiB requires -ReceiveTo.'
}
if ($QuicProbe -and !$ReceiveTo) {
    throw '-QuicProbe requires -ReceiveTo.'
}
if ($AirDropPreflight -and (!$SharedAdvertPeer -or $SharedEcho -or $WakePeer)) {
    throw 'Use -AirDropPreflight with -SharedAdvertPeer and no radio-probe switches.'
}
if ($SharedEcho -and !$PSBoundParameters.ContainsKey('WakePeer')) {
    $WakePeer = $true
}
$root = Split-Path -Parent $PSScriptRoot
$query = Join-Path $root 'build\x64\mt7922-query.exe'
$adapter = @(Get-NetAdapter -Physical | Where-Object {
    $_.PnPDeviceID -like 'PCI\VEN_14C3&DEV_0616&SUBSYS_E0DF105B&REV_00\*' -and
        $_.Status -ne 'Not Present'
})
if ($adapter.Count -ne 1) {
    throw 'Expected one physical MT7922 adapter (14c3:0616).'
}

$wifi = & $query --shared-wifi-status ([guid]$adapter[0].InterfaceGuid).ToString('B')
$wifi | Write-Output
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$routes = @(Get-NetRoute -InterfaceIndex $adapter[0].ifIndex -ErrorAction Stop |
    Where-Object DestinationPrefix -in @('0.0.0.0/0','::/0'))
if ($routes.Count -eq 0) {
    Write-Output 'MT7922 has no IPv4 or IPv6 default route of its own.'
    exit 4
}
if (!$SharedAdvertPeer) {
    if ($SharedEcho -or $WakePeer) { throw 'Use -SharedEcho or -WakePeer with -SharedAdvertPeer.' }
    Write-Output 'MT7922 has a default route. This check reports Wi-Fi prerequisites only.'
    return
}
Write-Output 'MT7922 has a default route.'
if ($AirDropPreflight) {
    $python = (Get-Command python.exe -CommandType Application -TotalCount 1).Source
    $client = Join-Path $PSScriptRoot 'discover-awdl.py'
    $firmware = Join-Path $root 'build\wificx-x64\package\mt7922-wificx-fw.bin'
    $sample = Join-Path $root 'build\x64\MT7922-AirDrop-Test.png'
    $destination = Join-Path $root 'received'
    $common = @('-B', '-X', 'utf8', $client, $firmware, $SharedAdvertPeer)
    & $python @common '8770' --send-file $sample --shared-wifi --send-fast-profile --send-he-2-stream `
        --prepare-upload-early --batch-radio-send --radio-send-batch-size 16 --check
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $python @common '8771' --receive-into $destination --shared-wifi --check
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Output 'AirDrop sender and receiver prerequisites passed. No file transfer was started.'
    return
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (!$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run the shared AWDL transmit test from an administrator terminal.'
}
$driver = Get-PnpDeviceProperty -InstanceId $adapter[0].PnPDeviceID -KeyName DEVPKEY_Device_DriverVersion
$minimumDriver = if ($SendAmsdu3) {
    '0.70.0.83'
} elseif ($SendRepeatReset -ne 'none') {
    '0.70.0.80'
} elseif ($PrepareUploadEarly) {
    '0.70.0.74'
} elseif ($TraceStationPackets) {
    '0.70.0.49'
} elseif ($TraceStationIo) {
    '0.70.0.48'
} elseif ($TraceWlanCoexistence) {
    '0.70.0.45'
} elseif ($PeerWcidAck) {
    '0.70.0.44'
} elseif ($Mtu1500) {
    '0.70.0.39'
} elseif ($WinAwdlProfile) {
    '0.70.0.38'
} elseif ($ReceiveQueue256) {
    '0.70.0.37'
} elseif ($AdvertiseHe) {
    '0.70.0.36'
} elseif ($UseRxAmsdu) {
    '0.70.0.35'
} elseif ($RadioBatchSize -eq 64) {
    '0.70.0.34'
} elseif ($RadioBatchSize -eq 32) {
    '0.70.0.32'
} elseif ($transfer) {
    '0.70.0.18'
} elseif ($SharedEcho) {
    '0.70.0.17'
} else {
    '0.70.0.16'
}
if ([version]$driver.Data -lt [version]$minimumDriver) {
    throw "This shared AWDL test requires WiFiCx $minimumDriver or later."
}
if (!($wifi -match '^MT7922 connected: primary-channel=149 frequency=5745 MHz$')) {
    throw 'The shared AWDL transmit test requires 5 GHz primary channel 149.'
}

$config = Get-NetIPConfiguration -InterfaceIndex $adapter[0].ifIndex
$gateway = $config.IPv4DefaultGateway.NextHop | Select-Object -First 1
$source = $config.IPv4Address.IPAddress | Where-Object { $_ -notlike '169.254.*' } | Select-Object -First 1
if (!$source -or !$gateway) {
    throw 'The MT7922 needs an IPv4 address and gateway for this connectivity test.'
}
if ($WiredControlInterfaceIndex) {
    $wiredAdapter = Get-NetAdapter -InterfaceIndex $WiredControlInterfaceIndex
    if ($wiredAdapter.Status -ne 'Up' -or $wiredAdapter.PhysicalMediaType -ne '802.3') {
        throw 'The wired control requires a connected Ethernet adapter.'
    }
    $wiredConfig = Get-NetIPConfiguration -InterfaceIndex $WiredControlInterfaceIndex
    $wiredSource = $wiredConfig.IPv4Address.IPAddress | Where-Object { $_ -notlike '169.254.*' } | Select-Object -First 1
    $wiredGateway = $wiredConfig.IPv4DefaultGateway.NextHop | Select-Object -First 1
    if (!$wiredSource -or !$wiredGateway -or $wiredSource -eq $source) {
        throw 'The wired control needs its own IPv4 address and gateway.'
    }
}
function Test-WifiGateway {
    $ping = & ping.exe -4 -n 1 -w 800 -S $source $gateway
    return [bool]($ping -match '(?i)\bTTL=')
}
if (!$transfer -and !(Test-WifiGateway)) {
    throw 'The Wi-Fi gateway did not answer the baseline check. No AWDL frame was requested.'
}

function Invoke-WifiAirDropTransfer {
    $python = (Get-Command python.exe -CommandType Application -TotalCount 1).Source
    $client = Join-Path $PSScriptRoot 'discover-awdl.py'
    $firmware = Join-Path $root 'build\wificx-x64\package\mt7922-wificx-fw.bin'
    $port = if ($ReceiveTo) { '8771' } else { '8770' }
    $arguments = @('-B', '-X', 'utf8', $client, $firmware, $SharedAdvertPeer, $port, '--shared-wifi')
    if ($DesktopEvents) {
        $arguments = @('-u') + $arguments + @('--desktop-events')
    }
    if ($SendFile) {
        $arguments += @('--send-file', (Resolve-Path -LiteralPath $SendFile).ProviderPath)
        if ($SendFastProfile) {
            Write-Output 'AirDrop fast sender profile: IPv6 MTU 1500, send queue 256, up to 64 in-flight frames, A-MSDU if accepted.'
            $arguments += '--send-fast-profile'
        }
        if ($SendHe2Stream) {
            Write-Output 'AirDrop sender: requested HE MCS 0-11 on two spatial streams with automatic rate selection.'
            $arguments += '--send-he-2-stream'
        }
        if ($SendAmsdu3) {
            Write-Output 'AirDrop sender: up to three A-MSDU subframes when supported by the peer.'
            $arguments += '--send-amsdu-3'
        }
        if ($PrepareUploadEarly) {
            Write-Output 'AirDrop sender: preparing the HE peer early and requesting Block Ack after /Discover succeeds.'
            $arguments += '--prepare-upload-early'
        }
        if ($BatchRadioSend) {
            if ($CompareSendBatches) {
                Write-Output 'AirDrop sender: comparing 16, 32 and 16 packets per request during one upload.'
                $arguments += '--compare-send-batches'
            } else {
                Write-Output "AirDrop sender: batching available upload packets (up to $RadioSendBatchSize per driver request)."
            }
            $arguments += @('--batch-radio-send', '--radio-send-batch-size', [string]$RadioSendBatchSize)
        }
        if ($LargeWintunRing) {
            $arguments += '--large-wintun-ring'
        } elseif ($WintunRingKiB) {
            $arguments += @('--wintun-ring-kib', [string]$WintunRingKiB)
        }
        if ($TraceUploadFlow) {
            Write-Output 'AirDrop upload body and transmit timing enabled.'
            $arguments += '--trace-upload-flow'
        }
        if ($SendRepeat -gt 1) {
            Write-Output "AirDrop repeat send: $SendRepeat transfers on one AWDL link (gap $SendRepeatGap s, reset $SendRepeatReset)."
            $arguments += @('--send-repeat', [string]$SendRepeat)
            if ($SendRepeatGap -gt 0) {
                $arguments += @('--send-repeat-gap', [string]$SendRepeatGap)
            }
            if ($SendRepeatReset -ne 'none') {
                $arguments += @('--send-repeat-reset', $SendRepeatReset)
            }
        }
    } else {
        $destination = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ReceiveTo)
        $arguments += @('--receive-into', $destination)
        if ($WinAwdlProfile) {
            $profileMtu = if ($Mtu1500) { 1500 } else { 1450 }
            Write-Output "WinAWDL receive profile: IPv6 MTU $profileMtu, native merge up to 64 TCP segments, 400 us idle flush."
            $arguments += @("--shared-mtu-$profileMtu", '--winawdl-merge')
        }
        if ($CoalesceReceive) {
            Write-Output "AirDrop receive TCP coalescing enabled during upload (up to $ReceiveBatchSize segments)."
            $arguments += @('--coalesce-receive', '--receive-batch-size', [string]$ReceiveBatchSize)
        }
        if ($BatchRadioReceive) {
            Write-Output "AirDrop upload radio receive batching enabled (up to $RadioBatchSize queued packets)."
            $arguments += @('--batch-radio-receive', '--radio-batch-size', [string]$RadioBatchSize)
        }
        if ($PipelineReceive) {
            Write-Output 'AirDrop receive pipeline enabled during upload (prefetch after full batches, queued TX first).'
            $arguments += '--pipeline-receive'
        }
        if ($AdvertiseHe) {
            Write-Output 'AirDrop HE receive advertisement requested on the shared 80 MHz channel.'
            $arguments += '--advertise-he'
        }
        if ($ReceiveQueue256) {
            Write-Output 'AirDrop driver receive queue requested at 256 packets (default is 128).'
            $arguments += '--rx-queue-256'
        }
        if ($AdvertiseVht) {
            Write-Output 'AirDrop VHT receive advertisement requested. The driver requires an 80 MHz radio context.'
            $arguments += '--advertise-vht'
        }
        if ($UseRxReorder) {
            Write-Output 'AirDrop Block Ack receive and wireless frame reordering enabled.'
            $arguments += '--use-rx-reorder'
        }
        if ($UseRxAmsdu) {
            Write-Output 'AirDrop A-MSDU receive negotiation enabled for shared Wi-Fi.'
            $arguments += '--use-rx-amsdu'
        }
        if ($PeerWcidAck) {
            Write-Output 'AirDrop upload ACKs will use the registered peer WCID when ready.'
            $arguments += '--peer-wcid-ack'
        }
        if ($TraceUploadFlow) {
            Write-Output 'AirDrop upload TCP tracing enabled. An ACK timing CSV will be saved after the upload.'
            $arguments += '--trace-upload-flow'
        }
        if ($DecodeDuringReceive) {
            Write-Output 'AirDrop will decode the archive while receiving.'
            $arguments += '--decode-during-receive'
        }
        if ($MemoryArchive) {
            $arguments += '--memory-archive'
        }
        if ($FastUploadTail) {
            Write-Output 'AirDrop HTTP end marker wait: 100 ms after a complete archive.'
            $arguments += '--fast-upload-tail'
        }
        if ($ReceiveBufferMiB) {
            $arguments += @('--receive-buffer-mib', [string]$ReceiveBufferMiB)
        }
        if ($QuicProbe) {
            $arguments += '--quic-probe'
        }
    }

    $sampleConnectivity = {
        param(
            [string]$LocalAddress,
            [string]$RouterAddress,
            [Diagnostics.Stopwatch]$ExitClock,
            [bool]$TraceStation,
            [string]$Phase,
            [bool]$ProbeGatewayTcp,
            [string]$HttpsAddress
        )
        $startedAtMs = if ($ExitClock) { $ExitClock.ElapsedMilliseconds }
        $startedAt = Get-Date -Format 'o'
        [byte[]]$station = if ($TraceStation) {
            $key = 'HKLM:\SYSTEM\CurrentControlSet\Services\mt7922wificx'
            (Get-ItemProperty -LiteralPath $key -Name ConnectDebug -ErrorAction SilentlyContinue).ConnectDebug
        }
        $ping = & ping.exe -4 -n 1 -w 800 -S $LocalAddress $RouterAddress
        $httpsStartedAt = Get-Date -Format 'o'
        $head = @('-4', '--interface', $LocalAddress, '--silent', '--head', '-o', 'NUL')
        $httpsOptions = if ($HttpsAddress) { @('--noproxy', '*', '--resolve', "www.baidu.com:443:$HttpsAddress") }
        $httpsFormat = '%{http_code}|%{time_namelookup}|%{time_connect}|%{time_appconnect}|%{time_starttransfer}|%{time_total}|%{local_ip}|%{remote_ip}|%{errormsg}'
        if ($ProbeGatewayTcp) {
            $gatewayFormat = 'LAN|%{exitcode}|%{http_code}|%{time_connect}|%{time_total}|%{local_ip}|%{remote_ip}|%{errormsg}\n'
            $results = & curl.exe --parallel --parallel-immediate @head @httpsOptions --max-time 4 `
                -w ('WAN|%{exitcode}|' + $httpsFormat + '\n') 'https://www.baidu.com/' --next `
                @head --noproxy '*' --max-time 2 -w $gatewayFormat "http://$RouterAddress/"
            $curlExit = $LASTEXITCODE
            $wan = [string]($results | Where-Object { $_ -like 'WAN|*' } | Select-Object -First 1)
            $lan = [string]($results | Where-Object { $_ -like 'LAN|*' } | Select-Object -First 1)
            $wanFields = $wan -split '\|', 3
            $httpsResult = $wanFields[2]
            if ($wanFields.Count -eq 3) { $curlExit = [int]$wanFields[1] }
            $null, $gatewayExit, $gatewayHttp, $gatewayTcp, $gatewayTotal, $gatewayLocalIp, $gatewayRemoteIp, $gatewayError =
                ($lan -split '\|', 8)
        } else {
            $httpsResult = & curl.exe @head @httpsOptions --max-time 4 -w $httpsFormat 'https://www.baidu.com/'
            $curlExit = $LASTEXITCODE
        }
        $httpStatus, $dnsSeconds, $tcpSeconds, $tlsSeconds, $firstByteSeconds, $totalSeconds, $localIp, $remoteIp, $curlError =
            ([string]$httpsResult -split '\|', 9)
        $sample = [ordered]@{
            Time = $startedAt
            Phase = $Phase
            Gateway = [bool]($ping -match '(?i)\bTTL=')
            Https = [bool]($curlExit -eq 0 -and $httpStatus -match '^[23][0-9]{2}$')
            ExitOffsetMs = $startedAtMs
            HttpsExit = $curlExit
            HttpStatus = $httpStatus
        }
        if ($TraceStation -or $Phase -eq 'Wired') {
            $sample.HttpsStartedAt = $httpsStartedAt
            $sample.HttpsDnsSeconds = $dnsSeconds
            $sample.HttpsTcpSeconds = $tcpSeconds
            $sample.HttpsTlsSeconds = $tlsSeconds
            $sample.HttpsFirstByteSeconds = $firstByteSeconds
            $sample.HttpsTotalSeconds = $totalSeconds
            $sample.HttpsLocalIp = $localIp
            $sample.HttpsRemoteIp = $remoteIp
            $sample.HttpsError = $curlError
        }
        if ($ProbeGatewayTcp) {
            $sample.GatewayTcp = [bool]($gatewayLocalIp -eq $LocalAddress -and $gatewayRemoteIp -eq $RouterAddress -and
                (($gatewayTcp -as [double]) -gt 0 -or $gatewayHttp -match '^[1-5][0-9]{2}$'))
            $sample.GatewayTcpStartedAt = $httpsStartedAt
            $sample.GatewayTcpSeconds = $gatewayTcp
            $sample.GatewayHttpStatus = $gatewayHttp
            $sample.GatewayHttpTotalSeconds = $gatewayTotal
            $sample.GatewayHttpExit = $gatewayExit
            $sample.GatewayHttpError = $gatewayError
        }
        if ($TraceStation) {
            $fields = [ordered]@{
                ConnectSequence = 0
                ApRxData = 428
                PortSecured = 504
                StationTxSubmitted = 520
                StationTxBusy = 524
                StationTxLastStatus = 532
                StationTxCompleted = 536
                StationTxCompletionErrors = 540
                StationTxReleaseStatus = 544
                StationSample = 664
                StationFlags = 668
                BaResetAttempts = 672
                BaCleanupStatus = 676
                AckInFlight = 680
                StationRxStaged = 684
            }
            foreach ($field in $fields.GetEnumerator()) {
                $sample[$field.Key] = if ($station.Length -ge 688) {
                    [BitConverter]::ToUInt32($station, $field.Value)
                }
            }
        }
        [pscustomobject]$sample
    }
    $baselineSamples = @()
    for ($attempt = 1; $attempt -le 3; ++$attempt) {
        $before = & $sampleConnectivity $source $gateway $null $TraceWlanCoexistence.IsPresent 'Baseline' $ProbeLocalGateway.IsPresent $probeHttpsAddress
        $baselineSamples += $before
        if ($before.Gateway -and $before.Https) { break }
        Write-Output "WLAN baseline attempt $attempt/3 failed: gateway=$($before.Gateway) HTTPS=$($before.Https) curl-exit=$($before.HttpsExit) HTTP=$($before.HttpStatus)"
        if ($attempt -lt 3) { Start-Sleep -Milliseconds 500 }
    }
    if ($TraceWlanCoexistence) {
        $coexistenceLog = Join-Path $root "build\wificx-coexistence-$(Get-Date -Format 'yyyyMMdd-HHmmss-fff').csv"
        $baselineSamples | Export-Csv -LiteralPath $coexistenceLog -NoTypeInformation -Encoding UTF8
        Write-Output "WLAN coexistence log: $coexistenceLog"
    }
    Write-Output "WLAN before AirDrop: gateway=$($before.Gateway) HTTPS=$($before.Https)"
    if (!$before.Gateway -or !$before.Https) {
        throw 'The WLAN connectivity baseline failed after three attempts. No AirDrop transfer was started.'
    }

    if ($TraceStartup -or $SplitStartup) {
        $startupLog = [IO.Path]::ChangeExtension($coexistenceLog, 'startup.csv')
        $arguments += @('--startup-log', $startupLog)
        if (!$SplitStartup) { $arguments += '--no-startup-hold' }
        Write-Output "AirDrop startup timing log: $startupLog"
    }
    $transferExit = 1
    $after = $null
    $wiredMonitor = $null
    $stationIoMonitor = $null
    try {
        if ($TraceStationIo) {
            $traceScript = Join-Path $PSScriptRoot 'station-io-trace.ps1'
            . $traceScript
            $stationIoLog = [IO.Path]::ChangeExtension($coexistenceLog, 'station-io.csv')
            Start-StationIoTrace -UseActiveTrace | Select-Object @{n='TraceEpoch';e={1}}, * |
                Export-Csv -LiteralPath $stationIoLog -NoTypeInformation -Encoding UTF8
            $stationIoMonitor = Start-Job -ArgumentList $traceScript, $stationIoLog -ScriptBlock {
                param($TraceScript, $LogPath)
                $ErrorActionPreference = 'Stop'
                . $TraceScript
                $epoch = 1
                while ($true) {
                    $sample = Get-StationIoTraceSample
                    if ($sample) {
                        $sample | Select-Object @{n='TraceEpoch';e={$epoch}}, * |
                            Export-Csv -LiteralPath $LogPath -NoTypeInformation -Encoding UTF8 -Append
                        if (!$sample.Active) {
                            $null = Start-StationIoTrace -UseActiveTrace
                            ++$epoch
                        }
                    }
                    Start-Sleep -Milliseconds 480
                }
            }
            Write-Output "WLAN station I/O trace: $stationIoLog"
        }
        if ($WiredControlInterfaceIndex) {
            $wiredLog = [IO.Path]::ChangeExtension($coexistenceLog, 'wired.csv')
            $wiredMonitor = Start-Job -ArgumentList $sampleConnectivity.ToString(), $wiredSource, $wiredGateway, $wiredLog, $ProbeLocalGateway.IsPresent, $probeHttpsAddress -ScriptBlock {
                param($Definition, $LocalAddress, $RouterAddress, $LogPath, $ProbeGatewayTcp, $HttpsAddress)
                $sample = [scriptblock]::Create($Definition)
                while ($true) {
                    & $sample $LocalAddress $RouterAddress $null $false 'Wired' $ProbeGatewayTcp $HttpsAddress |
                        Export-Csv -LiteralPath $LogPath -NoTypeInformation -Encoding UTF8 -Append
                    Start-Sleep -Milliseconds 750
                }
            }
            Write-Output "Wired control during and after AirDrop: $wiredSource; log: $wiredLog"
        }
        $monitor = Start-Job -ArgumentList $sampleConnectivity.ToString(), $source, $gateway, $TraceWlanCoexistence.IsPresent, $ProbeLocalGateway.IsPresent, $probeHttpsAddress -ScriptBlock {
            param($Definition, $LocalAddress, $RouterAddress, $TraceStation, $ProbeGatewayTcp, $HttpsAddress)
            $sample = [scriptblock]::Create($Definition)
            while ($true) {
                & $sample $LocalAddress $RouterAddress $null $TraceStation 'AWDL' $ProbeGatewayTcp $HttpsAddress
                Start-Sleep -Seconds 1
            }
        }
        try {
            & $python @arguments
            $transferExit = $LASTEXITCODE
            $exitedAt = Get-Date
            $exitClock = [Diagnostics.Stopwatch]::StartNew()
        } finally {
            Stop-Job -Job $monitor
            $observed = @(Receive-Job -Job $monitor)
            Remove-Job -Job $monitor
        }

        if ($TraceWlanCoexistence) {
            $columns = $before.PSObject.Properties.Name
            $observed | Select-Object -Property $columns |
                Export-Csv -LiteralPath $coexistenceLog -NoTypeInformation -Encoding UTF8 -Append
        }
        $watchSeconds = if ($TraceWlanCoexistence) { 72 } else { 12 }
        $gatewayReplies = @($observed | Where-Object Gateway).Count
        $internetReplies = @($observed | Where-Object Https).Count
        Write-Output "WLAN during AirDrop: gateway=$gatewayReplies/$($observed.Count) HTTPS=$internetReplies/$($observed.Count)"
        if ($WatchWifiAfter) {
            Write-Output "Watching Wi-Fi for $watchSeconds seconds after AirDrop."
        }
        do {
            $sample = & $sampleConnectivity $source $gateway $exitClock $TraceWlanCoexistence.IsPresent 'After' $ProbeLocalGateway.IsPresent $probeHttpsAddress
            if ($TraceWlanCoexistence) {
                $sample | Export-Csv -LiteralPath $coexistenceLog -NoTypeInformation -Encoding UTF8 -Append
            }
            if ($null -eq $after) {
                $after = $sample
                Write-Output "WLAN after AirDrop: gateway=$($after.Gateway) HTTPS=$($after.Https)"
            }
            if ($WatchWifiAfter) {
                Write-Output "WLAN exit +$($sample.ExitOffsetMs)ms: gateway=$($sample.Gateway) HTTPS=$($sample.Https) curl-exit=$($sample.HttpsExit) HTTP=$($sample.HttpStatus)"
            }
            if (!$WatchWifiAfter -or $exitClock.Elapsed.TotalSeconds -ge $watchSeconds) { break }
            Start-Sleep -Milliseconds 750
        } while ($exitClock.Elapsed.TotalSeconds -lt $watchSeconds)
    } finally {
        if ($wiredMonitor) {
            Stop-Job -Job $wiredMonitor
            Remove-Job -Job $wiredMonitor
        }
        if ($stationIoMonitor) {
            Stop-Job -Job $stationIoMonitor
            Receive-Job -Job $stationIoMonitor -ErrorAction Continue
            Remove-Job -Job $stationIoMonitor
        }
    }
    if ($WatchWifiAfter) {
        $eventLog = 'Microsoft-Windows-WLAN-AutoConfig/Operational'
        $log = Get-WinEvent -ListLog $eventLog -ErrorAction SilentlyContinue
        if ($log.IsEnabled) {
            $guid = ([guid]$adapter[0].InterfaceGuid).ToString('B')
            $events = @(Get-WinEvent -FilterHashtable @{
                LogName = $eventLog
                Id = 8001,8003
                StartTime = $exitedAt.AddSeconds(-5)
            } -ErrorAction SilentlyContinue | Where-Object {
                $event = [xml]$_.ToXml()
                ($event.Event.EventData.Data | Where-Object Name -eq InterfaceGuid).'#text' -eq $guid
            })
            Write-Output "WLAN exit association events: $($events.Count)"
            foreach ($event in ($events | Sort-Object TimeCreated)) {
                $offsetMs = [math]::Round(($event.TimeCreated - $exitedAt).TotalMilliseconds)
                Write-Output "WLAN exit association event: offset-ms=$offsetMs id=$($event.Id)"
            }
        } else {
            Write-Output 'WLAN exit association events: log unavailable'
        }
    }
    if ($transferExit -ne 0) { exit $transferExit }
    if (!$after.Gateway -or !$after.Https -or $internetReplies -eq 0) { exit 7 }
    exit 0
}

function Get-PktMonFilterList([string]$Path) {
    $startInfo = [Diagnostics.ProcessStartInfo]::new($Path, 'filter list')
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    # PktMon writes redirected output as UTF-8 even in a legacy-code-page console.
    $startInfo.StandardOutputEncoding = [Text.Encoding]::UTF8
    $process = [Diagnostics.Process]::Start($startInfo)
    try {
        $output = $process.StandardOutput.ReadToEnd()
        $process.WaitForExit()
        [pscustomobject]@{ ExitCode = $process.ExitCode; Text = $output.TrimEnd() }
    } finally {
        $process.Dispose()
    }
}

function Invoke-WlanProbeCapture {
    $pktmon = Join-Path $env:SystemRoot 'System32\pktmon.exe'
    $logman = Join-Path $env:SystemRoot 'System32\logman.exe'
    $session = & $logman query PktMon -ets
    if ($LASTEXITCODE -eq 0) { throw 'A PktMon capture is already running.' }
    if ($LASTEXITCODE -ne -2144337918) { throw "Could not check PktMon session: $session" }

    # PktMon only supports removing all filters, so start with an empty list.
    $filters = Get-PktMonFilterList $pktmon
    if ($filters.ExitCode -ne 0 -or $filters.Text -notmatch '(?m)^\s*(None|\u65E0)\.?\s*$') {
        throw "PktMon must have an empty filter list for this capture. $($filters.Text)"
    }

    $capturePath = Join-Path $root "build\wificx-probes-$(Get-Date -Format 'yyyyMMdd-HHmmss-fff').etl"
    $filterSnapshot = $null
    $captureStarted = $false
    try {
        & $pktmon filter add AwdlWlanHttps -i $source -t TCP -p 443
        if ($LASTEXITCODE -ne 0) { throw 'Could not add the WLAN TCP filter.' }
        $filterSnapshot = Get-PktMonFilterList $pktmon
        & $pktmon filter add AwdlWlanGateway -i $source $gateway -t ICMP
        if ($LASTEXITCODE -ne 0) { throw 'Could not add the WLAN gateway filter.' }
        $filterSnapshot = Get-PktMonFilterList $pktmon
        if ($ProbeLocalGateway) {
            & $pktmon filter add AwdlWlanGatewayTcp -i $source $gateway -t TCP -p 80
            if ($LASTEXITCODE -ne 0) { throw 'Could not add the WLAN gateway TCP filter.' }
            $filterSnapshot = Get-PktMonFilterList $pktmon
        }

        & $pktmon start --capture --comp all --type all --pkt-size 96 --file-size 64 --file-name $capturePath
        if ($LASTEXITCODE -ne 0) { throw 'Could not start the WLAN probe capture.' }
        $captureStarted = $true
        Write-Output "WLAN probe capture: $capturePath"
        Invoke-WifiAirDropTransfer
    } finally {
        if ($captureStarted) {
            & $pktmon stop
            $captureStarted = $LASTEXITCODE -ne 0
            if ($captureStarted) { Write-Warning 'PktMon could not stop the WLAN probe capture.' }
        }
        if (!$captureStarted -and $filterSnapshot) {
            $currentFilters = Get-PktMonFilterList $pktmon
            if ($filterSnapshot.ExitCode -eq 0 -and $currentFilters.ExitCode -eq 0 -and
                $currentFilters.Text -ceq $filterSnapshot.Text) {
                & $pktmon filter remove
                if ($LASTEXITCODE -ne 0) { Write-Warning 'PktMon could not remove the test filters.' }
            } else {
                Write-Warning 'PktMon filters could not be verified for cleanup.'
            }
        }
    }
}

function Invoke-StationPacketCapture {
    $logman = Join-Path $env:SystemRoot 'System32\logman.exe'
    $probeHttpsAddress = [string](Resolve-DnsName -Name 'www.baidu.com' -Type A -DnsOnly |
        Where-Object { $_.Type -eq 'A' } | Select-Object -First 1 -ExpandProperty IPAddress)
    if (!$probeHttpsAddress) { throw 'Cannot resolve the HTTPS probe address.' }
    [byte[]]$targets = ([ipaddress]$gateway).GetAddressBytes() + ([ipaddress]$probeHttpsAddress).GetAddressBytes()
    $key = 'HKLM:\SYSTEM\CurrentControlSet\Services\mt7922wificx'
    $request = 'StationPacketTraceTargets'
    $session = "Mt7922StationPackets-$PID"
    $path = Join-Path $root "build\wificx-station-packets-$(Get-Date -Format 'yyyyMMdd-HHmmss-fff').etl"
    & $logman create trace $session -p '{79ed2cd0-f984-4bb5-b216-deefa87b1ed9}' 0x0 4 `
        -o $path -f bincirc -max 32 -ct perf -ets
    if ($LASTEXITCODE -ne 0) { throw 'Could not start the station packet ETW capture.' }
    try {
        New-ItemProperty -LiteralPath $key -Name $request -PropertyType Binary -Value $targets -Force | Out-Null
        $deadline = [Diagnostics.Stopwatch]::StartNew()
        do {
            $pending = (Get-ItemProperty -LiteralPath $key -Name $request -ErrorAction SilentlyContinue).$request
            if ($null -eq $pending) { break }
            Start-Sleep -Milliseconds 100
        } while ($deadline.Elapsed.TotalSeconds -lt 6)
        if ($null -ne $pending) { throw 'The station driver did not acknowledge the packet trace targets.' }
        Write-Output "WLAN station packet capture: $path (router $gateway`:80, HTTPS $probeHttpsAddress`:443)"
        Invoke-WlanProbeCapture
    } finally {
        & $logman stop $session -ets
        if ($LASTEXITCODE -ne 0) { Write-Warning 'Could not stop the station packet ETW capture.' }
        $pending = (Get-ItemProperty -LiteralPath $key -Name $request -ErrorAction SilentlyContinue).$request
        if ($null -ne $pending -and [Convert]::ToBase64String($pending) -ceq [Convert]::ToBase64String($targets)) {
            Remove-ItemProperty -LiteralPath $key -Name $request -ErrorAction SilentlyContinue
        }
    }
}

if ($transfer) {
    $previousConsoleMode = $null
    if ($Host.Name -eq 'ConsoleHost' -and ![Console]::IsInputRedirected) {
        if (!('Mt7922TestConsole' -as [type])) {
            Add-Type @'
using System;
using System.Runtime.InteropServices;

public static class Mt7922TestConsole
{
    private const int StandardInput = -10;
    private const uint QuickEdit = 0x0040;
    private const uint ExtendedFlags = 0x0080;

    [DllImport("kernel32.dll")]
    private static extern IntPtr GetStdHandle(int handle);

    [DllImport("kernel32.dll")]
    private static extern bool GetConsoleMode(IntPtr input, out uint mode);

    [DllImport("kernel32.dll")]
    private static extern bool SetConsoleMode(IntPtr input, uint mode);

    public static uint? DisableQuickEdit()
    {
        IntPtr input = GetStdHandle(StandardInput);
        uint mode;
        if (!GetConsoleMode(input, out mode) || (mode & QuickEdit) == 0)
            return null;
        return SetConsoleMode(input, (mode | ExtendedFlags) & ~QuickEdit)
            ? (uint?)mode : null;
    }

    public static void Restore(uint mode)
    {
        SetConsoleMode(GetStdHandle(StandardInput), mode | ExtendedFlags);
    }
}
'@
        }
        $previousConsoleMode = [Mt7922TestConsole]::DisableQuickEdit()
    }
    try {
        if ($TraceStationPackets) {
            Invoke-StationPacketCapture
        } elseif ($CaptureWlanProbes) {
            Invoke-WlanProbeCapture
        } else {
            Invoke-WifiAirDropTransfer
        }
    } finally {
        if ($null -ne $previousConsoleMode) {
            [Mt7922TestConsole]::Restore($previousConsoleMode)
        }
    }
}

function Start-WinAwdlBeacon([string]$ScriptPath) {
    $launch = [Diagnostics.ProcessStartInfo]::new()
    $launch.FileName = Join-Path $env:windir 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $launch.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File "{0}"' -f $ScriptPath
    $launch.UseShellExecute = $false
    $launch.CreateNoWindow = $true
    $launch.RedirectStandardOutput = $true
    $process = [Diagnostics.Process]::Start($launch)
    try {
        $line = $process.StandardOutput.ReadLineAsync()
        if (!$line.Wait(10000)) {
            throw 'The WinAWDL Bluetooth advertisement did not report its startup status.'
        }
        if ($line.Result -ne 'advertising') {
            throw "WinAWDL Bluetooth advertisement did not start ($($line.Result))."
        }
        return $process
    } catch {
        Stop-WinAwdlBeacon $process
        throw
    }
}

function Stop-WinAwdlBeacon([Diagnostics.Process]$Process) {
    if ($null -eq $Process) { return }
    try {
        if (!$Process.HasExited) {
            try {
                $Process.Kill()
            } catch [InvalidOperationException] {
                if (!$Process.HasExited) { throw }
            }
            [void]$Process.WaitForExit(3000)
        }
    } finally {
        $Process.Dispose()
    }
}

$service = 'HKLM:\SYSTEM\CurrentControlSet\Services\mt7922wificx'
$requestId = [uint32](Get-Random -Minimum 1 -Maximum ([int]::MaxValue))
$command = [byte[]]::new(12)
[BitConverter]::GetBytes($requestId).CopyTo($command, 0)
$peer = [byte[]]($SharedAdvertPeer.Split(':') | ForEach-Object { [Convert]::ToByte($_, 16) })
$peer.CopyTo($command, 4)
$command[10] = [byte]$SharedEcho.IsPresent
$gatewaySent = 0
$gatewayReplies = 0
$result = $null
$beacon = $null
$requestWritten = $false
try {
    if ($WakePeer) {
        $beacon = Start-WinAwdlBeacon (Join-Path $root 'tools\winawdl\scripts\airdrop-beacon.ps1')
        Write-Output 'AirDrop Bluetooth advertisement started.'
    }
    Write-Output "Shared AWDL TX test: peer=$SharedAdvertPeer, channel=149, request=$requestId, echo=$SharedEcho"
    New-ItemProperty -LiteralPath $service -Name SharedAdvertRequest -PropertyType Binary -Value $command | Out-Null
    $requestWritten = $true
    $elapsed = [Diagnostics.Stopwatch]::StartNew()
    $waitSeconds = if ($SharedEcho) { 27 } else { 15 }
    do {
        ++$gatewaySent
        if (Test-WifiGateway) { ++$gatewayReplies }
        $record = (Get-ItemProperty -LiteralPath $service -Name ConnectDebug -ErrorAction SilentlyContinue).ConnectDebug
        if ($record.Length -ge 624 -and [BitConverter]::ToUInt32($record, 588) -eq $requestId -and
            [BitConverter]::ToUInt32($record, 604) -ne 259) {
            $advertStatus = [BitConverter]::ToUInt32($record, 604)
            if (!$SharedEcho -or $advertStatus -ne 0 -or ($record.Length -ge 664 -and
                [BitConverter]::ToUInt32($record, 624) -eq $requestId -and
                [BitConverter]::ToUInt32($record, 640) -ne 259)) {
                $result = $record
            }
        }
        if ($result -and $elapsed.Elapsed.TotalSeconds -ge 5) {
            break
        }
        Start-Sleep -Milliseconds 500
    } while ($elapsed.Elapsed.TotalSeconds -lt $waitSeconds)
} finally {
    try {
        Stop-WinAwdlBeacon $beacon
    } finally {
        if ($requestWritten) {
            $pending = (Get-ItemProperty -LiteralPath $service -Name SharedAdvertRequest -ErrorAction SilentlyContinue).SharedAdvertRequest
            if ($pending.Length -eq $command.Length -and [BitConverter]::ToUInt32($pending, 0) -eq $requestId) {
                Remove-ItemProperty -LiteralPath $service -Name SharedAdvertRequest -ErrorAction SilentlyContinue
            }
        }
    }
}

Write-Output "Wi-Fi gateway during test: $gatewayReplies/$gatewaySent replies via $source"
if (!(Test-WifiGateway)) {
    Write-Warning 'The Wi-Fi gateway did not answer the post-test check.'
}
if (!$result) {
    throw 'No result from the WiFiCx worker for this request.'
}

$submitted = [BitConverter]::ToUInt32($result, 592)
$released = [BitConverter]::ToUInt32($result, 596)
$releaseStatus = [BitConverter]::ToUInt32($result, 600)
$status = [BitConverter]::ToUInt32($result, 604)
Write-Output ('Shared AWDL TX result: submitted={0} released={1} release-status={2} status=0x{3:X8}' -f
    $submitted, $released, $releaseStatus, $status)
Write-Output ('Shared AWDL hardware: dma-fetched={0} tx-status={1} rate=0x{2:X4} ack-errors=0x{3:X}' -f
    [BitConverter]::ToUInt32($result, 608), [BitConverter]::ToUInt32($result, 612),
    [BitConverter]::ToUInt32($result, 616), [BitConverter]::ToUInt32($result, 620))
if ($status -ne 0 -or !$released) {
    exit 5
}
if ($SharedEcho) {
    $echoStatus = [BitConverter]::ToUInt32($result, 640)
    $replies = [BitConverter]::ToUInt32($result, 660)
    Write-Output ('Shared AWDL echo: submitted={0} released={1} release-status={2} dma-fetched={3} tx-status={4} ack-errors=0x{5:X} replies={6} status=0x{7:X8}' -f
        [BitConverter]::ToUInt32($result, 628), [BitConverter]::ToUInt32($result, 632),
        [BitConverter]::ToUInt32($result, 636), [BitConverter]::ToUInt32($result, 644),
        [BitConverter]::ToUInt32($result, 648), [BitConverter]::ToUInt32($result, 656),
        $replies, $echoStatus)
    if ($echoStatus -ne 0 -or !$replies) {
        exit 6
    }
}
