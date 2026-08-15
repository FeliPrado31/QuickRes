"""
HOW THIS WORKS:
- Every user-facing string lives in STRINGS["en"], keyed by a short name.

- STRINGS["ru"] holds the Russian translation for the same key.

- Strings with dynamic content use {placeholders}, e.g. "Reverting {name}...".
  NEVER remove or rename anything inside { } when translating, only translate
  the surrounding text.

- If a key is missing from a non-English language, it silently falls back to
  English, so a partial translation never breaks the app.

FOR TRANSLATORS:
Only edit the STRINGS["ru"] block below. It has the same keys, in the same
order, as STRINGS["en"] above it, just scroll up to see the English text
for whatever key you're on. 

Leave STRINGS["en"] and everything else in
this file untouched. 

Keep any {word} placeholders exactly as they appear
in the English version (e.g. {name}, {seconds}), just move them to
wherever fits in the Russian sentence.
"""

import ctypes

LANG_RUSSIAN = 0x19

STRINGS = {
    "en": {
        # --- Main window ---
        "app_title": "QuickRes",
        "custom_resolution_label": "Custom Resolution",
        "custom_res_placeholder": "e.g. 1440x1080",
        "btn_apply": "Apply",
        "hotkey_toggle_label": "Hotkey Toggle",
        "hotkey_label": "Hotkey:",
        "btn_start_hotkey": "Start Hotkey",
        "btn_stop_hotkey": "Stop Hotkey",
        "native_label": "Native:",
        "stretched_label": "Stretched:",
        "btn_faq": "FAQ",
        "btn_monitors": "Monitors",
        "btn_settings": "Settings",
        "btn_github": "GitHub",
        "restore_banner_text": "A monitor may still be disabled from a previous session - click to restore it",

        # --- Status messages (main window) ---
        "status_custom_cleared": "Custom resolutions cleared",
        "status_hotkey_stopped": "Hotkey: stopped",
        "status_res_format_error": "Native/Stretched must look like 1920x1080",
        "status_custom_format_error": "Format like 1440x1080",
        "status_removed_custom": "Removed {res} from custom list",

        # --- Resolution-not-found dialog ---
        "dialog_res_not_found_title": "Resolution not found",
        "dialog_res_not_found_body": (
            "Couldn't detect {width} x {height} on your GPU driver.\n\n"
            "Would you like to open your graphics software to add it?"
        ),
        "btn_nvidia_panel": "NVIDIA Control Panel",
        "btn_amd_software": "AMD Software",
        "btn_intel_graphics": "Intel Graphics Software",
        "btn_cancel": "Cancel",

        # --- FAQ window ---
        "faq_window_title": "FAQ",
        "btn_close": "Close",
        "faq_q1": "Why does it say \"Resolution not found\"?",
        "faq_a1": (
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
        "faq_q2": "Nothing happens when I click a button?",
        "faq_a2": "Try running QuickRes as Administrator.",
        "faq_q3": "Screen looks wrong after switching? (valorant)",
        "faq_a3": (
            "Make sure Valorant is on the fill/agent select screen before you "
            "switch to a stretched res. You have to do this every match, since "
            "loading into a new game resets your resolution and switching too "
            "early will give you black bars. Set a hotkey below so you can "
            "toggle between native and stretched with one keypress instead of "
            "clicking through the app."
        ),
        "faq_q4": "What does the hotkey do?",
        "faq_a4": (
            "Once you press Start Hotkey, the key you picked toggles your "
            "display between the Native and Stretched resolutions set above. "
            "Press it once on the fill/agent select screen to go stretched, "
            "press it again after the match to go back to native.\n\n"
        ),

        # --- Settings window ---
        "settings_window_title": "Settings",
        "settings_theme_label": "Theme:",
        "theme_light": "Light",
        "theme_dark": "Dark",
        "btn_reset_custom_res": "Reset Custom Resolutions",
        "btn_check_update": "Check for Update",
        "settings_language_label": "Language:",

        # --- Monitors window ---
        "monitors_window_title": "Monitors",
        "monitors_none_found": "No monitors found.",
        "monitor_status_enabled": "Enabled",
        "monitor_status_disabled": "Disabled",
        "btn_disable": "Disable",
        "btn_enable": "Enable",
        "status_write_flag_failed": (
            "Could not write the crash-recovery flag — refusing to disable "
            "{name}. Check disk space/permissions and try again."
        ),
        "status_requesting_disable": "Requesting admin approval to disable {name}...",
        "status_requesting_enable": "Requesting admin approval to enable {name}...",
        "status_disable_pending": (
            "Still waiting on admin approval to disable {name} — "
            "reopen Monitors in a moment to see the real state."
        ),
        "status_disable_unconfirmed": "{name} disabled (result was unconfirmed, verified by re-check)",
        "status_enable_pending": (
            "Still waiting on admin approval to enable {name} — "
            "check again in a moment."
        ),
        "status_enable_unconfirmed": "{name} enabled (result was unconfirmed, verified by re-check)",

        # --- Revert / keep-disabled dialog ---
        "revert_dialog_title": "Keep this monitor disabled?",
        "revert_countdown": "Reverting in {seconds}s",
        "btn_keep_disabled": "Keep disabled",
        "btn_revert_now": "Revert now",
        "status_reverting": "Reverting {name}...",
        "status_revert_pending": (
            "Still waiting on admin approval to revert {name} — "
            "check again in a moment."
        ),
        "status_device_gone": "{name} is no longer present on this system — cleared the stale recovery flag.",
        "status_reverted": "Reverted {name}",
        "status_reverted_unconfirmed": "Reverted {name} (result was unconfirmed, verified by re-check)",
        "status_revert_failed": "Failed to revert {name}: {message}. Please retry manually.",

        # --- Restore-on-startup flow ---
        "status_restoring": "Restoring {name}...",
        "status_restore_pending": (
            "Still waiting on admin approval to restore {name} — "
            "check again in a moment."
        ),
        "status_restored": "Restored {name}",
        "status_restored_unconfirmed": "Restored {name} (result was unconfirmed, verified by re-check)",
        "status_restore_failed": "Failed to restore {name}: {message}. Please retry manually.",
        "status_unexpected_error": "Unexpected error: {error}",
    },

    "ru": {
        # --- Main window ---
        "app_title": "",
        "custom_resolution_label": "",
        "custom_res_placeholder": "",
        "btn_apply": "",
        "hotkey_toggle_label": "",
        "hotkey_label": "",
        "btn_start_hotkey": "",
        "btn_stop_hotkey": "",
        "native_label": "",
        "stretched_label": "",
        "btn_faq": "",
        "btn_monitors": "",
        "btn_settings": "",
        "btn_github": "",
        "restore_banner_text": "",

        # --- Status messages (main window) ---
        "status_custom_cleared": "",
        "status_hotkey_stopped": "",
        "status_res_format_error": "",
        "status_custom_format_error": "",
        "status_removed_custom": "",

        # --- Resolution-not-found dialog ---
        "dialog_res_not_found_title": "",
        "dialog_res_not_found_body": "",
        "btn_nvidia_panel": "",
        "btn_amd_software": "",
        "btn_intel_graphics": "",
        "btn_cancel": "",

        # --- FAQ window ---
        "faq_window_title": "",
        "btn_close": "",
        "faq_q1": "",
        "faq_a1": "",
        "faq_q2": "",
        "faq_a2": "",
        "faq_q3": "",
        "faq_a3": "",
        "faq_q4": "",
        "faq_a4": "",

        # --- Settings window ---
        "settings_window_title": "",
        "settings_theme_label": "",
        "theme_light": "",
        "theme_dark": "",
        "btn_reset_custom_res": "",
        "btn_check_update": "",
        "settings_language_label": "",

        # --- Monitors window ---
        "monitors_window_title": "",
        "monitors_none_found": "",
        "monitor_status_enabled": "",
        "monitor_status_disabled": "",
        "btn_disable": "",
        "btn_enable": "",
        "status_write_flag_failed": "",
        "status_requesting_disable": "",
        "status_requesting_enable": "",
        "status_disable_pending": "",
        "status_disable_unconfirmed": "",
        "status_enable_pending": "",
        "status_enable_unconfirmed": "",

        # --- Revert / keep-disabled dialog ---
        "revert_dialog_title": "",
        "revert_countdown": "",
        "btn_keep_disabled": "",
        "btn_revert_now": "",
        "status_reverting": "",
        "status_revert_pending": "",
        "status_device_gone": "",
        "status_reverted": "",
        "status_reverted_unconfirmed": "",
        "status_revert_failed": "",

        # --- Restore-on-startup flow ---
        "status_restoring": "",
        "status_restore_pending": "",
        "status_restored": "",
        "status_restored_unconfirmed": "",
        "status_restore_failed": "",
        "status_unexpected_error": "",
    },
}

LANGUAGE_NAMES = {
    "auto": "Auto",
    "en": "English",
    "ru": "Русский",
}

_current_lang = "en"


def detect_system_language() -> str:
    try:
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        primary = langid & 0x3FF
        if primary == LANG_RUSSIAN:
            return "ru"
    except Exception:
        pass
    return "en"


def resolve_language(setting: str) -> str:
    if setting == "auto" or setting not in STRINGS:
        return detect_system_language()
    return setting


def set_language(lang: str):
    global _current_lang
    _current_lang = lang if lang in STRINGS else "en"


def get_language() -> str:
    return _current_lang


def t(key: str, **kwargs) -> str:
    template = STRINGS.get(_current_lang, {}).get(key)
    if not template:
        template = STRINGS["en"].get(key, key)
    return template.format(**kwargs) if kwargs else template