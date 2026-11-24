# Build and run the upstream receive replay natively on the Surface.
param([string]$CaptureDirectory)
$ErrorActionPreference = 'Stop'
if ($CaptureDirectory) { $CaptureDirectory = (Resolve-Path -LiteralPath $CaptureDirectory).Path }
$project = Split-Path $PSScriptRoot -Parent
$work = Join-Path $env:TEMP ('SurfaceRxReplay-' + [guid]::NewGuid().ToString('N'))
[void](New-Item -ItemType Directory -Path $work)
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
$vs = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.ARM64 -property installationPath
if (-not $vs) { throw 'Visual Studio ARM64 C++ tools are required.' }
& (Join-Path $vs 'Common7/Tools/Launch-VsDevShell.ps1') -Arch arm64 -HostArch amd64 -SkipAutomaticLocation

$revision = '658980a647696bdc45ac92e106f83fd9234517fe'
$blobs = [ordered]@{
    discovery = '81a6cd39f4c94f6349e22b6804f7ca03e5f73cf1'
    receive = 'de890e4d7b17e5d51aad43b97482e1310617a461'
    send = 'be7b53e5b83ce5e925f30bb93a4898e3babcf430'
}
Push-Location $work
try {
    $captures = foreach ($stage in $blobs.Keys) {
        $name = "$stage-ios26.x-mt7925e-ch44-1.pcap"
        if ($CaptureDirectory) {
            Copy-Item -LiteralPath (Join-Path $CaptureDirectory $name) -Destination $name
        } else {
            Invoke-WebRequest "https://raw.githubusercontent.com/t4t5/owly/$revision/tests/captures/$name" -TimeoutSec 30 -OutFile $name
        }
        if ((git hash-object --no-filters -- $name) -ne $blobs[$stage]) { throw "Unexpected capture: $name" }
        $name
    }
    & cl.exe /nologo /c /kernel /Zl /std:c17 /W4 /WX /O2 "$project/src/rx.c" /Fo:surface-rx-kernel.obj
    if ($LASTEXITCODE) { throw "Kernel-mode object compilation failed ($LASTEXITCODE)." }
    & cl.exe /nologo /c /kernel /Zl /std:c17 /W4 /WX /O2 "$project/src/wcn7850-mgmt.c" /Fo:wcn7850-mgmt.obj
    if ($LASTEXITCODE) { throw "WCN7850 event reader compilation failed ($LASTEXITCODE)." }
    & cl.exe /nologo /c /DWCN7850_REPLAY /std:c17 /W4 /WX /O2 /MT "$project/tests/rx.c" /Fo:rx-test.obj
    if ($LASTEXITCODE) { throw "Test compilation failed ($LASTEXITCODE)." }
    & cl.exe /nologo /std:c17 /W4 /WX /O2 /MT "$project/src/tx.c" surface-rx-kernel.obj wcn7850-mgmt.obj rx-test.obj /Fe:surface-rx.exe /link /MACHINE:ARM64
    if ($LASTEXITCODE) { throw "ARM64 build failed ($LASTEXITCODE)." }
    & .\surface-rx.exe @captures
    if ($LASTEXITCODE) { throw "Replay failed ($LASTEXITCODE)." }
    Write-Output "ARM64 replay output: $work"
} finally {
    Pop-Location
}
