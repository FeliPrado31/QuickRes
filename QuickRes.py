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

CURRENT_VERSION = "1.0.1"
UPDATE_URL = "https://lxzy.my/version.json"


def check_for_update(manual=False):
    if not getattr(sys, "frozen", False):
        if manual:
            messagebox.showinfo("Check for updates", "Update checks only run in the built exe.")
        return

    try:
        with urllib.request.urlopen(UPDATE_URL, timeout=5) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        if manual:
            messagebox.showerror("Check for updates", "Could not reach the update server.")
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
    new_exe_path = os.path.join(exe_dir, "QuickRes_new.exe")
    bat_path = os.path.join(exe_dir, "update.bat")

    try:
        urllib.request.urlretrieve(download_url, new_exe_path)
    except Exception:
        messagebox.showerror("Update failed", "Could not download the update. Try again later.")
        return

    bat_contents = (
        "@echo off\n"
        "timeout /t 1 /nobreak >nul\n"
        f'del "{exe_path}"\n'
        f'move /y "{new_exe_path}" "{exe_path}"\n'
        f'start "" "{exe_path}"\n'
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


def set_resolution(width: int, height: int) -> tuple[bool, str]:
    devmode = DEVMODE()
    devmode.dmSize = ctypes.sizeof(DEVMODE)
    devmode.dmPelsWidth = width
    devmode.dmPelsHeight = height
    devmode.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT

    result = user32.ChangeDisplaySettingsW(ctypes.byref(devmode), 0)

    if result == 0:
        return True, f"Now running {width} x {height}"
    else:
        return False, (f"Error code {result}. Try running as Administrator, "
                        f"or that resolution isn't registered with your GPU driver.")


QUICK_LIST = [
    ("1920 x 1080", 1920, 1080),
    ("2560 x 1440", 2560, 1440),
    ("1920 x 1440", 1920, 1440),
    ("1440 x 1080", 1440, 1080),
    ("1600 x 1080", 1600, 1080),
    ("1280 x 1080", 1280, 1080),
    ("1350 x 1080", 1350, 1080),
    ("1280 x 1024", 1280, 1024),
    ("1280 x 960", 1280, 960),
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


FAQ_ITEMS = [
    (
        "Why doesn't my custom resolution work?",
        "Windows can only switch to resolutions your GPU driver already knows "
        "about. Add it first in your GPU control panel:\n\n"
        "NVIDIA: NVIDIA Control Panel > Display > Change Resolution > "
        "Customize > Create Custom Resolution\n\n"
        "AMD: AMD Software > Display > Custom Resolutions > Create New\n\n"
        "Intel: Intel Graphics Software > Display > Custom Resolutions\n\n"
        "Once it's added there, it'll work here too."
    ),
    (
        "Nothing happens when I click a button?",
        "Try running QuickRes as Administrator."
    ),
    (
        "Screen looks wrong after switching?",
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
        self.geometry("300x500")

        try:
            self.iconbitmap(resource_path("icon.ico"))
        except Exception:
            pass

        self.update_idletasks()
        w, h = 300, 500
        x = (self.winfo_screenwidth() // 2) - (w // 2)
        y = (self.winfo_screenheight() // 2) - (h // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")

        title_label = tk.Label(
            self, text="QuickRes",
            font=("Segoe UI", 12, "bold", "underline"),
            fg="#1a73e8", cursor="hand2"
        )
        title_label.pack(pady=(12, 8))
        title_label.bind("<Button-1>", lambda e: webbrowser.open("https://lxzy.my/"))

        for res_label, width, height in QUICK_LIST:
            btn = tk.Button(
                self, text=res_label, width=14,
                command=lambda w=width, h=height: self.apply_resolution(w, h)
            )
            btn.pack(pady=4)

        ttk.Separator(self, orient="horizontal").pack(fill="x", pady=10, padx=10)

        tk.Label(self, text="Custom resolution:").pack(anchor="w", padx=10)
        custom_row = tk.Frame(self)
        custom_row.pack(fill="x", padx=10, pady=(4, 0))

        self.custom_entry = tk.Entry(custom_row, width=14)
        self.custom_entry.pack(side="left")
        self.custom_entry.insert(0, "e.g. 1440x1080")
        self.custom_entry.bind("<FocusIn>", self._clear_placeholder)
        self.custom_entry.bind("<Return>", lambda e: self.apply_custom())

        tk.Button(custom_row, text="Apply", command=self.apply_custom).pack(side="left", padx=(6, 0))

        self.status_var = tk.StringVar(value="")
        self.status_label = tk.Label(self, textvariable=self.status_var, wraplength=250, fg="green")
        self.status_label.pack(pady=(14, 0), padx=10)

        bottom_row = tk.Frame(self)
        bottom_row.pack(side="bottom", pady=(0, 14))

        tk.Button(bottom_row, text="FAQ", command=self.show_faq).pack(side="left", padx=4)
        tk.Button(bottom_row, text="Check", command=lambda: check_for_update(manual=True)).pack(side="left", padx=4)
        tk.Button(
            bottom_row, text="GitHub",
            command=lambda: webbrowser.open("https://github.com/lxzydev/QuickRes")
        ).pack(side="left", padx=4)

        self.after(500, check_for_update)

    def show_faq(self):
        faq_win = tk.Toplevel(self)
        faq_win.title("FAQ")
        faq_win.resizable(False, False)

        fw, fh = 360, 480
        faq_win.geometry(f"{fw}x{fh}")
        faq_win.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() // 2) - (fw // 2)
        y = self.winfo_y() + (self.winfo_height() // 2) - (fh // 2)
        faq_win.geometry(f"{fw}x{fh}+{x}+{y}")

        canvas = tk.Canvas(faq_win, highlightthickness=0)
        scrollbar = ttk.Scrollbar(faq_win, orient="vertical", command=canvas.yview)
        content = tk.Frame(canvas)

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
                wraplength=fw - 40, font=("Segoe UI", 10, "bold")
            ).pack(fill="x", pady=(0 if i == 0 else 16, 4))

            tk.Label(
                content, text=answer, justify="left", anchor="w",
                wraplength=fw - 40, font=("Segoe UI", 9)
            ).pack(fill="x")

        tk.Button(faq_win, text="Close", command=faq_win.destroy).pack(side="bottom", pady=(0, 14))

    def _clear_placeholder(self, event):
        if self.custom_entry.get() == "e.g. 1440x1080":
            self.custom_entry.delete(0, "end")

    def apply_resolution(self, width, height):
        ok, msg = set_resolution(width, height)
        self.status_label.config(fg="green" if ok else "red")
        self.status_var.set(msg)

    def apply_custom(self):
        text = self.custom_entry.get().strip()
        match = re.match(r"^(\d{2,5})\s*[x, ]\s*(\d{2,5})$", text)
        if not match:
            self.status_label.config(fg="red")
            self.status_var.set("Format like 1440x1080")
            return
        self.apply_resolution(int(match.group(1)), int(match.group(2)))


if __name__ == "__main__":
    if sys.platform != "win32":
        messagebox.showerror("Unsupported", "This tool only works on Windows.")
        sys.exit(1)
    app = ResSwitcherApp()
    app.mainloop()
