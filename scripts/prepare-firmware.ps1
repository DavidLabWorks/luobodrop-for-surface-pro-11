param(
    [Parameter(Mandatory)][string]$FirmwareDirectory
)

$ErrorActionPreference = 'Stop'
$inputDirectory = (Resolve-Path -LiteralPath $FirmwareDirectory).ProviderPath
$required = @{
    'WIFI_MT7922_patch_mcu_1_1_hdr.bin' = '5356256C3A361CE6A06165F67CB01B55AECAAC0E2F8D2D9CBDE6312C4CF1A91C'
    'WIFI_RAM_CODE_MT7922_1.bin' = 'AE4DAB06CC1849682E254FE97183ED093C51382BACA9B3F8A00FD6809AB71884'
}
foreach ($name in $required.Keys) {
    $file = Join-Path $inputDirectory $name
    if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $required[$name]) {
        throw "Unexpected SHA-256 for $name. Use the firmware revision in BUILD.md."
    }
}

$output = Join-Path (Split-Path -Parent $PSScriptRoot) 'build\wificx-x64'
[void][IO.Directory]::CreateDirectory($output)
& python -B (Join-Path $PSScriptRoot 'inspect-firmware.py') $inputDirectory `
    --pack-image (Join-Path $output 'mt7922-reference-image.bin') `
    --pack-clc (Join-Path $output 'mt7922-wificx-clc.bin')
if ($LASTEXITCODE -ne 0) {
    throw "Firmware preparation failed with exit code $LASTEXITCODE."
}
