$desktop  = [Environment]::GetFolderPath('Desktop')
$taskbar  = "$env:APPDATA\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar"
$lnkPaths = @(
    "$desktop\NSOverlay.lnk",
    "$taskbar\NSOverlay.lnk"
)

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

foreach ($lnkPath in $lnkPaths) {
    if (Test-Path $lnkPath) {
        [LnkAppId]::Set($lnkPath, "NSOverlay.App")
        Write-Host "AppUserModelID set on: $lnkPath"
    }
}
