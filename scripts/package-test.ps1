param(
    [switch]$KernelMode,
    [switch]$WifiCx,
    [string]$PackageDirectory,
    [string]$WdkRoot,
    [ValidatePattern('^[A-Fa-f0-9]{40}$')][string]$SigningCertificateThumbprint
)

$ErrorActionPreference = 'Stop'
if ($WifiCx -and $KernelMode) {
    throw 'Choose either -KernelMode or -WifiCx.'
}

$root = Split-Path -Parent $PSScriptRoot
$kit = if ($WdkRoot) { (Resolve-Path -LiteralPath $WdkRoot).ProviderPath }
       else { Join-Path $root 'tools\wdk-10.0.26100.6584\c' }
$sdk = Join-Path ([Environment]::GetFolderPath('ProgramFilesX86')) 'Windows Kits\10'
$signTool = Join-Path $sdk 'bin\10.0.26100.0\x64\signtool.exe'
$target = if ($WifiCx) { 'build\wificx-x64' } elseif ($KernelMode) { 'build\x64' } else { 'build\umdf-x64' }
$driverFile = if ($WifiCx) { 'mt7922-wificx.sys' } elseif ($KernelMode) { 'mt7922-probe.sys' } else { 'mt7922-probe.dll' }
$packageName = if ($WifiCx) { 'mt7922-wificx' } elseif ($KernelMode) { 'mt7922-probe' } else { 'mt7922-umdf' }
$build = Join-Path $root $target
$package = if ($PackageDirectory) {
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($PackageDirectory)
} else {
    Join-Path $build 'package'
}
[void][IO.Directory]::CreateDirectory($package)
$inf = Join-Path $package "$packageName.inf"
$driver = Join-Path $package $driverFile
$catalog = Join-Path $package "$packageName.cat"
$publicCertificate = Join-Path $package 'mt7922-test.cer'
Copy-Item -LiteralPath (Join-Path $root "driver\$packageName.inf") -Destination $inf -Force
Copy-Item -LiteralPath (Join-Path $build $driverFile) -Destination $driver -Force
if ($WifiCx) {
    Copy-Item -LiteralPath (Join-Path $build 'mt7922-reference-image.bin') `
        -Destination (Join-Path $package 'mt7922-wificx-fw.bin') -Force
    Copy-Item -LiteralPath (Join-Path $build 'mt7922-wificx-clc.bin') `
        -Destination (Join-Path $package 'mt7922-wificx-clc.bin') -Force
}

$ephemeralSignature = !$SigningCertificateThumbprint
if ($ephemeralSignature) {
    $signature = New-SelfSignedCertificate -Type CodeSigningCert -Subject 'CN=MT7922 AirDrop Local Probe' -CertStoreLocation 'Cert:\CurrentUser\My' -KeyExportPolicy NonExportable -NotAfter (Get-Date).AddDays(1)
} else {
    $signature = Get-Item -LiteralPath "Cert:\CurrentUser\My\$SigningCertificateThumbprint"
    $now = Get-Date
    if (!$signature.HasPrivateKey -or $now -lt $signature.NotBefore -or $now -ge $signature.NotAfter) {
        throw 'The selected signing certificate must be current and have an accessible private key.'
    }
}
try {
    & $signTool sign /fd SHA256 /sha1 $signature.Thumbprint $driver
    if ($LASTEXITCODE -ne 0) { throw 'Driver binary could not be signed.' }

    & (Join-Path $kit 'tools\10.0.26100.0\x64\infverif.exe') /w $inf
    if ($LASTEXITCODE -ne 0) { throw 'INF verification failed.' }

    & (Join-Path $kit 'bin\10.0.26100.0\x86\Inf2Cat.exe') "/driver:$package" /os:10_GE_X64 /uselocaltime
    if ($LASTEXITCODE -ne 0) { throw 'Catalog generation failed.' }

    & $signTool sign /fd SHA256 /sha1 $signature.Thumbprint $catalog
    if ($LASTEXITCODE -ne 0) { throw 'Driver catalog could not be signed.' }

    Export-Certificate -Cert $signature -FilePath $publicCertificate -Force | Out-Null
    foreach ($file in $driver, $catalog) {
        $signedFile = Get-AuthenticodeSignature -LiteralPath $file
        if ($signedFile.SignerCertificate.Thumbprint -ne $signature.Thumbprint) {
            throw "Expected test signature missing: $file"
        }
    }
    Write-Output "Signed package: $package"
}
finally {
    if ($ephemeralSignature) {
        Remove-Item -LiteralPath "Cert:\CurrentUser\My\$($signature.Thumbprint)" -DeleteKey -ErrorAction Stop
    }
}
