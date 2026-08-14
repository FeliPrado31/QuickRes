import ctypes
import os
import subprocess
import webbrowser

user32 = ctypes.windll.user32


class DEVMODE(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * 32),
        ("dmSpecVersion", ctypes.c_ushort),
        ("dmDriverVersion", ctypes.c_ushort),
        ("dmSize", ctypes.c_ushort),
        ("dmDriverExtra", ctypes.c_ushort),
        ("dmFields", ctypes.c_ulong),
        ("dmPositionX", ctypes.c_long),
        ("dmPositionY", ctypes.c_long),
        ("dmDisplayOrientation", ctypes.c_ulong),
        ("dmDisplayFixedOutput", ctypes.c_ulong),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", ctypes.c_wchar * 32),
        ("dmLogPixels", ctypes.c_ushort),
        ("dmBitsPerPel", ctypes.c_ulong),
        ("dmPelsWidth", ctypes.c_ulong),
        ("dmPelsHeight", ctypes.c_ulong),
        ("dmDisplayFlags", ctypes.c_ulong),
        ("dmDisplayFrequency", ctypes.c_ulong),
    ]


DM_PELSWIDTH = 0x80000
DM_PELSHEIGHT = 0x100000
DM_DISPLAYFREQUENCY = 0x400000

CDS_UPDATEREGISTRY = 0x00000001
CDS_NORESET = 0x10000000
CDS_RESET = 0x40000000

ENUM_CURRENT_SETTINGS = -1

QUICK_LIST = [
    ("1920 x 1080", 1920, 1080),
    ("2560 x 1440", 2560, 1440),
    ("1920 x 1440", 1920, 1440),
    ("1620 x 1080", 1620, 1080),
    ("1568 x 1080", 1568, 1080),
    ("1440 x 1080", 1440, 1080),
    ("1600 x 1080", 1600, 1080),
    ("1280 x 1080", 1280, 1080),
    ("1350 x 1080", 1350, 1080),
    ("1280 x 1024", 1280, 1024),
    ("1080 x 1080", 1080, 1080),
    ("1280 x 960", 1280, 960),
    ("1024 x 768", 1024, 768),
    ("800 x 1080", 800, 1080),
]


def get_current_resolution() -> tuple[int, int] | None:
    devmode = DEVMODE()
    devmode.dmSize = ctypes.sizeof(DEVMODE)
    if user32.EnumDisplaySettingsW(None, ENUM_CURRENT_SETTINGS, ctypes.byref(devmode)):
        return devmode.dmPelsWidth, devmode.dmPelsHeight
    return None


def get_supported_resolutions() -> set:
    supported = set()
    devmode = DEVMODE()
    devmode.dmSize = ctypes.sizeof(DEVMODE)
    i = 0
    while user32.EnumDisplaySettingsW(None, i, ctypes.byref(devmode)):
        supported.add((devmode.dmPelsWidth, devmode.dmPelsHeight))
        i += 1
    return supported


def set_resolution(width: int, height: int) -> tuple[bool, str]:
    devmode = DEVMODE()
    devmode.dmSize = ctypes.sizeof(DEVMODE)

    has_refresh_rate = bool(
        user32.EnumDisplaySettingsW(None, ENUM_CURRENT_SETTINGS, ctypes.byref(devmode))
        and devmode.dmDisplayFrequency > 0
    )
    refresh_rate = devmode.dmDisplayFrequency if has_refresh_rate else 0

    devmode.dmPelsWidth = width
    devmode.dmPelsHeight = height
    devmode.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT
    if has_refresh_rate:
        devmode.dmDisplayFrequency = refresh_rate
        devmode.dmFields |= DM_DISPLAYFREQUENCY

    result = user32.ChangeDisplaySettingsW(
        ctypes.byref(devmode), CDS_UPDATEREGISTRY | CDS_NORESET
    )

    if result != 0 and has_refresh_rate:
        devmode.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT
        result = user32.ChangeDisplaySettingsW(
            ctypes.byref(devmode), CDS_UPDATEREGISTRY | CDS_NORESET
        )

    if result != 0:
        return False, (f"Error code {result}. Try running as Administrator, "
                        f"or that resolution isn't registered with your GPU driver.")

    result = user32.ChangeDisplaySettingsW(None, CDS_RESET)

    if result == 0:
        return True, f"Now running {width} x {height}"
    else:
        return False, (f"Error code {result}. Try running as Administrator, "
                        f"or that resolution isn't registered with your GPU driver.")


def detect_gpu_vendors() -> set:
    vendors = set()
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        output = result.stdout.lower()
        if "nvidia" in output:
            vendors.add("nvidia")
        if "amd" in output or "radeon" in output:
            vendors.add("amd")
        if "intel" in output:
            vendors.add("intel")
    except Exception:
        pass
    return vendors


def launch_appx_app(name_like: str, publisher_like: str = "") -> bool:
    try:
        publisher_clause = f" -and $_.Publisher -like '*{publisher_like}*'" if publisher_like else ""
        ps_cmd = (
            f"$pkg = Get-AppxPackage | Where-Object {{ $_.Name -like '*{name_like}*'{publisher_clause} }} "
            f"| Select-Object -First 1; "
            f"if ($pkg) {{ "
            f"$manifest = Get-AppxPackageManifest $pkg; "
            f"$appId = $manifest.Package.Applications.Application.Id; "
            f"Write-Output ($pkg.PackageFamilyName + '!' + $appId) "
            f"}}"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        aumid = result.stdout.strip()
        if aumid:
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{aumid}"])
            return True
    except Exception:
        pass
    return False


def launch_start_app(*name_substrings: str) -> bool:
    for substring in name_substrings:
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-StartApps | Where-Object {{ $_.Name -like '*{substring}*' }} "
                 f"| Select-Object -First 1 -ExpandProperty AppID)"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            aumid = result.stdout.strip()
            if aumid:
                subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{aumid}"])
                return True
        except Exception:
            pass
    return False


def open_nvidia_control_panel():
    legacy_path = r"C:\Program Files\NVIDIA Corporation\Control Panel Client\nvcplui.exe"
    if os.path.exists(legacy_path):
        subprocess.Popen([legacy_path])
        return
    if launch_start_app("NVIDIA Control Panel"):
        return
    webbrowser.open("https://www.nvidia.com/en-us/geforce/guides/nvidia-control-panel-quick-start-guide/")


def open_amd_software():
    paths = [
        r"C:\Program Files\AMD\CNext\CNext\RadeonSoftware.exe",
        r"C:\Program Files (x86)\AMD\CNext\CNext\RadeonSoftware.exe",
    ]
    for p in paths:
        if os.path.exists(p):
            subprocess.Popen([p])
            return
    if launch_start_app("AMD Software", "Radeon Software", "Radeon"):
        return
    webbrowser.open("https://www.amd.com/en/resources/support-articles/faqs/DH-032.html")


def open_intel_graphics_software():
    if launch_appx_app("Graphics", "Intel"):
        return
    if launch_start_app("Graphics Command Center", "Intel Graphics", "Command Center"):
        return
    webbrowser.open("https://www.intel.com/content/www/us/en/support/articles/000090440/graphics.html")