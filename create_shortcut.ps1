# Creates an NSOverlay desktop shortcut with the correct icon and AppUserModelID
# so it can be pinned to the taskbar correctly.
#
# Usage: right-click -> Run with PowerShell
# Then right-click the shortcut on your desktop and choose "Pin to taskbar".

Set-Location $PSScriptRoot

$scriptDir = $PSScriptRoot
$pythonw   = Join-Path $scriptDir ".venv\Scripts\pythonw.exe"
$script    = Join-Path $scriptDir "nsoverlay.py"
$icon      = Join-Path $scriptDir "icon.ico"
$desktop   = [Environment]::GetFolderPath('Desktop')
$lnkPath   = Join-Path $desktop "NSOverlay.lnk"

if (-not (Test-Path $pythonw)) {
    Write-Host "ERROR: pythonw.exe not found at: $pythonw" -ForegroundColor Red
    Write-Host "Make sure your virtual environment exists (.venv)." -ForegroundColor Red
    pause; exit 1
}

# Create the shortcut
$wsh = New-Object -ComObject WScript.Shell
$lnk = $wsh.CreateShortcut($lnkPath)
$lnk.TargetPath       = $pythonw
$lnk.Arguments        = "`"$script`""
$lnk.WorkingDirectory = $scriptDir
$lnk.IconLocation     = $icon
$lnk.Description      = "NSOverlay - Nightscout Overlay"
$lnk.Save()

Write-Host "Shortcut created at: $lnkPath" -ForegroundColor Cyan

# Set AppUserModelID so Windows groups the taskbar button correctly
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

[ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IPropertyStore {
    int GetCount(out uint c);
    int GetAt(uint i, out PropertyKey k);
    int GetValue(ref PropertyKey k, out PropVariant v);
    int SetValue(ref PropertyKey k, ref PropVariant v);
    int Commit();
}

[StructLayout(LayoutKind.Sequential)]
public struct PropertyKey { public Guid fmtid; public uint pid; }

[StructLayout(LayoutKind.Explicit)]
public struct PropVariant { [FieldOffset(0)] public short vt; [FieldOffset(8)] public IntPtr pwszVal; }

public class LnkAppId {
    [DllImport("shell32.dll", CharSet=CharSet.Unicode)]
    static extern int SHGetPropertyStoreFromParsingName(string p, IntPtr b, uint f, ref Guid r, out IPropertyStore s);

    public static void Set(string path, string appId) {
        Guid iid = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99");
        IPropertyStore ps;
        SHGetPropertyStoreFromParsingName(path, IntPtr.Zero, 2, ref iid, out ps);
        PropertyKey k = new PropertyKey();
        k.fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3");
        k.pid = 5;
        PropVariant v = new PropVariant();
        v.vt = 31;
        v.pwszVal = Marshal.StringToCoTaskMemUni(appId);
        ps.SetValue(ref k, ref v);
        ps.Commit();
    }
}
'@

[LnkAppId]::Set($lnkPath, "NSOverlay.App")
Write-Host "AppUserModelID set to: NSOverlay.App" -ForegroundColor Cyan

Write-Host ""
Write-Host "Done! Now right-click 'NSOverlay' on your desktop and choose 'Pin to taskbar'." -ForegroundColor Green
