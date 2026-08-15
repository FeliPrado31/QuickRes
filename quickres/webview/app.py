import os

import webview

from quickres.config import resource_path
from quickres.display import set_resolution
from quickres.webview.bridge import Api

PANEL_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "panel.html")


def run_app():
    api = Api()
    window = webview.create_window(
        "QuickRes",
        PANEL_HTML,
        js_api=api,
        width=410,
        height=530,
        resizable=False,
        background_color="#0d0f12",
    )
    api.bind_window(window)

    def on_closing():
        if api.hotkey_toggle:
            if api.hotkey_toggle.is_stretched:
                set_resolution(*api.hotkey_toggle.native_res)
            api.hotkey_toggle.stop()

    window.events.closing += on_closing

    icon_path = resource_path("icon.ico")
    start_kwargs = {"icon": icon_path} if os.path.exists(icon_path) else {}
    webview.start(**start_kwargs)
