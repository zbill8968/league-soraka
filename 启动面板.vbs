' League Soraka：无窗口启动
' 供登录自启调用；手动请用「启动面板-管理员.cmd」。
Option Explicit
Dim shell, fso, base, pyw, node, script
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)

pyw = shell.ExpandEnvironmentStrings("%USERPROFILE%") & "\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\pythonw.exe"
If Not fso.FileExists(pyw) Then pyw = "pythonw.exe"
node = shell.ExpandEnvironmentStrings("%USERPROFILE%") & "\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe"
If Not fso.FileExists(node) Then node = "node.exe"

script = base & "\overlay-py\overlay.py"
shell.CurrentDirectory = base
shell.Run """" & pyw & """ """ & script & """ --node """ & node & """", 0, False
