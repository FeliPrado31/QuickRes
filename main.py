import sys
from tkinter import messagebox

from quickres.config import enforce_single_instance
from quickres.gui import ResSwitcherApp

if __name__ == "__main__":
    if sys.platform != "win32":
        messagebox.showerror("Unsupported", "This tool only works on Windows.")
        sys.exit(1)
    _mutex_handle = enforce_single_instance()
    app = ResSwitcherApp()
    app.mainloop()