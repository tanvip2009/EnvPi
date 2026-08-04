' Silent launcher — double-click this for a launch with no console window.
CreateObject("WScript.Shell").Run "wscript.exe //B //Nologo """ & CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName) & "\vfd2-env-launch.vbs""", 0, False
