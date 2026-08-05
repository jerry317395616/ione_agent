$ErrorActionPreference = "SilentlyContinue"
$Root = Join-Path $env:LOCALAPPDATA "I-ONE\Agent"
Unregister-ScheduledTask -TaskName "I-ONE Agent Device" -Confirm:$false
Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -like "*I-ONE\Agent*launch.ps1*" -or
    $_.CommandLine -like "*ufo.client.client*"
} | Invoke-CimMethod -MethodName Terminate | Out-Null
Remove-Item -LiteralPath $Root -Recurse -Force
