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
from quickres import __version__
from quickres import i18n
from quickres.i18n import t

QUICKRES_WEBSITE = "https://quickres.online/"

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

FAQ_KEYS = [
    ("faq_q1", "faq_a1"),
    ("faq_q2", "faq_a2"),
    ("faq_q3", "faq_a3"),
    ("faq_q4", "faq_a4"),
]


def _faq_items():
    return [(t(q_key), t(a_key)) for q_key, a_key in FAQ_KEYS]


MAX_CUSTOM_RES = 6


class ResSwitcherApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.resizable(False, False)

        try:
            self.iconbitmap(resource_path("icon.ico"))
        except Exception:
            pass

        cfg = load_config()

        self.language_setting = cfg.get("language", "auto")
        i18n.set_language(i18n.resolve_language(self.language_setting))

        self.title(t("app_title"))

        self.theme_name = cfg.get("theme") if cfg.get("theme") in THEMES else "light"
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

        self.settings_win = None
        self.settings_themed_widgets = []
        self.theme_btn = None

        self.pending_guard = None
        self.revert_win = None
        self._monitor_op_in_flight = False
        self._active_pending_instance_id = None
        self.restore_banner = None

        title_row = tk.Frame(self)
        title_row.pack(pady=(12, 8))
        self.themed_frames.append(title_row)

        title_label = tk.Label(
            title_row, text=t("app_title"),
            font=("Segoe UI", 12, "bold", "underline"),
            cursor="hand2"
        )
        title_label.pack(side="left")
        title_label.bind("<Button-1>", lambda e: webbrowser.open(QUICKRES_WEBSITE))
        self.title_label = title_label

        version_label = tk.Label(
            title_row, text=f"v{__version__}",
            font=("Segoe UI", 8),
            cursor="hand2"
        )
        version_label.pack(side="left", padx=(5, 0), anchor="s", pady=(0, 2))
        version_label.bind("<Button-1>", lambda e: webbrowser.open(QUICKRES_WEBSITE))
        self.themed_labels.append(version_label)
        self.version_label = version_label

        self.supported_resolutions = get_supported_resolutions()
        self.gpu_vendors = None

        scroll_outer = tk.Frame(self)
        scroll_outer.pack(fill="both", expand=True)
        self.themed_frames.append(scroll_outer)

        self.scroll_canvas = tk.Canvas(scroll_outer, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(scroll_outer, orient="vertical", command=self.scroll_canvas.yview)
        self.scroll_canvas.configure(yscrollcommand=scrollbar.set)
        self.scroll_canvas.pack(side="left", fill="both", expand=True)
        self._scrollbar = scrollbar

        scroll_content = tk.Frame(self.scroll_canvas)
        self.themed_frames.append(scroll_content)
        self.scroll_content = scroll_content
        self._scroll_window_id = self.scroll_canvas.create_window((0, 0), window=scroll_content, anchor="nw")

        def _on_scroll_content_configure(event):
            self.scroll_canvas.configure(scrollregion=self.scroll_canvas.bbox("all"))
        scroll_content.bind("<Configure>", _on_scroll_content_configure)

        def _on_canvas_configure(event):
            self.scroll_canvas.itemconfig(self._scroll_window_id, width=event.width)
        self.scroll_canvas.bind("<Configure>", _on_canvas_configure)

        def _on_mousewheel(event):
            self.scroll_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.scroll_canvas.bind("<Enter>", lambda e: self.scroll_canvas.bind_all("<MouseWheel>", _on_mousewheel))
        self.scroll_canvas.bind("<Leave>", lambda e: self.scroll_canvas.unbind_all("<MouseWheel>"))

        grid_frame = tk.Frame(scroll_content)
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

        self.custom_label = tk.Label(scroll_content, text=t("custom_resolution_label"), font=("Segoe UI", 9, "bold"))
        self.custom_label.pack(pady=(4, 4))
        self.themed_labels.append(self.custom_label)

        custom_row = tk.Frame(scroll_content)
        custom_row.pack(pady=(0, 6))
        self.themed_frames.append(custom_row)
        self.custom_entry = tk.Entry(custom_row, width=14)
        self.custom_entry.insert(0, t("custom_res_placeholder"))
        self.custom_entry.bind("<FocusIn>", self._clear_placeholder)
        self.custom_entry.bind("<Return>", lambda e: self.apply_custom())
        self.custom_entry.pack(side="left")
        self.themed_entries.append(self.custom_entry)
        custom_btn = tk.Button(custom_row, text=t("btn_apply"), command=self.apply_custom)
        custom_btn.pack(side="left", padx=(6, 0))
        self.themed_buttons.append(custom_btn)

        self.custom_grid = tk.Frame(scroll_content)
        self.custom_grid.pack(pady=(0, 4))
        self.themed_frames.append(self.custom_grid)
        self.custom_buttons = []

        footer_frame = tk.Frame(self)
        footer_frame.pack(side="bottom", fill="x")
        self.themed_frames.append(footer_frame)

        self.separator = ttk.Separator(footer_frame, orient="horizontal")
        self.separator.pack(fill="x", pady=10, padx=10)

        hotkey_title = tk.Label(footer_frame, text=t("hotkey_toggle_label"), font=("Segoe UI", 9, "bold"))
        hotkey_title.pack(anchor="w", padx=10)
        self.themed_labels.append(hotkey_title)

        hotkey_row = tk.Frame(footer_frame)
        hotkey_row.pack(fill="x", padx=10, pady=(4, 0))
        self.themed_frames.append(hotkey_row)
        hotkey_label = tk.Label(hotkey_row, text=t("hotkey_label"))
        hotkey_label.pack(side="left")
        self.themed_labels.append(hotkey_label)
        saved_hotkey = cfg.get("hotkey", "F6")
        self.hotkey_var = tk.StringVar(value=saved_hotkey if saved_hotkey in HOTKEY_OPTIONS else "F6")
        self.hotkey_key_combo = ttk.Combobox(
            hotkey_row, textvariable=self.hotkey_var, values=list(HOTKEY_OPTIONS.keys()),
            width=8, state="readonly"
        )
        self.hotkey_key_combo.pack(side="left", padx=(6, 0))
        self.hotkey_key_combo.bind("<<ComboboxSelected>>", self._on_hotkey_settings_changed)

        self.hotkey_btn = tk.Button(hotkey_row, text=t("btn_start_hotkey"), command=self.toggle_hotkey_mode)
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

        native_row = tk.Frame(footer_frame)
        native_row.pack(fill="x", padx=10, pady=(6, 0))
        self.themed_frames.append(native_row)
        native_label = tk.Label(native_row, text=t("native_label"))
        native_label.pack(side="left")
        self.themed_labels.append(native_label)
        saved_native = cfg.get("native_res", native_str)
        self.native_var = tk.StringVar(value=saved_native if saved_native in native_options else native_str)
        self.native_combo = ttk.Combobox(
            native_row, textvariable=self.native_var, values=native_options,
            width=11, state="normal"
        )
        self.native_combo.pack(side="left", padx=(6, 0))
        self.native_combo.bind("<<ComboboxSelected>>", self._on_hotkey_settings_changed)
        self.native_combo.bind("<Return>", self._on_hotkey_settings_changed)
        self.native_combo.bind("<FocusOut>", self._on_hotkey_settings_changed)

        stretched_row = tk.Frame(footer_frame)
        stretched_row.pack(fill="x", padx=10, pady=(6, 0))
        self.themed_frames.append(stretched_row)
        stretched_label = tk.Label(stretched_row, text=t("stretched_label"))
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
        self.stretched_combo.bind("<<ComboboxSelected>>", self._on_hotkey_settings_changed)
        self.stretched_combo.bind("<Return>", self._on_hotkey_settings_changed)
        self.stretched_combo.bind("<FocusOut>", self._on_hotkey_settings_changed)

        self.status_var = tk.StringVar(value="")
        self.status_label = tk.Label(footer_frame, textvariable=self.status_var, wraplength=250, fg="green")
        self.status_label.pack(pady=(14, 0), padx=10)
        self.status_var.trace_add("write", lambda *args: self._fit_window_to_content())

        self.restore_banner = tk.Button(
            footer_frame, text=t("restore_banner_text"),
            wraplength=280, justify="left", relief="flat",
            command=self._restore_pending_from_banner
        )

        bottom_row = tk.Frame(footer_frame)
        bottom_row.pack(side="bottom", pady=(0, 14))
        self.themed_frames.append(bottom_row)

        faq_btn = tk.Button(bottom_row, text=t("btn_faq"), command=self.show_faq)
        faq_btn.pack(side="left", padx=4)
        self.themed_buttons.append(faq_btn)

        monitors_btn = tk.Button(bottom_row, text=t("btn_monitors"), command=self.show_monitors)
        monitors_btn.pack(side="left", padx=4)
        self.themed_buttons.append(monitors_btn)

        settings_btn = tk.Button(bottom_row, text=t("btn_settings"), command=self.show_settings)
        settings_btn.pack(side="left", padx=4)
        self.themed_buttons.append(settings_btn)

        github_btn = tk.Button(
            bottom_row, text=t("btn_github"),
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
        self._fit_window_to_content()

    def _fit_window_to_content(self):
        self.update_idletasks()
        self.scroll_content.update_idletasks()
        content_h = self.scroll_content.winfo_reqheight()
        self.scroll_canvas.configure(height=content_h)
        self.update_idletasks()

        total_w = self.winfo_reqwidth()
        total_h = self.winfo_reqheight()

        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        margin = 80  

        win_w = min(total_w, screen_w - 40)
        win_h = min(total_h, screen_h - margin)

        if win_h < total_h:
            overflow = total_h - win_h
            self.scroll_canvas.configure(height=max(content_h - overflow, 80))

        x = (screen_w // 2) - (win_w // 2)
        y = (screen_h // 2) - (win_h // 2)
        self.geometry(f"{win_w}x{win_h}+{x}+{y}")

    def toggle_theme(self):
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        update_config({"theme": self.theme_name})
        self.apply_theme()

    def apply_theme(self):
        colors = THEMES[self.theme_name]

        self.configure(bg=colors["bg"])
        self.scroll_canvas.configure(bg=colors["canvas_bg"])

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

        if self.theme_btn is not None and self.theme_btn.winfo_exists():
            self.theme_btn.configure(text=t("theme_light") if self.theme_name == "dark" else t("theme_dark"))

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

        if self.settings_win is not None and self.settings_win.winfo_exists():
            self.settings_win.configure(bg=colors["bg"])
            for widget in self.settings_themed_widgets:
                if not widget.winfo_exists():
                    continue
                kind = widget.winfo_class()
                if kind == "Label":
                    widget.configure(bg=colors["bg"], fg=colors["fg"])
                elif kind == "Frame":
                    widget.configure(bg=colors["bg"])
                elif kind == "Button":
                    widget.configure(
                        bg=colors["btn_bg"], fg=colors["btn_fg"],
                        activebackground=colors["btn_active"], activeforeground=colors["btn_fg"]
                    )
            if self.theme_btn is not None and self.theme_btn.winfo_exists():
                self.theme_btn.configure(text=t("theme_light") if self.theme_name == "dark" else t("theme_dark"))

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

            self._refresh_monitor_list()

        if self.revert_win is not None and self.revert_win.winfo_exists():
            self.revert_win.configure(bg=colors["bg"])
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
            "Vertical.TScrollbar",
            background=colors["btn_bg"],
            troughcolor=colors["bg"],
            arrowcolor=colors["fg"],
        )
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
        faq_win.title(t("faq_window_title"))
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

        for i, (question, answer) in enumerate(_faq_items()):
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
            faq_win, text=t("btn_close"), command=lambda: self._close_faq(faq_win),
            bg=colors["btn_bg"], fg=colors["btn_fg"],
            activebackground=colors["btn_active"], activeforeground=colors["btn_fg"]
        )
        close_btn.pack(side="bottom", pady=(0, 14))
        self.faq_themed_widgets.append(close_btn)

    def _close_faq(self, faq_win):
        self.faq_win = None
        self.faq_themed_widgets = []
        faq_win.destroy()

    def show_settings(self):
        if self.settings_win is not None and self.settings_win.winfo_exists():
            self.settings_win.lift()
            self.settings_win.focus_force()
            return

        colors = THEMES[self.theme_name]

        win = tk.Toplevel(self)
        win.title("Settings")
        win.resizable(False, False)
        win.configure(bg=colors["bg"])
        self.settings_win = win
        self.settings_themed_widgets = []
        win.protocol("WM_DELETE_WINDOW", lambda: self._close_settings(win))

        sw, sh = 260, 230
        win.geometry(f"{sw}x{sh}")
        win.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() // 2) - (sw // 2)
        y = self.winfo_y() + (self.winfo_height() // 2) - (sh // 2)
        win.geometry(f"{sw}x{sh}+{x}+{y}")

        theme_row = tk.Frame(win, bg=colors["bg"])
        theme_row.pack(fill="x", padx=16, pady=(18, 8))
        self.settings_themed_widgets.append(theme_row)

        theme_label = tk.Label(theme_row, text=t("settings_theme_label"), bg=colors["bg"], fg=colors["fg"])
        theme_label.pack(side="left")
        self.settings_themed_widgets.append(theme_label)

        self.theme_btn = tk.Button(
            theme_row, text=t("theme_light") if self.theme_name == "dark" else t("theme_dark"),
            width=6, command=self.toggle_theme,
            bg=colors["btn_bg"], fg=colors["btn_fg"],
            activebackground=colors["btn_active"], activeforeground=colors["btn_fg"]
        )
        self.theme_btn.pack(side="left", padx=(8, 0))
        self.settings_themed_widgets.append(self.theme_btn)

        language_row = tk.Frame(win, bg=colors["bg"])
        language_row.pack(fill="x", padx=16, pady=(0, 8))
        self.settings_themed_widgets.append(language_row)

        language_label = tk.Label(language_row, text=t("settings_language_label"), bg=colors["bg"], fg=colors["fg"])
        language_label.pack(side="left")
        self.settings_themed_widgets.append(language_label)

        lang_codes = list(i18n.LANGUAGE_NAMES.keys())
        self.language_var = tk.StringVar(value=i18n.LANGUAGE_NAMES.get(self.language_setting, "Auto"))
        language_combo = ttk.Combobox(
            language_row, textvariable=self.language_var,
            values=[i18n.LANGUAGE_NAMES[code] for code in lang_codes],
            width=10, state="readonly"
        )
        language_combo.pack(side="left", padx=(8, 0))
        language_combo.bind("<<ComboboxSelected>>", self._on_language_selected)

        reset_btn = tk.Button(
            win, text=t("btn_reset_custom_res"),
            command=self._reset_custom_resolutions,
            bg=colors["btn_bg"], fg=colors["btn_fg"],
            activebackground=colors["btn_active"], activeforeground=colors["btn_fg"]
        )
        reset_btn.pack(padx=16, pady=(4, 8), fill="x")
        self.settings_themed_widgets.append(reset_btn)

        check_btn = tk.Button(
            win, text=t("btn_check_update"),
            command=lambda: check_for_update(manual=True),
            bg=colors["btn_bg"], fg=colors["btn_fg"],
            activebackground=colors["btn_active"], activeforeground=colors["btn_fg"]
        )
        check_btn.pack(padx=16, pady=(0, 8), fill="x")
        self.settings_themed_widgets.append(check_btn)

        close_btn = tk.Button(
            win, text=t("btn_close"), command=lambda: self._close_settings(win),
            bg=colors["btn_bg"], fg=colors["btn_fg"],
            activebackground=colors["btn_active"], activeforeground=colors["btn_fg"]
        )
        close_btn.pack(side="bottom", pady=(0, 14))
        self.settings_themed_widgets.append(close_btn)

    def _on_language_selected(self, event):
        name_to_code = {v: k for k, v in i18n.LANGUAGE_NAMES.items()}
        selected_code = name_to_code.get(self.language_var.get(), "auto")
        self.language_setting = selected_code
        update_config({"language": selected_code})
        i18n.set_language(i18n.resolve_language(selected_code))
        self._retranslate_ui()

    def _retranslate_ui(self):
        # Static text needs a full rebuild to pick up the new language.
        # Simplest reliable approach: relaunch the main window.
        if self.settings_win is not None and self.settings_win.winfo_exists():
            self._close_settings(self.settings_win)
        self.destroy()
        new_app = ResSwitcherApp()
        new_app.mainloop()

    def _close_settings(self, win):
        self.settings_win = None
        self.settings_themed_widgets = []
        self.theme_btn = None
        win.destroy()

    def _reset_custom_resolutions(self):
        update_config({"custom_resolutions": []})
        self._rebuild_custom_section()
        self._sync_hotkey_dropdown_values()
        self.status_label.config(fg=THEMES[self.theme_name]["status_ok"])
        self.status_var.set(t("status_custom_cleared"))

    def _clear_placeholder(self, event):
        if self.custom_entry.get() == t("custom_res_placeholder"):
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
            self.hotkey_btn.config(text=t("btn_start_hotkey"))
            self.status_var.set(t("status_hotkey_stopped"))
            return

        native = self._parse_res(self.native_var.get())
        stretched = self._parse_res(self.stretched_var.get())
        if not native or not stretched:
            self.status_label.config(fg=THEMES[self.theme_name]["status_err"])
            self.status_var.set(t("status_res_format_error"))
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
        self.hotkey_btn.config(text=t("btn_stop_hotkey"))

    def _on_hotkey_settings_changed(self, event=None):
        if not self.hotkey_running or self.hotkey_toggle is None:
            return

        native = self._parse_res(self.native_var.get())
        stretched = self._parse_res(self.stretched_var.get())
        if not native or not stretched:
            return

        key_name = self.hotkey_var.get()
        if (
            native == self.hotkey_toggle.native_res
            and stretched == self.hotkey_toggle.stretched_res
            and key_name == self.hotkey_toggle.key_name
        ):
            return

        update_config({
            "hotkey": key_name,
            "native_res": self.native_var.get(),
            "stretched_res": self.stretched_var.get()
        })

        was_stretched = self.hotkey_toggle.is_stretched
        self.hotkey_toggle.stop()

        self.hotkey_toggle = HotkeyToggle(
            key_name=key_name,
            native_res=native,
            stretched_res=stretched,
            on_status=self._set_status_threadsafe,
        )
        self.hotkey_toggle.is_stretched = was_stretched
        self.hotkey_toggle.start()

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
        dialog.title(t("dialog_res_not_found_title"))
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
            text=t("dialog_res_not_found_body", width=width, height=height),
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
                btn_frame, text=t("btn_nvidia_panel"), width=22,
                command=lambda: (open_nvidia_control_panel(), dialog.destroy()),
                **btn_style
            ).pack(pady=3)
        if "amd" in vendors_to_show:
            tk.Button(
                btn_frame, text=t("btn_amd_software"), width=22,
                command=lambda: (open_amd_software(), dialog.destroy()),
                **btn_style
            ).pack(pady=3)
        if "intel" in vendors_to_show:
            tk.Button(
                btn_frame, text=t("btn_intel_graphics"), width=22,
                command=lambda: (open_intel_graphics_software(), dialog.destroy()),
                **btn_style
            ).pack(pady=3)

        tk.Button(dialog, text=t("btn_cancel"), command=dialog.destroy, **btn_style).pack(pady=(0, 12))

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
            self.status_var.set(t("status_custom_format_error"))
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

        self._fit_window_to_content()

    def _remove_custom(self, res_str):
        cfg = load_config()
        customs = cfg.get("custom_resolutions", [])
        if res_str in customs:
            customs.remove(res_str)
            update_config({"custom_resolutions": customs})
        self._rebuild_custom_section()
        self._sync_hotkey_dropdown_values()
        self.status_label.config(fg=THEMES[self.theme_name]["status_ok"])
        self.status_var.set(t("status_removed_custom", res=res_str))

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


    def _check_pending_restore_on_startup(self):
        pending = load_pending_restore()
        if pending is None:
            return
        instance_id = pending.get("instance_id")
        if not instance_id:

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
        self.status_var.set(t("status_restoring", name=friendly_name))
        self._monitor_op_in_flight = True
        self._run_threaded_monitor_op(
            monitors_mod.enable_monitor, instance_id,
            lambda ok, message: self._on_restore_complete(ok, message, friendly_name, instance_id)
        )

    def _on_restore_complete(self, ok, message, friendly_name, instance_id):
        colors = THEMES[self.theme_name]
        self._monitor_op_in_flight = False
        if not ok and message == monitors_mod.TIMEOUT_MESSAGE:
            self.status_label.config(fg=colors["fg"])
            self.status_var.set(t("status_restore_pending", name=friendly_name))
            if self.monitors_win is not None and self.monitors_win.winfo_exists():
                self._refresh_monitor_list()
            return
        if not ok and message.startswith(monitors_mod.DEVICE_NOT_FOUND_PREFIX):
            clear_pending_restore()
            self._active_pending_instance_id = None
            self._hide_restore_banner()
            self.status_label.config(fg=colors["status_err"])
            self.status_var.set(t("status_device_gone", name=friendly_name))
            if self.monitors_win is not None and self.monitors_win.winfo_exists():
                self._refresh_monitor_list()
            return
        status_text = t("status_restored", name=friendly_name)
        if not ok:
            actual = self._find_monitor(instance_id)
            if actual is not None and actual.is_enabled:
                ok = True
                status_text = t("status_restored_unconfirmed", name=friendly_name)
        if ok:
            clear_pending_restore()
            self._active_pending_instance_id = None
            self.status_label.config(fg=colors["status_ok"])
            self.status_var.set(status_text)
        else:
            self._show_restore_banner()
            self.status_label.config(fg=colors["status_err"])
            self.status_var.set(t("status_restore_failed", name=friendly_name, message=message))
        if self.monitors_win is not None and self.monitors_win.winfo_exists():
            self._refresh_monitor_list()

    def _run_threaded_monitor_op(self, op_func, instance_id, on_done):
        def worker():
            try:
                ok, message = op_func(instance_id)
            except Exception as e:
                ok, message = False, t("status_unexpected_error", error=e)
            try:
                self.after(0, lambda: on_done(ok, message))
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    def show_monitors(self):
        if self.monitors_win is not None and self.monitors_win.winfo_exists():
            self.monitors_win.lift()
            self.monitors_win.focus_force()
            return

        colors = THEMES[self.theme_name]

        win = tk.Toplevel(self)
        win.title(t("monitors_window_title"))
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
            win, text=t("btn_close"), command=lambda: self._close_monitors(win),
            bg=colors["btn_bg"], fg=colors["btn_fg"],
            activebackground=colors["btn_active"], activeforeground=colors["btn_fg"]
        )
        close_btn.pack(side="bottom", pady=(0, 14))
        self.monitors_themed_widgets.append(close_btn)

        self._reconcile_stuck_pending()
        self._refresh_monitor_list()

    def _reconcile_stuck_pending(self):
        if self._active_pending_instance_id is None:
            return
        if self.revert_win is not None and self.revert_win.winfo_exists():
            return  
        if self._monitor_op_in_flight:
            return 

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
            return 
        if actual.is_enabled:
            clear_pending_restore()
            self._active_pending_instance_id = None
        else:
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
                self.monitors_list_frame, text=t("monitors_none_found"),
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

            status_text = t("monitor_status_enabled") if mon.is_enabled else t("monitor_status_disabled")
            status_color = colors["status_ok"] if mon.is_enabled else colors["status_err"]
            status_label = tk.Label(
                row, text=status_text, width=8,
                bg=colors["bg"], fg=status_color
            )
            status_label.pack(side="left", padx=(4, 4))

            action_text = t("btn_disable") if mon.is_enabled else t("btn_enable")
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
        if self._monitor_op_in_flight or self._active_pending_instance_id is not None:
            return

        colors = THEMES[self.theme_name]
        instance_id = monitor.instance_id
        friendly_name = monitor.friendly_name

        if monitor.is_enabled:
            if not save_pending_restore({
                "instance_id": instance_id,
                "friendly_name": friendly_name,
                "action": "disable",
                "started_at": time.time(),
            }):
                self.status_label.config(fg=colors["status_err"])
                self.status_var.set(t("status_write_flag_failed", name=friendly_name))
                return
            self.status_var.set(t("status_requesting_disable", name=friendly_name))
            self.status_label.config(fg=colors["fg"])
            self._monitor_op_in_flight = True
            self._refresh_monitor_list()
            self._run_threaded_monitor_op(
                monitors_mod.disable_monitor, instance_id,
                lambda ok, message: self._on_disable_complete(ok, message, monitor)
            )
        else:
            self.status_var.set(t("status_requesting_enable", name=friendly_name))
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
            self._active_pending_instance_id = monitor.instance_id
            self.status_label.config(fg=colors["fg"])
            self.status_var.set(t("status_disable_pending", name=monitor.friendly_name))
            self._refresh_monitor_list()
            return
        if not ok:
            actual = self._find_monitor(monitor.instance_id)
            if actual is not None and not actual.is_enabled:
                ok = True
                message = t("status_disable_unconfirmed", name=monitor.friendly_name)
        if ok:
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
            self.status_label.config(fg=colors["fg"])
            self.status_var.set(t("status_enable_pending", name=monitor.friendly_name))
            self._refresh_monitor_list()
            return
        if not ok:
            actual = self._find_monitor(monitor.instance_id)
            if actual is not None and actual.is_enabled:
                ok = True
                message = t("status_enable_unconfirmed", name=monitor.friendly_name)
        self.status_label.config(fg=colors["status_ok"] if ok else colors["status_err"])
        self.status_var.set(message)
        self._refresh_monitor_list()

    def _open_revert_dialog(self, instance_id, friendly_name):
        colors = THEMES[self.theme_name]

        win = tk.Toplevel(self)
        win.title(t("revert_dialog_title"))
        win.resizable(False, False)
        win.transient(self)
        win.configure(bg=colors["bg"])
        self.revert_win = win
        win.protocol("WM_DELETE_WINDOW", lambda: self._revert_now(win, instance_id, friendly_name))

        dw, dh = 300, 160
        win.geometry(f"{dw}x{dh}")
        win.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() // 2) - (dw // 2)
        y = self.winfo_y() + (self.winfo_height() // 2) - (dh // 2)
        win.geometry(f"{dw}x{dh}+{x}+{y}")

        countdown_var = tk.StringVar(value=t("revert_countdown", seconds=10))
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
            countdown_var.set(t("revert_countdown", seconds=remaining["seconds"]))
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
            btn_frame, text=t("btn_keep_disabled"), width=13,
            command=lambda: self._confirm_keep_disabled(win),
            **btn_style
        ).pack(side="left", padx=4)

        tk.Button(
            btn_frame, text=t("btn_revert_now"), width=13,
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
        if self.revert_win is not None:
            self._close_revert_dialog(self.revert_win)

        colors = THEMES[self.theme_name]
        self.status_label.config(fg=colors["fg"])
        self.status_var.set(t("status_reverting", name=friendly_name))
        self._monitor_op_in_flight = True
        self._run_threaded_monitor_op(
            monitors_mod.enable_monitor, instance_id,
            lambda ok, message: self._on_revert_complete(ok, message, friendly_name, instance_id)
        )

    def _on_revert_complete(self, ok, message, friendly_name, instance_id):
        colors = THEMES[self.theme_name]
        self._monitor_op_in_flight = False
        if not ok and message == monitors_mod.TIMEOUT_MESSAGE:
            self.status_label.config(fg=colors["fg"])
            self.status_var.set(t("status_revert_pending", name=friendly_name))
            if self.monitors_win is not None and self.monitors_win.winfo_exists():
                self._refresh_monitor_list()
            return
        if not ok and message.startswith(monitors_mod.DEVICE_NOT_FOUND_PREFIX):
            clear_pending_restore()
            self._active_pending_instance_id = None
            self._hide_restore_banner()
            self.status_label.config(fg=colors["status_err"])
            self.status_var.set(t("status_device_gone", name=friendly_name))
            if self.monitors_win is not None and self.monitors_win.winfo_exists():
                self._refresh_monitor_list()
            return
        status_text = t("status_reverted", name=friendly_name)
        if not ok:
            actual = self._find_monitor(instance_id)
            if actual is not None and actual.is_enabled:
                ok = True
                status_text = t("status_reverted_unconfirmed", name=friendly_name)
        if ok:
            clear_pending_restore()
            self._active_pending_instance_id = None
            self.status_label.config(fg=colors["status_ok"])
            self.status_var.set(status_text)
        else:
            self.status_label.config(fg=colors["status_err"])
            self.status_var.set(t("status_revert_failed", name=friendly_name, message=message))
            self._show_restore_banner()
        if self.monitors_win is not None and self.monitors_win.winfo_exists():
            self._refresh_monitor_list()