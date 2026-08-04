Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
ps1Path = scriptDir & "\vfd2-env.ps1"
logDir = scriptDir & "\logs"

If Not fso.FolderExists(logDir) Then
    fso.CreateFolder logDir
End If

If Not fso.FileExists(ps1Path) Then
    shell.Popup "vfd2-env.ps1 was not found in:" & vbCrLf & scriptDir, 0, "EnvPilot", 48
    WScript.Quit 1
End If

psCmd = "powershell.exe -NoLogo -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -STA -File """ & ps1Path & """"

On Error Resume Next
' Style 1 = normal process; -WindowStyle Hidden suppresses the black console only.
shell.Run psCmd, 1, False
If Err.Number <> 0 Then
    errLog = logDir & "\launcher_error.log"
    Set logFile = fso.OpenTextFile(errLog, 8, True)
    logFile.WriteLine Now & " | Launch failed: " & Err.Description
    logFile.Close
    shell.Popup "Could not start EnvPilot:" & vbCrLf & Err.Description & vbCrLf & vbCrLf & "Details: " & errLog, 0, "EnvPilot", 16
    WScript.Quit 1
End If
