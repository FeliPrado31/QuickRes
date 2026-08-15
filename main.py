import json
import sys
from tkinter import messagebox

if __name__ == "__main__":
    if sys.platform != "win32":
        messagebox.showerror("Unsupported", "This tool only works on Windows.")
        sys.exit(1)

    if "--monitor-op" in sys.argv:
        import argparse
        from quickres.monitors import run_elevated_worker_op

        parser = argparse.ArgumentParser()
        parser.add_argument("--monitor-op", required=True, choices=["enable", "disable"])
        parser.add_argument("--instance-id", required=True)
        parser.add_argument("--result-file", required=True)
        args = parser.parse_args()

        try:
            ok, message = run_elevated_worker_op(args.monitor_op, args.instance_id)
        except Exception as e:
            ok, message = False, f"Elevated helper crashed: {e}"

        try:
            with open(args.result_file, "w", encoding="utf-8") as f:
                json.dump({"ok": ok, "message": message}, f)
        except Exception:
            sys.exit(1)
        sys.exit(0 if ok else 1)

    from quickres.config import enforce_single_instance
    from quickres.gui import ResSwitcherApp

    _mutex_handle = enforce_single_instance()
    app = ResSwitcherApp()
    app.mainloop()