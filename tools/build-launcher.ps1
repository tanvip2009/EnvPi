# Rebuilds EnvPilot.exe from EnvPilotLauncher.cs.
#
# Uses the C# compiler that ships with the .NET Framework, so there is nothing
# to install. /target:winexe is the part that matters: it marks the binary as a
# Windows application, so no console is ever allocated for it.

$ErrorActionPreference = 'Stop'

$root = Split-Path $PSScriptRoot -Parent
$source = Join-Path $PSScriptRoot 'EnvPilotLauncher.cs'
$output = Join-Path $root 'EnvPilot.exe'

$csc = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $csc)) {
    $csc = 'C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe'
}
if (-not (Test-Path -LiteralPath $csc)) {
    throw "No in-box C# compiler found under C:\Windows\Microsoft.NET."
}

& $csc /nologo /target:winexe /optimize+ /out:"$output" `
    /r:System.dll /r:System.Windows.Forms.dll "$source"

if ($LASTEXITCODE -ne 0) {
    throw "csc.exe failed with exit code $LASTEXITCODE."
}

"Built $output"
(Get-Item -LiteralPath $output) | Select-Object Name, Length, LastWriteTime
