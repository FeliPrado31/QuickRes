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
PENDING_RESTORE_PATH = os.path.join(APP_DIR, "pending_restore.json")


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


def save_pending_restore(data: dict) -> bool:
    """Write the crash-recovery flag. Callers MUST check the return value:
    this flag is the only thing that lets QuickRes detect and offer recovery
    for a monitor left disabled by a crash, so a silent failure here would
    defeat that guarantee.

    Written via temp-file + os.replace (atomic on Windows for paths on the
    same volume) instead of an in-place write, so a crash or power loss
    mid-write can never leave a half-written/corrupt pending_restore.json
    that load_pending_restore() would then have to silently discard.
    """
    tmp_path = PENDING_RESTORE_PATH + f".tmp{os.getpid()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, PENDING_RESTORE_PATH)
        return True
    except Exception as e:
        log_msg(f"Failed to write pending_restore.json: {e}")
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        return False


def load_pending_restore():
    try:
        if os.path.exists(PENDING_RESTORE_PATH):
            with open(PENDING_RESTORE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        log_msg(f"Failed to read pending_restore.json: {e}")
    return None


def clear_pending_restore():
    try:
        if os.path.exists(PENDING_RESTORE_PATH):
            os.remove(PENDING_RESTORE_PATH)
    except Exception as e:
        # Not treated as fatal like a failed save (nothing risky is left
        # unprotected), but log it: a deletion failure here means the
        # "may still be disabled" banner will resurface on next startup
        # even after a successful restore, which should be diagnosable.
        log_msg(f"Failed to clear pending_restore.json: {e}")


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