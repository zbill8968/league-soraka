' League Soraka launcher: starts the overlay elevated (the champion-select data needs
' the League client's command line, which only an elevated process may read) and
' without a console window.
Option Explicit
Dim shell, fso, base, exePath, args, elevated

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
exePath = base & "\LeagueSoraka.exe"

If Not fso.FileExists(exePath) Then
  MsgBox "LeagueSoraka.exe not found next to this launcher.", 16, "League Soraka"
  WScript.Quit 1
End If

elevated = False
For Each args In WScript.Arguments
  If LCase(args) = "/elevated" Then elevated = True
Next

If Not elevated Then
  CreateObject("Shell.Application").ShellExecute "wscript.exe", """" & WScript.ScriptFullName & """ /elevated", base, "runas", 1
  WScript.Quit 0
End If

shell.CurrentDirectory = base
shell.Run """" & exePath & """", 0, False
