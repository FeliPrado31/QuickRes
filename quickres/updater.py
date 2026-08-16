import json
import os
import subprocess
import sys
import urllib.request
import threading
from tkinter import messagebox

from quickres import __version__, UPDATE_URL


def fetch_version_info():
    request = urllib.request.Request(
        UPDATE_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) QuickRes-Updater"
        },
    )
    with urllib.request.urlopen(request, timeout=5) as resp:
        return json.loads(resp.read().decode())


def check_for_update(manual=False):
    threading.Thread(target=_check_for_update, args=(manual,), daemon=True).start()


def _check_for_update(manual=False):
    if not getattr(sys, "frozen", False):
        if manual:
            messagebox.showinfo(
                "Check for updates", "Update checks only run in the built exe."
            )
        return

    try:
        data = fetch_version_info()
    except Exception as e:
        if manual:
            messagebox.showerror(
                "Check for updates", f"Could not reach the update server.\n\n{e}"
            )
        return

    latest_version = data.get("version", "")
    download_url = data.get("url", "")
    if not latest_version or not download_url:
        if manual:
            messagebox.showerror(
                "Check for updates", "Update server returned bad data."
            )
        return

    def version_tuple(v):
        return tuple(int(p) for p in v.split("."))

    try:
        is_newer = version_tuple(latest_version) > version_tuple(__version__)
    except ValueError:
        if manual:
            messagebox.showerror(
                "Check for updates", "Update server returned bad data."
            )
        return

    if not is_newer:
        if manual:
            messagebox.showinfo(
                "Check for updates", f"You're up to date ({__version__})."
            )
        return

    if messagebox.askyesno(
        "Update available",
        f"QuickRes {latest_version} is available (you have {__version__}).\n\nUpdate now?",
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
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) QuickRes-Updater"
            },
        )
        with urllib.request.urlopen(request, timeout=30) as resp, open(
            new_exe_path, "wb"
        ) as out_file:
            out_file.write(resp.read())
    except Exception:
        messagebox.showerror(
            "Update failed", "Could not download the update. Try again later."
        )
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
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
    )

    sys.exit(0)
