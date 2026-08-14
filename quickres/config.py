import ctypes
import json
import os
import sys
import time

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

ERROR_ALREADY_EXISTS = 183
SW_RESTORE = 9
MUTEX_NAME = "QuickRes_SingleInstance_Mutex"


def get_app_dir() -> str:
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            path = os.path.join(local_app_data, "QuickRes")
            try:
                os.makedirs(path, exist_ok=True)
                return path
            except Exception:
                pass

    base_dir = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    return base_dir


APP_DIR = get_app_dir()
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
LOG_PATH = os.path.join(APP_DIR, "quickres.log")


def log_msg(msg: str):
    timestamp = time.strftime("[%Y-%m-%d %H:%M:%S]")
    line = f"{timestamp} {msg}\n"
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def load_config() -> dict:
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_config(config_dict: dict):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config_dict, f, indent=2)
    except Exception:
        pass


def update_config(updates: dict):
    cfg = load_config()
    cfg.update(updates)
    save_config(cfg)
    return cfg


def enforce_single_instance():
    mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        hwnd = user32.FindWindowW(None, "QuickRes")
        if hwnd:
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
        sys.exit(0)
    return mutex


def resource_path(relative_path):
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(getattr(sys, "_MEIPASS", ""), relative_path))
        candidates.append(os.path.join(os.path.dirname(sys.executable), relative_path))
    else:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates.append(os.path.join(project_root, relative_path))

    for path in candidates:
        if os.path.exists(path):
            return path

    return candidates[0]