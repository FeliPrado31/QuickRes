import re
import threading
import time
import webbrowser
import tkinter as tk
from tkinter import ttk

from quickres.config import (
    resource_path, load_config, update_config,
    save_pending_restore, load_pending_restore, clear_pending_restore,
)
from quickres.display import (
    QUICK_LIST,
    set_resolution,
    get_supported_resolutions,
    get_current_resolution,
    detect_gpu_vendors,
    open_nvidia_control_panel,
    open_amd_software,
    open_intel_graphics_software,
)
from quickres.updater import check_for_update
from quickres.hotkey import HotkeyToggle, HOTKEY_OPTIONS
from quickres import monitors as monitors_mod
from quickres.monitors import PendingDisableGuard

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
        "early will give you black bars. Set a hotkey below so you can "
        "toggle between native and stretched with one keypress instead of "
        "clicking through the app."
    ),
    (
        "What does the hotkey do?",
        "Once you press Start Hotkey, the key you picked toggles your "
        "display between the Native and Stretched resolutions set above. "
        "Press it once on the fill/agent select screen to go stretched, "
        "press it again after the match to go back to native.\n\n"
    ),
]

MAX_CUSTOM_RES = 6


class ResSwitcherApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("QuickRes")
        self.resizable(False, False)
        self.geometry("340x500")

        try:
            self.iconbitmap(resource_path("icon.ico"))
        except Exception:
            pass

        self.update_idletasks()
        w, h = 410, 529
        x = (self.winfo_screenwidth() // 2) - (w // 2)
        y = (self.winfo_screenheight() // 2) - (h // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")

        self.theme_name = "light"
        self.themed_frames = []
        self.themed_labels = []
        self.themed_buttons = []
        self.themed_entries = []

        self.hotkey_toggle = None
        self.hotkey_running = False

        self.faq_win = None
        self.faq_themed_widgets = []

        self.monitors_win = None
        self.monitors_themed_widgets = []
        self.monitor_rows = {}
        self.pending_guard = None
        self.revert_win = None
        self._monitor_op_in_flight = False
        self._active_pending_instance_id = None
        self.restore_banner = None

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

        self.custom_label = tk.Label(self, text="Custom Resolution", font=("Segoe UI", 9, "bold"))
        self.custom_label.pack(pady=(4, 4))
        self.themed_labels.append(self.custom_label)

        custom_row = tk.Frame(self)
        custom_row.pack(pady=(0, 6))
        self.themed_frames.append(custom_row)
        self.custom_entry = tk.Entry(custom_row, width=14)
        self.custom_entry.insert(0, "e.g. 1440x1080")
        self.custom_entry.bind("<FocusIn>", self._clear_placeholder)
        self.custom_entry.bind("<Return>", lambda e: self.apply_custom())
        self.custom_entry.pack(side="left")
        self.themed_entries.append(self.custom_entry)
        custom_btn = tk.Button(custom_row, text="Apply", command=self.apply_custom)
        custom_btn.pack(side="left", padx=(6, 0))
        self.themed_buttons.append(custom_btn)

        self.custom_grid = tk.Frame(self)
        self.custom_grid.pack(pady=(0, 4))
        self.themed_frames.append(self.custom_grid)
        self.custom_buttons = []

        self.separator = ttk.Separator(self, orient="horizontal")
        self.separator.pack(fill="x", pady=10, padx=10)

        hotkey_title = tk.Label(self, text="Hotkey Toggle", font=("Segoe UI", 9, "bold"))
        hotkey_title.pack(anchor="w", padx=10)
        self.themed_labels.append(hotkey_title)

        cfg = load_config()

        hotkey_row = tk.Frame(self)
        hotkey_row.pack(fill="x", padx=10, pady=(4, 0))
        self.themed_frames.append(hotkey_row)
        hotkey_label = tk.Label(hotkey_row, text="Hotkey:")
        hotkey_label.pack(side="left")
        self.themed_labels.append(hotkey_label)
        saved_hotkey = cfg.get("hotkey", "F6")
        self.hotkey_var = tk.StringVar(value=saved_hotkey if saved_hotkey in HOTKEY_OPTIONS else "F6")
        ttk.Combobox(
            hotkey_row, textvariable=self.hotkey_var, values=list(HOTKEY_OPTIONS.keys()),
            width=8, state="readonly"
        ).pack(side="left", padx=(6, 0))

        self.hotkey_btn = tk.Button(hotkey_row, text="Start Hotkey", command=self.toggle_hotkey_mode)
        self.hotkey_btn.pack(side="left", padx=(6, 0))
        self.themed_buttons.append(self.hotkey_btn)

        res_options = [f"{w}x{h}" for _, w, h in QUICK_LIST]

        detected_native = get_current_resolution()
        native_options = list(res_options)
        if detected_native:
            native_str = f"{detected_native[0]}x{detected_native[1]}"
            if native_str not in native_options:
                native_options.insert(0, native_str)
        else:
            native_str = res_options[0]

        native_row = tk.Frame(self)
        native_row.pack(fill="x", padx=10, pady=(6, 0))
        self.themed_frames.append(native_row)
        native_label = tk.Label(native_row, text="Native:")
        native_label.pack(side="left")
        self.themed_labels.append(native_label)
        saved_native = cfg.get("native_res", native_str)
        self.native_var = tk.StringVar(value=saved_native if saved_native in native_options else native_str)
        self.native_combo = ttk.Combobox(
            native_row, textvariable=self.native_var, values=native_options,
            width=11, state="normal"
        )
        self.native_combo.pack(side="left", padx=(6, 0))

        stretched_row = tk.Frame(self)
        stretched_row.pack(fill="x", padx=10, pady=(6, 0))
        self.themed_frames.append(stretched_row)
        stretched_label = tk.Label(stretched_row, text="Stretched:")
        stretched_label.pack(side="left")
        self.themed_labels.append(stretched_label)
        saved_stretched = cfg.get("stretched_res", "1568x1080")
        self.stretched_var = tk.StringVar(
            value=saved_stretched if saved_stretched in res_options else res_options[0]
        )
        self.stretched_combo = ttk.Combobox(
            stretched_row, textvariable=self.stretched_var, values=res_options,
            width=11, state="normal"
        )
        self.stretched_combo.pack(side="left", padx=(6, 0))

        self.status_var = tk.StringVar(value="")
        self.status_label = tk.Label(self, textvariable=self.status_var, wraplength=250, fg="green")
        self.status_label.pack(pady=(14, 0), padx=10)

        self.restore_banner = tk.Button(
            self, text="A monitor may still be disabled from a previous "
                       "session - click to restore it",
            wraplength=280, justify="left", relief="flat",
            command=self._restore_pending_from_banner
        )

        bottom_row = tk.Frame(self)
        bottom_row.pack(side="bottom", pady=(0, 14))
        self.themed_frames.append(bottom_row)

        faq_btn = tk.Button(bottom_row, text="FAQ", command=self.show_faq)
        faq_btn.pack(side="left", padx=4)
        self.themed_buttons.append(faq_btn)

        monitors_btn = tk.Button(bottom_row, text="Monitors", command=self.show_monitors)
        monitors_btn.pack(side="left", padx=4)
        self.themed_buttons.append(monitors_btn)

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

        self._rebuild_custom_section()
        self._sync_hotkey_dropdown_values()
        self.apply_theme()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(500, check_for_update)
        self._check_pending_restore_on_startup()

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

        for entry in self.themed_entries:
            entry.configure(bg=colors["entry_bg"], fg=colors["entry_fg"],
                             insertbackground=colors["entry_fg"])

        self.status_label.configure(bg=colors["bg"])

        current_fg = self.status_label.cget("fg")
        if current_fg in ("green", THEMES["dark"]["status_ok"]):
            self.status_label.configure(fg=colors["status_ok"])
        elif current_fg in ("red", THEMES["dark"]["status_err"]):
            self.status_label.configure(fg=colors["status_err"])

        self.theme_btn.configure(text="Light" if self.theme_name == "dark" else "Dark")

        if self.faq_win is not None and self.faq_win.winfo_exists():
            self.faq_win.configure(bg=colors["bg"])
            for widget in self.faq_themed_widgets:
                kind = widget.winfo_class()
                if kind == "Label":
                    widget.configure(bg=colors["bg"], fg=colors["fg"])
                elif kind == "Frame":
                    widget.configure(bg=colors["bg"])
                elif kind == "Canvas":
                    widget.configure(bg=colors["canvas_bg"])
                elif kind == "Button":
                    widget.configure(
                        bg=colors["btn_bg"], fg=colors["btn_fg"],
                        activebackground=colors["btn_active"], activeforeground=colors["btn_fg"]
                    )

        if self.restore_banner is not None:
            self.restore_banner.configure(
                bg=colors["bg"], fg=colors["status_err"],
                activebackground=colors["bg"], activeforeground=colors["status_err"]
            )

        if self.monitors_win is not None and self.monitors_win.winfo_exists():
            self.monitors_win.configure(bg=colors["bg"])
            for widget in self.monitors_themed_widgets:
                if not widget.winfo_exists():
                    continue
                kind = widget.winfo_class()
                if kind == "Frame":
                    widget.configure(bg=colors["bg"])
                elif kind == "Button":
                    widget.configure(
                        bg=colors["btn_bg"], fg=colors["btn_fg"],
                        activebackground=colors["btn_active"], activeforeground=colors["btn_fg"]
                    )
            # Status/name labels carry status-specific colors, so rebuild them
            # fully against the new theme rather than trying to patch in place.
            self._refresh_monitor_list()

        if self.revert_win is not None and self.revert_win.winfo_exists():
            self.revert_win.configure(bg=colors["bg"])
            # The "Keep disabled"/"Revert now" buttons live inside a nested
            # btn_frame (see _open_revert_dialog), not directly under
            # revert_win, so a shallow winfo_children() misses them entirely
            # — walk one level deeper for frames to actually reach them.
            for widget in self.revert_win.winfo_children():
                kind = widget.winfo_class()
                if kind == "Label":
                    widget.configure(bg=colors["bg"], fg=colors["fg"])
                elif kind == "Frame":
                    widget.configure(bg=colors["bg"])
                    for child in widget.winfo_children():
                        if child.winfo_class() == "Button":
                            child.configure(
                                bg=colors["btn_bg"], fg=colors["btn_fg"],
                                activebackground=colors["btn_active"], activeforeground=colors["btn_fg"]
                            )
                elif kind == "Button":
                    widget.configure(
                        bg=colors["btn_bg"], fg=colors["btn_fg"],
                        activebackground=colors["btn_active"], activeforeground=colors["btn_fg"]
                    )

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TSeparator", background=colors["bg"])
        style.configure(
            "TCombobox",
            fieldbackground=colors["entry_bg"],
            background=colors["btn_bg"],
            foreground=colors["entry_fg"],
            arrowcolor=colors["fg"],
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", colors["entry_bg"])],
            foreground=[("readonly", colors["entry_fg"])],
            selectbackground=[("readonly", colors["entry_bg"])],
            selectforeground=[("readonly", colors["entry_fg"])],
        )

    def show_faq(self):
        if self.faq_win is not None and self.faq_win.winfo_exists():
            self.faq_win.lift()
            self.faq_win.focus_force()
            return

        colors = THEMES[self.theme_name]

        faq_win = tk.Toplevel(self)
        faq_win.title("FAQ")
        faq_win.resizable(False, False)
        faq_win.configure(bg=colors["bg"])
        self.faq_win = faq_win
        self.faq_themed_widgets = []
        faq_win.protocol("WM_DELETE_WINDOW", lambda: self._close_faq(faq_win))

        fw, fh = 400, 620
        faq_win.geometry(f"{fw}x{fh}")
        faq_win.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() // 2) - (fw // 2)
        y = self.winfo_y() + (self.winfo_height() // 2) - (fh // 2)
        faq_win.geometry(f"{fw}x{fh}+{x}+{y}")

        canvas = tk.Canvas(faq_win, highlightthickness=0, bg=colors["canvas_bg"])
        scrollbar = ttk.Scrollbar(faq_win, orient="vertical", command=canvas.yview)
        content = tk.Frame(canvas, bg=colors["bg"])
        self.faq_themed_widgets.append(canvas)
        self.faq_themed_widgets.append(content)

        content.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=content, anchor="nw", width=fw - 20)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True, padx=(14, 0), pady=14)
        scrollbar.pack(side="right", fill="y", pady=14)

        for i, (question, answer) in enumerate(FAQ_ITEMS):
            q_label = tk.Label(
                content, text=question, justify="left", anchor="w",
                wraplength=fw - 40, font=("Segoe UI", 10, "bold"),
                bg=colors["bg"], fg=colors["fg"]
            )
            q_label.pack(fill="x", pady=(0 if i == 0 else 16, 4))
            self.faq_themed_widgets.append(q_label)

            a_label = tk.Label(
                content, text=answer, justify="left", anchor="w",
                wraplength=fw - 40, font=("Segoe UI", 9),
                bg=colors["bg"], fg=colors["fg"]
            )
            a_label.pack(fill="x")
            self.faq_themed_widgets.append(a_label)

        close_btn = tk.Button(
            faq_win, text="Close", command=lambda: self._close_faq(faq_win),
            bg=colors["btn_bg"], fg=colors["btn_fg"],
            activebackground=colors["btn_active"], activeforeground=colors["btn_fg"]
        )
        close_btn.pack(side="bottom", pady=(0, 14))
        self.faq_themed_widgets.append(close_btn)

    def _close_faq(self, faq_win):
        self.faq_win = None
        self.faq_themed_widgets = []
        faq_win.destroy()

    def _clear_placeholder(self, event):
        if self.custom_entry.get() == "e.g. 1440x1080":
            self.custom_entry.delete(0, "end")

    def _parse_res(self, text: str):
        match = re.match(r"^(\d{2,5})\s*[x, ]\s*(\d{2,5})$", text.strip())
        if not match:
            return None
        return int(match.group(1)), int(match.group(2))

    def toggle_hotkey_mode(self):
        if self.hotkey_running:
            if self.hotkey_toggle:
                self.hotkey_toggle.stop()
            self.hotkey_running = False
            self.hotkey_btn.config(text="Start Hotkey")
            self.status_var.set("Hotkey: stopped")
            return

        native = self._parse_res(self.native_var.get())
        stretched = self._parse_res(self.stretched_var.get())
        if not native or not stretched:
            self.status_label.config(fg=THEMES[self.theme_name]["status_err"])
            self.status_var.set("Native/Stretched must look like 1920x1080")
            return

        update_config({
            "hotkey": self.hotkey_var.get(),
            "native_res": self.native_var.get(),
            "stretched_res": self.stretched_var.get()
        })

        self.hotkey_toggle = HotkeyToggle(
            key_name=self.hotkey_var.get(),
            native_res=native,
            stretched_res=stretched,
            on_status=self._set_status_threadsafe,
        )
        self.hotkey_toggle.start()
        self.hotkey_running = True
        self.hotkey_btn.config(text="Stop Hotkey")

    def _set_status_threadsafe(self, message: str):
        self.after(0, lambda: (
            self.status_label.config(fg=THEMES[self.theme_name]["status_ok"]),
            self.status_var.set(message)
        ))

    def _on_close(self):
        if self.hotkey_toggle:
            if self.hotkey_toggle.is_stretched:
                set_resolution(*self.hotkey_toggle.native_res)
            self.hotkey_toggle.stop()
        self.destroy()

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
        parsed = self._parse_res(text)
        if not parsed:
            self.status_label.config(fg=colors["status_err"])
            self.status_var.set("Format like 1440x1080")
            return

        width, height = parsed
        res_str = f"{width}x{height}"
        preset_strs = {f"{w}x{h}" for _, w, h in QUICK_LIST}

        if res_str not in preset_strs:
            cfg = load_config()
            customs = cfg.get("custom_resolutions", [])
            if res_str in customs:
                customs.remove(res_str)
            customs.append(res_str)
            if len(customs) > MAX_CUSTOM_RES:
                customs = customs[-MAX_CUSTOM_RES:]
            update_config({"custom_resolutions": customs})
            self._rebuild_custom_section()
            self._sync_hotkey_dropdown_values()

        self.custom_entry.delete(0, "end")
        self.apply_resolution(width, height)

    def _rebuild_custom_section(self):
        for btn in self.custom_buttons:
            if btn in self.themed_buttons:
                self.themed_buttons.remove(btn)
            btn.destroy()
        self.custom_buttons = []

        cfg = load_config()
        customs = cfg.get("custom_resolutions", [])
        colors = THEMES[self.theme_name]

        for i, res_str in enumerate(customs):
            w_str, h_str = res_str.split("x")
            row, col = divmod(i, 2)
            btn = tk.Button(
                self.custom_grid, text=res_str, width=13,
                command=lambda w=int(w_str), h=int(h_str): self.apply_resolution(w, h),
                bg=colors["btn_bg"], fg=colors["btn_fg"],
                activebackground=colors["btn_active"], activeforeground=colors["btn_fg"]
            )
            btn.bind("<Button-3>", lambda e, r=res_str: self._remove_custom(r))
            btn.grid(row=row, column=col, padx=4, pady=4)
            self.themed_buttons.append(btn)
            self.custom_buttons.append(btn)

        self._resize_for_custom(len(customs))

    def _remove_custom(self, res_str):
        cfg = load_config()
        customs = cfg.get("custom_resolutions", [])
        if res_str in customs:
            customs.remove(res_str)
            update_config({"custom_resolutions": customs})
        self._rebuild_custom_section()
        self._sync_hotkey_dropdown_values()
        self.status_label.config(fg=THEMES[self.theme_name]["status_ok"])
        self.status_var.set(f"Removed {res_str} from custom list")

    def _sync_hotkey_dropdown_values(self):
        cfg = load_config()
        customs = cfg.get("custom_resolutions", [])

        merged = [f"{w}x{h}" for _, w, h in QUICK_LIST]
        for c in customs:
            if c not in merged:
                merged.append(c)

        native_opts = list(merged)
        detected_native = get_current_resolution()
        if detected_native:
            native_str = f"{detected_native[0]}x{detected_native[1]}"
            if native_str not in native_opts:
                native_opts.insert(0, native_str)

        self.native_combo["values"] = native_opts
        self.stretched_combo["values"] = merged

    def _resize_for_custom(self, count):
        extra = 0
        if count:
            rows = (count + 1) // 2
            extra = 30 + rows * 42
        new_h = 529 + extra
        x = self.winfo_x()
        y = self.winfo_y()
        self.geometry(f"410x{new_h}+{x}+{y}")

    # -- Monitor enable/disable -------------------------------------------------

    def _check_pending_restore_on_startup(self):
        pending = load_pending_restore()
        if pending is None:
            return
        instance_id = pending.get("instance_id")
        if not instance_id:
            # Nothing actionable in this record — don't lock the UI or show
            # a banner over a flag that can never be resolved either way.
            clear_pending_restore()
            return
        self._active_pending_instance_id = instance_id
        self._show_restore_banner()

    def _show_restore_banner(self):
        if self.restore_banner is None:
            return
        colors = THEMES[self.theme_name]
        self.restore_banner.configure(
            bg=colors["bg"], fg=colors["status_err"],
            activebackground=colors["bg"], activeforeground=colors["status_err"]
        )
        self.restore_banner.pack_forget()
        self.restore_banner.pack(after=self.status_label, pady=(6, 0), padx=10, fill="x")

    def _hide_restore_banner(self):
        if self.restore_banner is not None:
            self.restore_banner.pack_forget()

    def _restore_pending_from_banner(self):
        # Reentrancy guard: without this, clicking the banner twice quickly
        # (or once while an op from elsewhere is already running) could
        # launch two elevated processes racing CM_Enable/CM_Disable calls
        # against the same device.
        if self._monitor_op_in_flight:
            return
        pending = load_pending_restore()
        if pending is None:
            self._hide_restore_banner()
            return
        instance_id = pending.get("instance_id")
        friendly_name = pending.get("friendly_name", "the monitor")
        if not instance_id:
            clear_pending_restore()
            self._hide_restore_banner()
            return
        self._hide_restore_banner()
        self.status_label.config(fg=THEMES[self.theme_name]["fg"])
        self.status_var.set(f"Restoring {friendly_name}...")
        self._monitor_op_in_flight = True
        self._run_threaded_monitor_op(
            monitors_mod.enable_monitor, instance_id,
            lambda ok, message: self._on_restore_complete(ok, message, friendly_name, instance_id)
        )

    def _on_restore_complete(self, ok, message, friendly_name, instance_id):
        colors = THEMES[self.theme_name]
        self._monitor_op_in_flight = False
        if not ok and message == monitors_mod.TIMEOUT_MESSAGE:
            # Genuinely unknown outcome, not a confirmed failure — the
            # elevated process may still be waiting on a slow UAC prompt and
            # could finish moments later, off in the background. Don't touch
            # the recovery flag or the lock; leave it to a later check.
            self.status_label.config(fg=colors["fg"])
            self.status_var.set(
                f"Still waiting on admin approval to restore {friendly_name} — "
                f"check again in a moment."
            )
            if self.monitors_win is not None and self.monitors_win.winfo_exists():
                self._refresh_monitor_list()
            return
        if not ok and message.startswith(monitors_mod.DEVICE_NOT_FOUND_PREFIX):
            # The device is gone (unplugged/replaced) — nothing left to
            # protect, so the flag is stale. Clear it instead of leaving the
            # user stuck retrying a restore that can never succeed.
            clear_pending_restore()
            self._active_pending_instance_id = None
            self._hide_restore_banner()
            self.status_label.config(fg=colors["status_err"])
            self.status_var.set(
                f"{friendly_name} is no longer present on this system — cleared the stale recovery flag."
            )
            if self.monitors_win is not None and self.monitors_win.winfo_exists():
                self._refresh_monitor_list()
            return
        status_text = f"Restored {friendly_name}"
        if not ok:
            # Same false-negative risk as the disable path, mirrored: a
            # reported failure doesn't prove the re-enable didn't actually
            # happen. Don't leave the "still disabled" banner up forever
            # over a stale/unconfirmed result if the device is really back.
            actual = self._find_monitor(instance_id)
            if actual is not None and actual.is_enabled:
                ok = True
                status_text = f"Restored {friendly_name} (result was unconfirmed, verified by re-check)"
        if ok:
            clear_pending_restore()
            self._active_pending_instance_id = None
            self.status_label.config(fg=colors["status_ok"])
            self.status_var.set(status_text)
        else:
            self._show_restore_banner()
            self.status_label.config(fg=colors["status_err"])
            self.status_var.set(
                f"Failed to restore {friendly_name}: {message}. Please retry manually."
            )
        if self.monitors_win is not None and self.monitors_win.winfo_exists():
            self._refresh_monitor_list()

    def _run_threaded_monitor_op(self, op_func, instance_id, on_done):
        def worker():
            # Without this, an unexpected exception from op_func (a raw
            # ctypes/OS call) would skip on_done entirely, leaving
            # _monitor_op_in_flight/_active_pending_instance_id stuck set
            # forever — every action button permanently disabled and the
            # status label frozen on "Requesting admin approval..." with no
            # way out short of restarting the app.
            try:
                ok, message = op_func(instance_id)
            except Exception as e:
                ok, message = False, f"Unexpected error: {e}"
            try:
                self.after(0, lambda: on_done(ok, message))
            except Exception:
                # Main window was closed while this op was still running.
                # The elevated helper's real work (CM_Disable/Enable) has
                # already happened regardless — pending_restore.json still
                # correctly reflects reality on disk even though there's no
                # UI left to update.
                pass
        threading.Thread(target=worker, daemon=True).start()

    def show_monitors(self):
        if self.monitors_win is not None and self.monitors_win.winfo_exists():
            self.monitors_win.lift()
            self.monitors_win.focus_force()
            return

        colors = THEMES[self.theme_name]

        win = tk.Toplevel(self)
        win.title("Monitors")
        win.resizable(False, False)
        win.configure(bg=colors["bg"])
        self.monitors_win = win
        self.monitors_themed_widgets = []
        win.protocol("WM_DELETE_WINDOW", lambda: self._close_monitors(win))

        mw, mh = 380, 320
        win.geometry(f"{mw}x{mh}")
        win.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() // 2) - (mw // 2)
        y = self.winfo_y() + (self.winfo_height() // 2) - (mh // 2)
        win.geometry(f"{mw}x{mh}+{x}+{y}")

        self.monitors_list_frame = tk.Frame(win, bg=colors["bg"])
        self.monitors_list_frame.pack(fill="both", expand=True, padx=14, pady=14)
        self.monitors_themed_widgets.append(self.monitors_list_frame)

        close_btn = tk.Button(
            win, text="Close", command=lambda: self._close_monitors(win),
            bg=colors["btn_bg"], fg=colors["btn_fg"],
            activebackground=colors["btn_active"], activeforeground=colors["btn_fg"]
        )
        close_btn.pack(side="bottom", pady=(0, 14))
        self.monitors_themed_widgets.append(close_btn)

        self._reconcile_stuck_pending()
        self._refresh_monitor_list()

    def _reconcile_stuck_pending(self):
        # A disable that hit TIMEOUT_MESSAGE leaves _active_pending_instance_id
        # set with no confirm/revert dialog driving it (we didn't know the
        # outcome yet, so none was opened) — the UI stays locked until this
        # resolves. By the time the user reopens Monitors, the elevated
        # helper has almost certainly finished one way or the other, so
        # re-check now instead of leaving them stuck until an app restart.
        if self._active_pending_instance_id is None:
            return
        if self.revert_win is not None and self.revert_win.winfo_exists():
            return  # a normal awaiting-confirmation dialog is already open
        if self._monitor_op_in_flight:
            return  # something is actively running right now, don't interfere

        pending = load_pending_restore()
        if pending is None:
            self._active_pending_instance_id = None
            return
        instance_id = pending.get("instance_id")
        friendly_name = pending.get("friendly_name", "the monitor")
        if not instance_id:
            clear_pending_restore()
            self._active_pending_instance_id = None
            return

        actual = self._find_monitor(instance_id)
        if actual is None:
            return  # still can't confirm — stay locked, fail safe
        if actual.is_enabled:
            # Confirmed back on — nothing left to protect.
            clear_pending_restore()
            self._active_pending_instance_id = None
        else:
            # Confirmed disabled — resume the normal keep/revert flow instead
            # of leaving it silently locked with no dialog to act on.
            self._active_pending_instance_id = instance_id
            self._open_revert_dialog(instance_id, friendly_name)

    def _close_monitors(self, win):
        self.monitors_win = None
        self.monitors_themed_widgets = []
        self.monitor_rows = {}
        win.destroy()

    def _refresh_monitor_list(self):
        if self.monitors_win is None or not self.monitors_win.winfo_exists():
            return

        colors = THEMES[self.theme_name]

        for child in self.monitors_list_frame.winfo_children():
            child.destroy()
        self.monitor_rows = {}

        monitor_list = monitors_mod.enumerate_monitors()

        if not monitor_list:
            empty_label = tk.Label(
                self.monitors_list_frame, text="No monitors found.",
                bg=colors["bg"], fg=colors["fg"]
            )
            empty_label.pack(pady=8)
            return

        for mon in monitor_list:
            row = tk.Frame(self.monitors_list_frame, bg=colors["bg"])
            row.pack(fill="x", pady=4)

            name_label = tk.Label(
                row, text=mon.friendly_name, anchor="w",
                wraplength=180, justify="left",
                bg=colors["bg"], fg=colors["fg"]
            )
            name_label.pack(side="left", fill="x", expand=True)

            status_text = "Enabled" if mon.is_enabled else "Disabled"
            status_color = colors["status_ok"] if mon.is_enabled else colors["status_err"]
            status_label = tk.Label(
                row, text=status_text, width=8,
                bg=colors["bg"], fg=status_color
            )
            status_label.pack(side="left", padx=(4, 4))

            action_text = "Disable" if mon.is_enabled else "Enable"
            locked = self._monitor_op_in_flight or self._active_pending_instance_id is not None
            action_btn = tk.Button(
                row, text=action_text, width=8,
                bg=colors["btn_bg"], fg=colors["btn_fg"],
                activebackground=colors["btn_active"], activeforeground=colors["btn_fg"],
                state=tk.DISABLED if locked else tk.NORMAL,
                command=lambda m=mon: self._on_monitor_action_click(m)
            )
            action_btn.pack(side="left")

            self.monitor_rows[mon.instance_id] = row

    def _on_monitor_action_click(self, monitor):
        # Defensive: the row button is disabled while a op is in flight or a
        # disable is awaiting confirmation, but guard here too in case a click
        # slips in before the UI catches up (e.g. a queued event).
        if self._monitor_op_in_flight or self._active_pending_instance_id is not None:
            return

        colors = THEMES[self.theme_name]
        instance_id = monitor.instance_id
        friendly_name = monitor.friendly_name

        if monitor.is_enabled:
            # Refuse to disable the only currently-enabled monitor: QuickRes
            # is for turning off the *extra* monitor(s), not the one the
            # user is actually looking at. Without this, disabling it blacks
            # out the screen, and if the elevated call also happens to time
            # out, there's no visible UI left to recover from.
            other_enabled = [
                m for m in monitors_mod.enumerate_monitors()
                if m.is_enabled and m.instance_id != instance_id
            ]
            if not other_enabled:
                self.status_label.config(fg=colors["status_err"])
                self.status_var.set(
                    f"Refusing to disable {friendly_name} — it's the only "
                    f"enabled monitor. QuickRes is for disabling the extra "
                    f"monitor(s), not your only display."
                )
                return
            # Disabling is the risky direction: write the crash-recovery flag
            # before we ever hand off to the elevated helper. If we can't
            # persist that flag, the whole safety net is void, so refuse to
            # disable rather than proceed unprotected.
            if not save_pending_restore({
                "instance_id": instance_id,
                "friendly_name": friendly_name,
                "action": "disable",
                "started_at": time.time(),
            }):
                self.status_label.config(fg=colors["status_err"])
                self.status_var.set(
                    f"Could not write the crash-recovery flag — refusing to disable "
                    f"{friendly_name}. Check disk space/permissions and try again."
                )
                return
            self.status_var.set(f"Requesting admin approval to disable {friendly_name}...")
            self.status_label.config(fg=colors["fg"])
            self._monitor_op_in_flight = True
            self._refresh_monitor_list()
            self._run_threaded_monitor_op(
                monitors_mod.disable_monitor, instance_id,
                lambda ok, message: self._on_disable_complete(ok, message, monitor)
            )
        else:
            self.status_var.set(f"Requesting admin approval to enable {friendly_name}...")
            self.status_label.config(fg=colors["fg"])
            self._monitor_op_in_flight = True
            self._refresh_monitor_list()
            self._run_threaded_monitor_op(
                monitors_mod.enable_monitor, instance_id,
                lambda ok, message: self._on_enable_complete(ok, message, monitor)
            )

    def _find_monitor(self, instance_id):
        for mon in monitors_mod.enumerate_monitors():
            if mon.instance_id == instance_id:
                return mon
        return None

    def _on_disable_complete(self, ok, message, monitor):
        colors = THEMES[self.theme_name]
        self._monitor_op_in_flight = False
        if not ok and message == monitors_mod.TIMEOUT_MESSAGE:
            # Genuinely unknown outcome, not a confirmed failure — the
            # elevated process may still be waiting on a slow UAC prompt and
            # could disable the device moments later, off in the background.
            # The recovery flag was already written before we started, so
            # leave it exactly as-is (don't clear it, don't guess a verdict
            # from an immediate re-check that would just see "not yet").
            #
            # Keep the lock on too: without this, _active_pending_instance_id
            # stays None, _on_monitor_action_click's guard stops blocking,
            # and the user could start disabling a SECOND monitor — whose
            # save_pending_restore() call would overwrite this monitor's
            # still-possibly-completing entry in the single-record
            # pending_restore.json, silently losing its recovery flag.
            self._active_pending_instance_id = monitor.instance_id
            self.status_label.config(fg=colors["fg"])
            self.status_var.set(
                f"Still waiting on admin approval to disable {monitor.friendly_name} — "
                f"reopen Monitors in a moment to see the real state."
            )
            self._refresh_monitor_list()
            return
        if not ok:
            # A reported failure (its result file never got written) does
            # NOT prove the disable didn't happen — the elevated process may
            # have succeeded and just failed to report back. Re-check the
            # real device state before ever clearing the crash-recovery flag
            # on a false negative, which would otherwise strand a genuinely
            # disabled monitor with no recovery flag and no restore banner.
            actual = self._find_monitor(monitor.instance_id)
            if actual is not None and not actual.is_enabled:
                ok = True
                message = f"{monitor.friendly_name} disabled (result was unconfirmed, verified by re-check)"
        if ok:
            # Stays locked (via _active_pending_instance_id) until the user
            # confirms or the 10s auto-revert resolves it — only one risky
            # pending action can be tracked at a time (pending_restore.json
            # holds a single record), so no other monitor can be touched
            # while this one is unresolved.
            self._active_pending_instance_id = monitor.instance_id
            self.status_label.config(fg=colors["status_ok"])
            self.status_var.set(message)
            self._refresh_monitor_list()
            self._open_revert_dialog(monitor.instance_id, monitor.friendly_name)
        else:
            clear_pending_restore()
            self.status_label.config(fg=colors["status_err"])
            self.status_var.set(message)
            self._refresh_monitor_list()

    def _on_enable_complete(self, ok, message, monitor):
        colors = THEMES[self.theme_name]
        self._monitor_op_in_flight = False
        if not ok and message == monitors_mod.TIMEOUT_MESSAGE:
            # Same "genuinely unknown, not a confirmed failure" case as the
            # other three completion handlers — don't render it as a plain
            # failure (wrong color, misleading text). This direction never
            # sets the pending-restore lock, so there's nothing else to do.
            self.status_label.config(fg=colors["fg"])
            self.status_var.set(
                f"Still waiting on admin approval to enable {monitor.friendly_name} — "
                f"check again in a moment."
            )
            self._refresh_monitor_list()
            return
        if not ok:
            # Same false-negative risk as the other three completion
            # handlers, mirrored here for message accuracy: a reported
            # failure doesn't prove the enable didn't actually happen.
            actual = self._find_monitor(monitor.instance_id)
            if actual is not None and actual.is_enabled:
                ok = True
                message = f"{monitor.friendly_name} enabled (result was unconfirmed, verified by re-check)"
        self.status_label.config(fg=colors["status_ok"] if ok else colors["status_err"])
        self.status_var.set(message)
        self._refresh_monitor_list()

    def _open_revert_dialog(self, instance_id, friendly_name):
        colors = THEMES[self.theme_name]

        win = tk.Toplevel(self)
        win.title("Keep this monitor disabled?")
        win.resizable(False, False)
        win.transient(self)
        win.configure(bg=colors["bg"])
        self.revert_win = win
        # Dismissing via the titlebar X isn't a decision to "keep disabled" —
        # treat it the same as clicking "Revert now" (the safe default),
        # instead of leaving revert_win pointing at a destroyed widget while
        # the countdown keeps running invisibly with no dialog left to act on.
        win.protocol("WM_DELETE_WINDOW", lambda: self._revert_now(win, instance_id, friendly_name))

        dw, dh = 300, 160
        win.geometry(f"{dw}x{dh}")
        win.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() // 2) - (dw // 2)
        y = self.winfo_y() + (self.winfo_height() // 2) - (dh // 2)
        win.geometry(f"{dw}x{dh}+{x}+{y}")

        countdown_var = tk.StringVar(value=f"Reverting in {10}s")
        countdown_label = tk.Label(
            win, textvariable=countdown_var, wraplength=260,
            bg=colors["bg"], fg=colors["fg"]
        )
        countdown_label.pack(padx=16, pady=(16, 10))

        remaining = {"seconds": 10}

        def tick():
            if self.revert_win is not win or not win.winfo_exists():
                return
            remaining["seconds"] -= 1
            countdown_var.set(f"Reverting in {remaining['seconds']}s")
            if remaining["seconds"] > 0:
                self.after(1000, tick)

        self.after(1000, tick)

        guard = PendingDisableGuard(
            revert_callback=lambda: self._start_revert(instance_id, friendly_name),
            schedule_fn=lambda seconds, cb: self.after(int(seconds * 1000), cb),
            cancel_fn=lambda handle: self.after_cancel(handle),
            timeout_seconds=10,
        )
        self.pending_guard = guard
        guard.start()

        btn_frame = tk.Frame(win, bg=colors["bg"])
        btn_frame.pack(pady=(0, 10))

        btn_style = dict(
            bg=colors["btn_bg"], fg=colors["btn_fg"],
            activebackground=colors["btn_active"], activeforeground=colors["btn_fg"]
        )

        tk.Button(
            btn_frame, text="Keep disabled", width=13,
            command=lambda: self._confirm_keep_disabled(win),
            **btn_style
        ).pack(side="left", padx=4)

        tk.Button(
            btn_frame, text="Revert now", width=13,
            command=lambda: self._revert_now(win, instance_id, friendly_name),
            **btn_style
        ).pack(side="left", padx=4)

    def _confirm_keep_disabled(self, win):
        if self.pending_guard is not None:
            self.pending_guard.confirm()
            self.pending_guard = None
        clear_pending_restore()
        self._active_pending_instance_id = None
        self._close_revert_dialog(win)
        self._refresh_monitor_list()

    def _revert_now(self, win, instance_id, friendly_name):
        if self.pending_guard is not None:
            # Stop the auto-revert timer so it doesn't fire a second time;
            # the manual click below performs the same revert path instead.
            self.pending_guard.confirm()
            self.pending_guard = None
        self._close_revert_dialog(win)
        self._start_revert(instance_id, friendly_name)

    def _close_revert_dialog(self, win):
        if self.revert_win is win:
            self.revert_win = None
        if win.winfo_exists():
            win.destroy()

    def _start_revert(self, instance_id, friendly_name):
        # The auto-revert timeout calls this directly (as PendingDisableGuard's
        # revert_callback) without going through _revert_now, so the confirm
        # dialog would otherwise stay on screen showing stale "Keep
        # disabled"/"Revert now" choices after the monitor's already been
        # re-enabled. Close it here too — idempotent when _revert_now already
        # closed it.
        if self.revert_win is not None:
            self._close_revert_dialog(self.revert_win)

        colors = THEMES[self.theme_name]
        self.status_label.config(fg=colors["fg"])
        self.status_var.set(f"Reverting {friendly_name}...")
        self._monitor_op_in_flight = True
        self._run_threaded_monitor_op(
            monitors_mod.enable_monitor, instance_id,
            lambda ok, message: self._on_revert_complete(ok, message, friendly_name, instance_id)
        )

    def _on_revert_complete(self, ok, message, friendly_name, instance_id):
        colors = THEMES[self.theme_name]
        self._monitor_op_in_flight = False
        if not ok and message == monitors_mod.TIMEOUT_MESSAGE:
            # Genuinely unknown outcome, not a confirmed failure — leave the
            # recovery flag and lock exactly as they are and let a later
            # check (reopening Monitors, or the startup banner) resolve it.
            self.status_label.config(fg=colors["fg"])
            self.status_var.set(
                f"Still waiting on admin approval to revert {friendly_name} — "
                f"check again in a moment."
            )
            if self.monitors_win is not None and self.monitors_win.winfo_exists():
                self._refresh_monitor_list()
            return
        if not ok and message.startswith(monitors_mod.DEVICE_NOT_FOUND_PREFIX):
            # The device is gone (unplugged/replaced) — nothing left to
            # protect, so the flag is stale. Clear it instead of leaving the
            # user stuck retrying a revert that can never succeed.
            clear_pending_restore()
            self._active_pending_instance_id = None
            self._hide_restore_banner()
            self.status_label.config(fg=colors["status_err"])
            self.status_var.set(
                f"{friendly_name} is no longer present on this system — cleared the stale recovery flag."
            )
            if self.monitors_win is not None and self.monitors_win.winfo_exists():
                self._refresh_monitor_list()
            return
        status_text = f"Reverted {friendly_name}"
        if not ok:
            # Same false-negative risk as the disable path, mirrored: a
            # reported failure doesn't prove the re-enable didn't actually
            # happen — don't leave the monitor locked/the recovery flag set
            # over a stale/unconfirmed result if it's really back on.
            actual = self._find_monitor(instance_id)
            if actual is not None and actual.is_enabled:
                ok = True
                status_text = f"Reverted {friendly_name} (result was unconfirmed, verified by re-check)"
        if ok:
            clear_pending_restore()
            self._active_pending_instance_id = None
            self.status_label.config(fg=colors["status_ok"])
            self.status_var.set(status_text)
        else:
            # Leave the pending-restore flag AND the lock in place — the
            # monitor is still in an unresolved, potentially-disabled state,
            # so don't let the user start touching other monitors until this
            # is resolved (via the startup/banner retry path).
            self.status_label.config(fg=colors["status_err"])
            self.status_var.set(
                f"Failed to revert {friendly_name}: {message}. Please retry manually."
            )
            self._show_restore_banner()
        if self.monitors_win is not None and self.monitors_win.winfo_exists():
            self._refresh_monitor_list()