$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut([Environment]::GetFolderPath('Startup') + '\ClipboardTray.lnk')
$s.TargetPath = 'pythonw'
$s.Arguments = (Join-Path $scriptDir 'clipboard-tray.py')
$s.WorkingDirectory = $scriptDir
$s.Save()
Write-Host 'Startup shortcut updated'
