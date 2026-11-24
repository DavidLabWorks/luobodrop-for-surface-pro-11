param([switch]$UserMode, [switch]$TestMcu, [switch]$TestRx, [switch]$WinAwdlMergeOnly, [switch]$WifiCx, [switch]$QueryOnly, [string]$WdkRoot, [switch]$TestNetRings)

$ErrorActionPreference = 'Stop'
if ($WifiCx -and ($UserMode -or $WinAwdlMergeOnly -or $QueryOnly)) {
    throw '-WifiCx builds the separate kernel-mode WiFiCx target.'
}

if ($TestNetRings -and ($WifiCx -or $QueryOnly -or $WinAwdlMergeOnly -or $UserMode -or $TestMcu -or $TestRx)) {
    throw '-TestNetRings runs the standalone NetAdapterCx ring tests.'
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$wdk = if ($WdkRoot) { (Resolve-Path -LiteralPath $WdkRoot).ProviderPath }
       else { Join-Path $projectRoot 'tools\wdk-10.0.26100.6584\c' }
$programFiles32 = [Environment]::GetFolderPath('ProgramFilesX86')
$sdk = Join-Path $programFiles32 'Windows Kits\10'
$kitVersion = '10.0.26100.0'
$kmdfVersion = '1.33'
$vswhere = Join-Path $programFiles32 'Microsoft Visual Studio\Installer\vswhere.exe'

if (!$QueryOnly -and !$WinAwdlMergeOnly -and !(Test-Path -LiteralPath $wdk)) {
    throw "WDK not found. Pass -WdkRoot with the extracted WDK package c directory: $wdk"
}
if (-not (Test-Path -LiteralPath $vswhere)) {
    throw 'Install the Visual Studio C++ Build Tools for x64.'
}
$cl = & $vswhere -latest -products '*' -find 'VC\Tools\MSVC\**\bin\Hostx64\x64\cl.exe' |
    Select-Object -First 1
if (-not $cl) {
    throw 'The Visual Studio x64 C compiler was not found.'
}

$bin = Split-Path -Parent $cl
$msvc = (Get-Item -LiteralPath $bin).Parent.Parent.Parent.FullName
$link = Join-Path $bin 'link.exe'
$src = Join-Path $projectRoot 'src'
$buildType = if ($UserMode) { 'umdf-x64' } elseif ($WifiCx) { 'wificx-x64' } else { 'x64' }
$output = Join-Path $projectRoot "build\$buildType"
$driverObject = Join-Path $output 'driver.obj'
$queryObject = Join-Path $output 'query.obj'
$mcuObject = Join-Path $output 'mcu.obj'
$rxKernelObject = Join-Path $output 'rx-kernel.obj'
$rxQueueKernelObject = Join-Path $output 'rx-queue-kernel.obj'
$txKernelObject = Join-Path $output 'tx-kernel.obj'
$baKernelObject = Join-Path $output 'pulseit-ba-kernel.obj'
$driverName = if ($UserMode) { 'mt7922-probe.dll' } elseif ($WifiCx) { 'mt7922-wificx.sys' } else { 'mt7922-probe.sys' }
$driver = Join-Path $output $driverName
$query = Join-Path $output 'mt7922-query.exe'
$wdkInclude = Join-Path $wdk "Include\$kitVersion"
$wdkLib = Join-Path $wdk "Lib\$kitVersion\km\x64"
$kmdfInclude = Join-Path $wdk "Include\wdf\kmdf\$kmdfVersion"
$kmdfLib = Join-Path $wdk "Lib\wdf\kmdf\x64\$kmdfVersion"
$sdkInclude = Join-Path $sdk "Include\$kitVersion"
$sdkLib = Join-Path $sdk "Lib\$kitVersion"

if (-not (Test-Path -LiteralPath (Join-Path $sdkInclude 'ucrt\stdio.h'))) {
    throw "Install Windows SDK $kitVersion."
}
[void][IO.Directory]::CreateDirectory($output)

function Invoke-BuildTool {
    param([string]$Program, [string[]]$CommandArguments)

    & $Program @CommandArguments
    if ($LASTEXITCODE -ne 0) {
        throw "$(Split-Path -Leaf $Program) failed with exit code $LASTEXITCODE."
    }
}

if ($QueryOnly -and ($UserMode -or $WinAwdlMergeOnly -or $TestMcu -or $TestRx)) {
    throw '-QueryOnly builds the query executable by itself.'
}

if ($TestMcu -or $TestNetRings) {
    $wificxCompile = @(
        '/nologo', '/c', '/std:c17', '/W4', '/WX', '/external:W0', '/O2', '/MT',
        '/D_AMD64_', '/D_KERNEL_MODE', '/DNDIS630_MINIPORT=1',
        "/external:I$msvc\include", "/external:I$sdkInclude\ucrt", "/external:I$sdkInclude\shared",
        "/external:I$wdkInclude\km", "/external:I$wdkInclude\km\wlan\2.0"
    )
    $wificxNetCompile = $wificxCompile + @(
        '/DKMDF_VERSION_MAJOR=1', '/DKMDF_VERSION_MINOR=33',
        '/DNET_VERSION_MAJOR=2', '/DNET_VERSION_MINOR=5',
        "/external:I$kmdfInclude", "/external:I$wdkInclude\km\netcx\kmdf\adapter\2.5",
        "/external:I$sdkInclude\shared\netcx\shared\1.0"
    )
    $netRingsObject = Join-Path $output 'wificx-netrings.obj'
    $netRingsTestObject = Join-Path $output 'wificx-netrings-test.obj'
    Invoke-BuildTool $cl ($wificxNetCompile + @("/Fo$netRingsObject", (Join-Path $src 'wificx-netrings.c')))
    $netRingsTestCompile = $wificxNetCompile
    if ($TestNetRings) {
        $netRingsTestCompile += '/DLUOBODROP_STANDALONE_NET_RINGS'
    }
    Invoke-BuildTool $cl ($netRingsTestCompile + @(
        "/Fo$netRingsTestObject", (Join-Path $projectRoot 'tests\wificx-netrings.c')
    ))
}

if ($TestNetRings) {
    $ringTest = Join-Path $output 'wificx-netrings-test.exe'
    Invoke-BuildTool $link @(
        '/NOLOGO', '/WX', '/SUBSYSTEM:CONSOLE', '/MACHINE:X64', '/INCREMENTAL:NO',
        "/LIBPATH:$msvc\lib\x64", "/LIBPATH:$sdkLib\ucrt\x64", "/LIBPATH:$sdkLib\um\x64",
        "/OUT:$ringTest", $netRingsObject, $netRingsTestObject
    )
    Invoke-BuildTool $ringTest @()
    exit 0
}

if (!$UserMode -and !$WifiCx -and !$QueryOnly) {
    $mergeSource = Join-Path $projectRoot 'tools\winawdl\awdl\win\tcp_merge.c'
    $mergeObject = Join-Path $output 'winawdl-tcp-merge.obj'
    $mergeDll = Join-Path $output 'winawdl-tcp-merge.dll'
    Invoke-BuildTool $cl @(
        '/nologo', '/c', '/std:c17', '/W4', '/WX', '/external:W0', '/O2', '/MT',
        "/external:I$msvc\include", "/external:I$sdkInclude\ucrt",
        "/Fo$mergeObject", $mergeSource
    )
    Invoke-BuildTool $link @(
        '/NOLOGO', '/WX', '/DLL', '/MACHINE:X64', '/INCREMENTAL:NO',
        "/LIBPATH:$msvc\lib\x64", "/LIBPATH:$sdkLib\ucrt\x64", "/LIBPATH:$sdkLib\um\x64",
        '/EXPORT:tcp_merge_offer', '/EXPORT:tcp_merge_take', "/OUT:$mergeDll",
        "/IMPLIB:$output\winawdl-tcp-merge.lib", $mergeObject
    )
    if ($WinAwdlMergeOnly) {
        Get-Item -LiteralPath $mergeDll | Select-Object FullName, Length
        exit 0
    }
}

if (!$WifiCx) {
    Invoke-BuildTool $cl @(
        '/nologo', '/c', '/std:c17', '/W4', '/WX', '/external:W0', '/O2', '/MT',
        "/external:I$msvc\include", "/external:I$sdkInclude\ucrt", "/external:I$sdkInclude\um", "/external:I$sdkInclude\shared",
        "/Fo$queryObject", (Join-Path $src 'query.c')
    )
    Invoke-BuildTool $link @(
        '/NOLOGO', '/WX', '/SUBSYSTEM:CONSOLE', '/MACHINE:X64', '/INCREMENTAL:NO',
        "/LIBPATH:$msvc\lib\x64", "/LIBPATH:$sdkLib\ucrt\x64", "/LIBPATH:$sdkLib\um\x64",
        "/OUT:$query", $queryObject, 'cfgmgr32.lib', 'wlanapi.lib', 'ole32.lib'
    )

    if ($QueryOnly) {
        Get-Item -LiteralPath $query | Select-Object FullName, Length
        exit 0
    }
}

if ($UserMode) {
    $umdfVersion = '2.33'
    $umdfInclude = Join-Path $wdk "Include\wdf\umdf\$umdfVersion"
    $umdfLib = Join-Path $wdk "Lib\wdf\umdf\x64\$umdfVersion"
    Invoke-BuildTool $cl @(
        '/nologo', '/c', '/std:c17', '/W4', '/WX', '/external:W0', '/O2', '/Z7', '/MT',
        '/DMT7922_USER_MODE', '/DUMDF_VERSION_MAJOR=2', '/DUMDF_VERSION_MINOR=33',
        "/external:I$umdfInclude", "/external:I$wdkInclude\um", "/external:I$msvc\include",
        "/external:I$sdkInclude\ucrt", "/external:I$sdkInclude\um", "/external:I$sdkInclude\shared",
        "/Fo$driverObject", (Join-Path $src 'driver.c')
    )
    Invoke-BuildTool $link @(
        '/NOLOGO', '/WX', '/DLL', '/SUBSYSTEM:WINDOWS', '/MACHINE:X64', '/INCREMENTAL:NO',
        '/DEBUG:FULL', "/PDB:$output\mt7922-probe.pdb", "/OUT:$driver", $driverObject,
        "/LIBPATH:$msvc\lib\x64", "/LIBPATH:$sdkLib\ucrt\x64", "/LIBPATH:$sdkLib\um\x64",
        "$umdfLib\WdfDriverStubUm.lib", 'ntdll.lib'
    )
} else {
    $kernelCompile = @(
        '/nologo', '/c', '/W4', '/WX', '/external:W0', '/O2', '/Z7', '/Zl', '/GS', '/kernel',
        '/D_AMD64_', '/DAMD64', '/D_WIN64', '/D_WIN32_WINNT=0x0A00',
        '/DKMDF_VERSION_MAJOR=1', '/DKMDF_VERSION_MINOR=33',
        "/external:I$wdkInclude\km", "/external:I$wdkInclude\shared", "/external:I$sdkInclude\shared", "/external:I$kmdfInclude", "/external:I$msvc\include", "/external:I$sdkInclude\ucrt"
    )
    $driverCompile = $kernelCompile
    if ($WifiCx) {
        $kernelCompile += '/DMT7922_STATION_PACKET_TRACE'
        $driverCompile = $kernelCompile + '/DMT7922_WIFICX'
    }
    Invoke-BuildTool $cl ($driverCompile + @("/Fo$driverObject", (Join-Path $src 'driver.c')))
    $stationScanKernelObject = Join-Path $output 'station-scan-kernel.obj'
    $stationPortKernelObject = Join-Path $output 'station-port-kernel.obj'
    $stationObserverKernelObject = Join-Path $output 'station-observer-kernel.obj'
    $stationClcKernelObject = Join-Path $output 'station-clc-kernel.obj'
    Invoke-BuildTool $cl ($kernelCompile + @('/std:c17', "/Fo$stationPortKernelObject", (Join-Path $src 'station-port.c')))
    Invoke-BuildTool $cl ($kernelCompile + @('/std:c17', "/Fo$stationScanKernelObject", (Join-Path $src 'station-scan.c')))
    Invoke-BuildTool $cl ($kernelCompile + @('/std:c17', "/Fo$stationObserverKernelObject", (Join-Path $src 'station-observer.c')))
    Invoke-BuildTool $cl ($kernelCompile + @('/std:c17', "/Fo$stationClcKernelObject", (Join-Path $src 'station-clc.c')))
    Invoke-BuildTool $cl ($kernelCompile + @("/Fo$mcuObject", (Join-Path $src 'mcu.c')))
    $wifiLink = @()
    if ($TestMcu -or $WifiCx) {
        $stationKernelCompile = $kernelCompile + @(
            '/std:c17', '/DNDIS630_MINIPORT=1', '/DNET_VERSION_MAJOR=2',
            '/DNET_VERSION_MINOR=5', '/DWIFI_VERSION_MAJOR=1', '/DWIFI_VERSION_MINOR=2',
            "/external:I$wdkInclude\km\wlan\2.0", "/external:I$wdkInclude\km\wificx\1.2",
            "/external:I$wdkInclude\km\netcx\kmdf\adapter\2.5", "/external:I$sdkInclude\shared\netcx\shared\1.0"
        )
        Invoke-BuildTool $cl ($stationKernelCompile + @(
            "/Fo$(Join-Path $output 'wificx-scan-kernel-format.obj')", (Join-Path $src 'wificx-scan.c')
        ))
        Invoke-BuildTool $cl ($stationKernelCompile + @(
            "/Fo$(Join-Path $output 'wificx-scan-kernel.obj')", (Join-Path $src 'wificx-scan-kernel.c')
        ))
        foreach ($source in @('wificx-netrings', 'wificx-netqueues')) {
            Invoke-BuildTool $cl ($stationKernelCompile + @(
                "/Fo$(Join-Path $output "$source-kernel.obj")", (Join-Path $src "$source.c")
            ))
        }
        if ($WifiCx) {
            $deviceObject = Join-Path $output 'wificx-device.obj'
            $stationPacketTraceObject = Join-Path $output 'station-packet-trace.obj'
            Invoke-BuildTool $cl ($stationKernelCompile + @(
                "/Fo$stationPacketTraceObject", (Join-Path $src 'station-packet-trace.c')
            ))
            $tlvObject = Join-Path $output 'wificx-tlv-kernel.obj'
            Invoke-BuildTool $cl ($stationKernelCompile + @("/Fo$deviceObject", (Join-Path $src 'wificx-device.c')))
            Invoke-BuildTool $cl ($kernelCompile + @(
                '/std:c++17', "/Fo$tlvObject", (Join-Path $src 'wificx-tlv-kernel.cpp')
            ))
            $wifiLink = @(
                $deviceObject, $tlvObject, $stationPacketTraceObject,
                "$output\wificx-scan-kernel-format.obj", "$output\wificx-scan-kernel.obj",
                "$output\wificx-netrings-kernel.obj", "$output\wificx-netqueues-kernel.obj",
                "$wdkLib\wificx\1.2\wificxstub.lib",
                "$wdkLib\netcx\kmdf\adapter\2.5\netadaptercxstub.lib",
                "$wdkLib\wlan\2.0\WificxTLVGenParse.lib", "$wdkLib\ndis.lib"
            )
        }
    }
    Invoke-BuildTool $cl ($kernelCompile + @("/Fo$rxKernelObject", (Join-Path $src 'rx.c')))
    Invoke-BuildTool $cl ($kernelCompile + @("/Fo$rxQueueKernelObject", (Join-Path $src 'rx-queue.c')))
    Invoke-BuildTool $cl ($kernelCompile + @("/Fo$txKernelObject", (Join-Path $src 'tx.c')))
    Invoke-BuildTool $cl ($kernelCompile + @("/Fo$baKernelObject", (Join-Path $src 'pulseit-ba-kernel.c')))
    Invoke-BuildTool $link (@(
        '/NOLOGO', '/WX', '/DRIVER', '/KERNEL', '/SUBSYSTEM:NATIVE', '/MACHINE:X64',
        '/ENTRY:FxDriverEntry', '/NODEFAULTLIB', '/INCREMENTAL:NO', '/DEBUG:FULL',
        "/PDB:$([IO.Path]::ChangeExtension($driver, '.pdb'))", "/OUT:$driver", $driverObject, $mcuObject, $rxKernelObject, $rxQueueKernelObject, $txKernelObject, $baKernelObject, $stationScanKernelObject, $stationPortKernelObject, $stationObserverKernelObject, $stationClcKernelObject,
        "$wdkLib\bufferoverflowfastfailk.lib", "$wdkLib\ntoskrnl.lib", "$wdkLib\cng.lib",
        "$kmdfLib\wdfldr.lib", "$kmdfLib\wdfdriverentry.lib"
    ) + $wifiLink)
}
$targets = if ($WifiCx) { @($driver) } else { @($driver, $query) }
Get-Item -LiteralPath $targets | Select-Object FullName, Length

if ($TestMcu -or $TestRx) {
    $rxObject = Join-Path $output 'rx.obj'
    $txObject = Join-Path $output 'tx.obj'
    Invoke-BuildTool $cl @(
        '/nologo', '/c', '/std:c17', '/W4', '/WX', '/external:W0', '/O2', '/MT',
        "/external:I$msvc\include", "/external:I$sdkInclude\ucrt",
        "/external:I$sdkInclude\um", "/external:I$sdkInclude\shared",
        "/Fo$rxObject", (Join-Path $src 'rx.c')
    )
    Invoke-BuildTool $cl @(
        '/nologo', '/c', '/std:c17', '/W4', '/WX', '/external:W0', '/O2', '/MT',
        "/external:I$msvc\include", "/external:I$sdkInclude\ucrt",
        "/external:I$sdkInclude\um", "/external:I$sdkInclude\shared",
        "/Fo$txObject", (Join-Path $src 'tx.c')
    )
}
if ($TestMcu) {
    $testObject = Join-Path $output 'mcu-probe-test.obj'
    $baTestObject = Join-Path $output 'pulseit-ba-test.obj'
    Invoke-BuildTool $cl @(
        '/nologo', '/c', '/std:c17', '/W4', '/WX', '/external:W0', '/O2', '/MT',
        "/external:I$msvc\include", "/external:I$sdkInclude\ucrt",
        "/external:I$sdkInclude\um", "/external:I$sdkInclude\shared",
        "/Fo$baTestObject", (Join-Path $projectRoot 'tools\pulseit-ba\ba_reorder.c')
    )
    $stationScanObject = Join-Path $output 'station-scan-test.obj'
    $stationPortObject = Join-Path $output 'station-port-test.obj'
    $stationObserverObject = Join-Path $output 'station-observer-test.obj'
    $stationClcObject = Join-Path $output 'station-clc-test.obj'
    Invoke-BuildTool $cl @(
        '/nologo', '/c', '/std:c17', '/W4', '/WX', '/external:W0', '/O2', '/MT',
        "/external:I$msvc\include", "/external:I$sdkInclude\ucrt",
        "/Fo$stationPortObject", (Join-Path $src 'station-port.c')
    )
    Invoke-BuildTool $cl @(
        '/nologo', '/c', '/std:c17', '/W4', '/WX', '/external:W0', '/O2', '/MT',
        "/external:I$msvc\include", "/external:I$sdkInclude\ucrt",
        "/Fo$stationObserverObject", (Join-Path $src 'station-observer.c')
    )
    Invoke-BuildTool $cl @(
        '/nologo', '/c', '/std:c17', '/W4', '/WX', '/external:W0', '/O2', '/MT',
        "/external:I$msvc\include", "/external:I$sdkInclude\ucrt",
        "/Fo$stationClcObject", (Join-Path $src 'station-clc.c')
    )
    Invoke-BuildTool $cl @(
        '/nologo', '/c', '/std:c17', '/W4', '/WX', '/external:W0', '/O2', '/MT',
        "/external:I$msvc\include", "/external:I$sdkInclude\ucrt",
        "/Fo$stationScanObject", (Join-Path $src 'station-scan.c')
    )
    $wificxScanObject = Join-Path $output 'wificx-scan.obj'
    $wificxScanTestObject = Join-Path $output 'wificx-scan-test.obj'
    $wificxTlvAllocObject = Join-Path $output 'wificx-tlv-alloc.obj'
    Invoke-BuildTool $cl @(
        '/nologo', '/c', '/W4', '/WX', '/EHsc', '/MT', '/external:W0',
        "/external:I$msvc\include", "/external:I$sdkInclude\ucrt",
        "/Fo$wificxTlvAllocObject", (Join-Path $projectRoot 'tests\wificx-tlv-alloc.cpp')
    )
    $wdkUmLib = Join-Path (Split-Path (Split-Path $wdkLib -Parent) -Parent) 'um\x64'
    Invoke-BuildTool $cl ($wificxCompile + @("/Fo$wificxScanObject", (Join-Path $src 'wificx-scan.c')))
    Invoke-BuildTool $cl ($wificxCompile + @("/Fo$wificxScanTestObject", (Join-Path $projectRoot 'tests\wificx-scan.c')))
    $testBinary = Join-Path $output 'mcu-probe-test.exe'
    Invoke-BuildTool $cl @(
        '/nologo', '/c', '/std:c17', '/W4', '/WX', '/external:W0', '/MT',
        "/external:I$msvc\include", "/external:I$sdkInclude\ucrt",
        "/external:I$sdkInclude\um", "/external:I$sdkInclude\shared",
        "/Fo$testObject", (Join-Path $projectRoot 'tests\mcu-probe.c')
    )
    Invoke-BuildTool $link @(
        '/NOLOGO', '/WX', '/SUBSYSTEM:CONSOLE', '/MACHINE:X64', '/INCREMENTAL:NO',
        "/LIBPATH:$msvc\lib\x64", "/LIBPATH:$sdkLib\ucrt\x64", "/LIBPATH:$sdkLib\um\x64",
        "/OUT:$testBinary", $testObject, $rxObject, $txObject, $baTestObject, $stationScanObject, $stationPortObject, $stationObserverObject, $stationClcObject,
        $wificxScanObject, $wificxScanTestObject, $wificxTlvAllocObject, $netRingsObject, $netRingsTestObject, 'bcrypt.lib', 'advapi32.lib',
        (Join-Path $wdkUmLib 'wlan\2.0\WificxTLVGenParse.lib')
    )
    $factoryImage = Join-Path $output 'mt7922-factory-image.bin'
    $firmwareInspector = Join-Path $PSScriptRoot 'inspect-firmware.py'
    Invoke-BuildTool 'python' @('-B', $firmwareInspector, '--pack-image', $factoryImage) | Out-Null
    $referenceDir = Join-Path $projectRoot 'tools\linux-firmware-mt7922-reference'
    $referenceImage = Join-Path $output 'mt7922-reference-image.bin'
    Invoke-BuildTool 'python' @('-B', $firmwareInspector, $referenceDir, '--pack-image', $referenceImage) | Out-Null
    Invoke-BuildTool $testBinary @($factoryImage, $referenceImage)
}

if ($TestRx) {
    $rxTestObject = Join-Path $output 'rx-test.obj'
    Invoke-BuildTool $cl @(
        '/nologo', '/c', '/std:c17', '/W4', '/WX', '/external:W0', '/O2', '/MT',
        "/external:I$msvc\include", "/external:I$sdkInclude\ucrt",
        "/external:I$sdkInclude\um", "/external:I$sdkInclude\shared",
        "/Fo$rxTestObject", (Join-Path $projectRoot 'tests\rx.c')
    )

    $rxTest = Join-Path $output 'rx-test.exe'
    Invoke-BuildTool $link @(
        '/NOLOGO', '/WX', '/SUBSYSTEM:CONSOLE', '/MACHINE:X64', '/INCREMENTAL:NO',
        "/LIBPATH:$msvc\lib\x64", "/LIBPATH:$sdkLib\ucrt\x64", "/LIBPATH:$sdkLib\um\x64",
        "/OUT:$rxTest", $rxObject, $txObject, $rxTestObject
    )
    $fixtures = Join-Path $projectRoot 'tests\fixtures'
    Invoke-BuildTool $rxTest @(
        (Join-Path $fixtures 'discovery-ios26.x-mt7925e-ch44-1.pcap'),
        (Join-Path $fixtures 'receive-ios26.x-mt7925e-ch44-1.pcap'),
        (Join-Path $fixtures 'send-ios26.x-mt7925e-ch44-1.pcap')
    )
}
