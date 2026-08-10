import ctypes
import json
import os
import re
import subprocess
import sys
import urllib.request
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

ERROR_ALREADY_EXISTS = 183
SW_RESTORE = 9
MUTEX_NAME = "QuickRes_SingleInstance_Mutex"


def enforce_single_instance():
    mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        hwnd = user32.FindWindowW(None, "QuickRes")
        if hwnd:
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
        sys.exit(0)
    return mutex

CURRENT_VERSION = "1.0.4"
UPDATE_URL = "https://lxzy.my/version.json"


def fetch_version_info():
    request = urllib.request.Request(
        UPDATE_URL,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) QuickRes-Updater"}
    )
    with urllib.request.urlopen(request, timeout=5) as resp:
        return json.loads(resp.read().decode())


def check_for_update(manual=False):
    if not getattr(sys, "frozen", False):
        if manual:
            messagebox.showinfo("Check for updates", "Update checks only run in the built exe.")
        return

    try:
        data = fetch_version_info()
    except Exception as e:
        if manual:
            messagebox.showerror("Check for updates", f"Could not reach the update server.\n\n{e}")
        return

    latest_version = data.get("version", "")
    download_url = data.get("url", "")
    if not latest_version or not download_url:
        if manual:
            messagebox.showerror("Check for updates", "Update server returned bad data.")
        return

    def version_tuple(v):
        return tuple(int(p) for p in v.split("."))

    try:
        is_newer = version_tuple(latest_version) > version_tuple(CURRENT_VERSION)
    except ValueError:
        if manual:
            messagebox.showerror("Check for updates", "Update server returned bad data.")
        return

    if not is_newer:
        if manual:
            messagebox.showinfo("Check for updates", f"You're up to date ({CURRENT_VERSION}).")
        return

    if messagebox.askyesno(
        "Update available",
        f"QuickRes {latest_version} is available (you have {CURRENT_VERSION}).\n\nUpdate now?"
    ):
        apply_update(download_url)


def apply_update(download_url):
    exe_path = os.path.abspath(sys.executable)
    exe_dir = os.path.dirname(exe_path)
    exe_name = os.path.basename(exe_path)
    new_exe_path = os.path.join(exe_dir, "QuickRes_new.exe")
    old_exe_path = os.path.join(exe_dir, f"{exe_name}.old")
    bat_path = os.path.join(exe_dir, "update.bat")

    try:
        request = urllib.request.Request(
            download_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) QuickRes-Updater"}
        )
        with urllib.request.urlopen(request, timeout=30) as resp, open(new_exe_path, "wb") as out_file:
            out_file.write(resp.read())
    except Exception:
        messagebox.showerror("Update failed", "Could not download the update. Try again later.")
        return

    bat_contents = (
        "@echo off\n"
        "timeout /t 5 /nobreak >nul\n"
        f'if exist "{old_exe_path}" del "{old_exe_path}"\n'
        f'ren "{exe_path}" "{exe_name}.old"\n'
        "if errorlevel 1 goto :restore\n"
        f'move /y "{new_exe_path}" "{exe_path}"\n'
        "if errorlevel 1 goto :restore\n"
        f'del "{old_exe_path}"\n'
        "goto :launch\n"
        ":restore\n"
        f'if exist "{old_exe_path}" ren "{old_exe_path}" "{exe_name}"\n'
        ":launch\n"
        "timeout /t 2 /nobreak >nul\n"
        f'start "" "{exe_path}"\n'
        "timeout /t 2 /nobreak >nul\n"
        f'tasklist | find /i "{exe_name}" >nul\n'
        f'if errorlevel 1 start "" "{exe_path}"\n'
        'del "%~f0"\n'
    )

    with open(bat_path, "w") as f:
        f.write(bat_contents)

    subprocess.Popen(
        ["cmd", "/c", bat_path],
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    )

    sys.exit(0)

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

CDS_UPDATEREGISTRY = 0x00000001
CDS_NORESET = 0x10000000
CDS_RESET = 0x40000000


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


ENUM_CURRENT_SETTINGS = -1


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
    devmode.dmPelsWidth = width
    devmode.dmPelsHeight = height
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

def resource_path(relative_path):
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(getattr(sys, "_MEIPASS", ""), relative_path))
        candidates.append(os.path.join(os.path.dirname(sys.executable), relative_path))
    else:
        candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path))

    for path in candidates:
        if os.path.exists(path):
            return path

    return candidates[0]


THEMES = {
    "light": {
        "bg": "#f0f0f0",
        "fg": "#000000",
        "btn_bg": "#e1e1e1",
        "btn_fg": "#000000",
        "btn_active": "#d4d4d4",
        "entry_bg": "#ffffff",
        "entry_fg": "#000000",
        "title_fg": "#1a73e8",
        "status_ok": "green",
        "status_err": "red",
        "canvas_bg": "#f0f0f0",
    },
    "dark": {
        "bg": "#1e1e1e",
        "fg": "#e8e8e8",
        "btn_bg": "#3c3c3c",
        "btn_fg": "#e8e8e8",
        "btn_active": "#4c4c4c",
        "entry_bg": "#2d2d2d",
        "entry_fg": "#e8e8e8",
        "title_fg": "#4d9dff",
        "status_ok": "#4caf50",
        "status_err": "#f44336",
        "canvas_bg": "#1e1e1e",
    },
}


FAQ_ITEMS = [
    (
        "Why does it say \"Resolution not found\"?",
        "QuickRes checks your GPU driver's list of registered resolutions "
        "before switching. If a resolution isn't on that list, Windows has "
        "no way to switch to it yet.\n\n"
        "When this happens, QuickRes shows a popup with a button for your "
        "graphics software (NVIDIA Control Panel, AMD Software, or Intel "
        "Graphics Software, based on what's actually detected in your PC). "
        "Click it, then add the resolution as a custom resolution there:\n\n"
        "NVIDIA: Display > Change Resolution > Customize >"
        "Create Custom Resolution\n\n"
        "AMD: Display > Custom Resolutions > Create New\n\n"
        "Intel: Display > General > Resolution > + (create custom profile)"
    ),
    (
        "Nothing happens when I click a button?",
        "Try running QuickRes as Administrator."
    ),
    (
        "Screen looks wrong after switching? (valorant)",
        "Make sure Valorant is on the fill/agent select screen before you "
        "switch to a stretched res. You have to do this every match, since "
        "loading into a new game resets your resolution and switching too "
        "early will give you black bars. Using QuickRes for this is a lot "
        "faster than going through NVIDIA Control Panel (or AMD/Intel) "
        "manually every time."
    ),
]


class ResSwitcherApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("QuickRes")
        self.resizable(False, False)
        self.geometry("340x440")

        try:
            self.iconbitmap(resource_path("icon.ico"))
        except Exception:
            pass

        self.update_idletasks()
        w, h = 340, 420
        x = (self.winfo_screenwidth() // 2) - (w // 2)
        y = (self.winfo_screenheight() // 2) - (h // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")

        self.theme_name = "light"
        self.themed_frames = []
        self.themed_labels = []
        self.themed_buttons = []

        title_label = tk.Label(
            self, text="QuickRes",
            font=("Segoe UI", 12, "bold", "underline"),
            cursor="hand2"
        )
        title_label.pack(pady=(12, 8))
        title_label.bind("<Button-1>", lambda e: webbrowser.open("https://lxzy.my/"))
        self.title_label = title_label

        self.supported_resolutions = get_supported_resolutions()
        self.gpu_vendors = None

        grid_frame = tk.Frame(self)
        grid_frame.pack(pady=(4, 8))
        self.themed_frames.append(grid_frame)

        for i, (res_label, width, height) in enumerate(QUICK_LIST):
            row, col = divmod(i, 2)
            btn = tk.Button(
                grid_frame, text=res_label, width=13,
                command=lambda w=width, h=height: self.apply_resolution(w, h)
            )
            btn.grid(row=row, column=col, padx=4, pady=4)
            self.themed_buttons.append(btn)

        self.separator = ttk.Separator(self, orient="horizontal")
        self.separator.pack(fill="x", pady=10, padx=10)

        custom_label = tk.Label(self, text="Custom resolution:")
        custom_label.pack(anchor="w", padx=10)
        self.themed_labels.append(custom_label)

        custom_row = tk.Frame(self)
        custom_row.pack(fill="x", padx=10, pady=(4, 0))
        self.themed_frames.append(custom_row)

        self.custom_entry = tk.Entry(custom_row, width=14)
        self.custom_entry.pack(side="left")
        self.custom_entry.insert(0, "e.g. 1440x1080")
        self.custom_entry.bind("<FocusIn>", self._clear_placeholder)
        self.custom_entry.bind("<Return>", lambda e: self.apply_custom())

        apply_btn = tk.Button(custom_row, text="Apply", command=self.apply_custom)
        apply_btn.pack(side="left", padx=(6, 0))
        self.themed_buttons.append(apply_btn)

        self.status_var = tk.StringVar(value="")
        self.status_label = tk.Label(self, textvariable=self.status_var, wraplength=250, fg="green")
        self.status_label.pack(pady=(14, 0), padx=10)

        bottom_row = tk.Frame(self)
        bottom_row.pack(side="bottom", pady=(0, 14))
        self.themed_frames.append(bottom_row)

        faq_btn = tk.Button(bottom_row, text="FAQ", command=self.show_faq)
        faq_btn.pack(side="left", padx=4)
        self.themed_buttons.append(faq_btn)

        self.theme_btn = tk.Button(bottom_row, text="Dark", width=5, command=self.toggle_theme)
        self.theme_btn.pack(side="left", padx=4)
        self.themed_buttons.append(self.theme_btn)

        check_btn = tk.Button(bottom_row, text="Check", command=lambda: check_for_update(manual=True))
        check_btn.pack(side="left", padx=4)
        self.themed_buttons.append(check_btn)

        github_btn = tk.Button(
            bottom_row, text="GitHub",
            command=lambda: webbrowser.open("https://github.com/lxzydev/QuickRes")
        )
        github_btn.pack(side="left", padx=4)
        self.themed_buttons.append(github_btn)

        self.apply_theme()
        self.after(500, check_for_update)

    def toggle_theme(self):
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self.apply_theme()

    def apply_theme(self):
        colors = THEMES[self.theme_name]

        self.configure(bg=colors["bg"])

        for frame in self.themed_frames:
            frame.configure(bg=colors["bg"])

        for label in self.themed_labels:
            label.configure(bg=colors["bg"], fg=colors["fg"])

        for btn in self.themed_buttons:
            btn.configure(
                bg=colors["btn_bg"], fg=colors["btn_fg"],
                activebackground=colors["btn_active"], activeforeground=colors["btn_fg"]
            )

        self.title_label.configure(bg=colors["bg"], fg=colors["title_fg"])

        self.custom_entry.configure(bg=colors["entry_bg"], fg=colors["entry_fg"],
                                     insertbackground=colors["entry_fg"])

        self.status_label.configure(bg=colors["bg"])

        current_fg = self.status_label.cget("fg")
        if current_fg in ("green", THEMES["dark"]["status_ok"]):
            self.status_label.configure(fg=colors["status_ok"])
        elif current_fg in ("red", THEMES["dark"]["status_err"]):
            self.status_label.configure(fg=colors["status_err"])

        self.theme_btn.configure(text="Light" if self.theme_name == "dark" else "Dark")

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TSeparator", background=colors["bg"])

    def show_faq(self):
        colors = THEMES[self.theme_name]

        faq_win = tk.Toplevel(self)
        faq_win.title("FAQ")
        faq_win.resizable(False, False)
        faq_win.configure(bg=colors["bg"])

        fw, fh = 360, 560
        faq_win.geometry(f"{fw}x{fh}")
        faq_win.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() // 2) - (fw // 2)
        y = self.winfo_y() + (self.winfo_height() // 2) - (fh // 2)
        faq_win.geometry(f"{fw}x{fh}+{x}+{y}")

        canvas = tk.Canvas(faq_win, highlightthickness=0, bg=colors["canvas_bg"])
        scrollbar = ttk.Scrollbar(faq_win, orient="vertical", command=canvas.yview)
        content = tk.Frame(canvas, bg=colors["bg"])

        content.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=content, anchor="nw", width=fw - 20)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True, padx=(14, 0), pady=14)
        scrollbar.pack(side="right", fill="y", pady=14)

        for i, (question, answer) in enumerate(FAQ_ITEMS):
            tk.Label(
                content, text=question, justify="left", anchor="w",
                wraplength=fw - 40, font=("Segoe UI", 10, "bold"),
                bg=colors["bg"], fg=colors["fg"]
            ).pack(fill="x", pady=(0 if i == 0 else 16, 4))

            tk.Label(
                content, text=answer, justify="left", anchor="w",
                wraplength=fw - 40, font=("Segoe UI", 9),
                bg=colors["bg"], fg=colors["fg"]
            ).pack(fill="x")

        tk.Button(
            faq_win, text="Close", command=faq_win.destroy,
            bg=colors["btn_bg"], fg=colors["btn_fg"],
            activebackground=colors["btn_active"], activeforeground=colors["btn_fg"]
        ).pack(side="bottom", pady=(0, 14))

    def _clear_placeholder(self, event):
        if self.custom_entry.get() == "e.g. 1440x1080":
            self.custom_entry.delete(0, "end")

    def unsupported_click(self, width, height):
        colors = THEMES[self.theme_name]

        dialog = tk.Toplevel(self)
        dialog.title("Resolution not found")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        dialog.configure(bg=colors["bg"])

        dw, dh = 300, 220
        dialog.geometry(f"{dw}x{dh}")
        dialog.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() // 2) - (dw // 2)
        y = self.winfo_y() + (self.winfo_height() // 2) - (dh // 2)
        dialog.geometry(f"{dw}x{dh}+{x}+{y}")

        tk.Label(
            dialog,
            text=f"Couldn't detect {width} x {height} on your GPU driver.\n\n"
                 "Would you like to open your graphics software to add it?",
            wraplength=260, justify="left",
            bg=colors["bg"], fg=colors["fg"]
        ).pack(padx=16, pady=(16, 10))

        if self.gpu_vendors is None:
            self.gpu_vendors = detect_gpu_vendors()
        vendors_to_show = self.gpu_vendors if self.gpu_vendors else {"nvidia", "amd", "intel"}

        btn_frame = tk.Frame(dialog, bg=colors["bg"])
        btn_frame.pack(pady=(0, 10))

        btn_style = dict(
            bg=colors["btn_bg"], fg=colors["btn_fg"],
            activebackground=colors["btn_active"], activeforeground=colors["btn_fg"]
        )

        if "nvidia" in vendors_to_show:
            tk.Button(
                btn_frame, text="NVIDIA Control Panel", width=22,
                command=lambda: (open_nvidia_control_panel(), dialog.destroy()),
                **btn_style
            ).pack(pady=3)
        if "amd" in vendors_to_show:
            tk.Button(
                btn_frame, text="AMD Software", width=22,
                command=lambda: (open_amd_software(), dialog.destroy()),
                **btn_style
            ).pack(pady=3)
        if "intel" in vendors_to_show:
            tk.Button(
                btn_frame, text="Intel Graphics Software", width=22,
                command=lambda: (open_intel_graphics_software(), dialog.destroy()),
                **btn_style
            ).pack(pady=3)

        tk.Button(dialog, text="Cancel", command=dialog.destroy, **btn_style).pack(pady=(0, 12))

    def apply_resolution(self, width, height):
        colors = THEMES[self.theme_name]
        if (width, height) not in self.supported_resolutions:
            self.unsupported_click(width, height)
            return
        ok, msg = set_resolution(width, height)
        self.status_label.config(fg=colors["status_ok"] if ok else colors["status_err"])
        self.status_var.set(msg)

    def apply_custom(self):
        colors = THEMES[self.theme_name]
        text = self.custom_entry.get().strip()
        match = re.match(r"^(\d{2,5})\s*[x, ]\s*(\d{2,5})$", text)
        if not match:
            self.status_label.config(fg=colors["status_err"])
            self.status_var.set("Format like 1440x1080")
            return
        self.apply_resolution(int(match.group(1)), int(match.group(2)))


if __name__ == "__main__":
    if sys.platform != "win32":
        messagebox.showerror("Unsupported", "This tool only works on Windows.")
        sys.exit(1)
    _mutex_handle = enforce_single_instance()
    app = ResSwitcherApp()
    app.mainloop()
