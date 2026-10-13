# Downloads Google's Android SDK Platform-Tools and copies ADB into
# vendor\platform-tools, where LumaLinkStudio.spec picks it up.
$ErrorActionPreference = 'Stop'

$url = 'https://dl.google.com/android/repository/platform-tools-latest-windows.zip'
$files = 'adb.exe', 'AdbWinApi.dll', 'AdbWinUsbApi.dll', 'NOTICE.txt'
$target = Join-Path (Split-Path -Parent $PSScriptRoot) 'vendor\platform-tools'
$work = Join-Path ([IO.Path]::GetTempPath()) ('lumalink-platform-tools-' + [Guid]::NewGuid())

New-Item -ItemType Directory -Path $work | Out-Null
try {
    $archive = Join-Path $work 'platform-tools.zip'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $url -OutFile $archive -UseBasicParsing
    Expand-Archive -Path $archive -DestinationPath $work

    New-Item -ItemType Directory -Force -Path $target | Out-Null
    foreach ($name in $files) {
        Copy-Item (Join-Path $work "platform-tools\$name") $target -Force
    }
    Write-Host "ADB copied to $target"
}
finally {
    Remove-Item -Recurse -Force $work
}
