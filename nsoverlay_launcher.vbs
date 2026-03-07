Set WshShell = CreateObject(""WScript.Shell"")
Set Fso = CreateObject(""Scripting.FileSystemObject"")

RepoDir = Fso.GetParentFolderName(WScript.ScriptFullName)
PythonExe = RepoDir & ""\.venv\Scripts\pythonw.exe""
AppScript = RepoDir & ""\nsoverlay.py""

If Not Fso.FileExists(PythonExe) Then
    PythonExe = ""pythonw""
End If

WshShell.CurrentDirectory = RepoDir
WshShell.Run Chr(34) & PythonExe & Chr(34) & "" "" & Chr(34) & AppScript & Chr(34), 0, False