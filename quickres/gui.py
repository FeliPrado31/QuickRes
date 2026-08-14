import re
import webbrowser
import tkinter as tk
from tkinter import ttk

from quickres.config import resource_path, load_config, update_config
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

        self._rebuild_custom_section()
        self._sync_hotkey_dropdown_values()
        self.apply_theme()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
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