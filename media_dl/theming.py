"""Follow the active Omarchy theme (same pattern as other Omarchy apps)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

THEME_DIR = Path.home() / ".local/state/omarchy/current/theme"
THEME_GTK_CSS = THEME_DIR / "gtk.css"
THEME_COLORS = THEME_DIR / "colors.toml"

_HEX = re.compile(r"^#?[0-9a-fA-F]{3,8}$")

# Provider kept alive + reloaded when the theme symlink changes.
_provider: Gtk.CssProvider | None = None
_provider_attached = False
_monitors: list[Gio.FileMonitor] = []


def _color(colors: dict[str, str], *keys: str, default: str = "#888888") -> str:
    for key in keys:
        val = colors.get(key)
        if val and _HEX.match(val.strip()):
            v = val.strip()
            return v if v.startswith("#") else f"#{v}"
    return default


def _load_colors() -> dict[str, str]:
    """Read palette the Omarchy way: omarchy-theme-color, else colors.toml."""
    colors: dict[str, str] = {}
    bin_path = shutil.which("omarchy-theme-color")
    if bin_path:
        try:
            out = subprocess.check_output(
                [bin_path, "--file", str(THEME_COLORS), "--all"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            for line in out.splitlines():
                if "\t" not in line:
                    continue
                key, val = line.split("\t", 1)
                colors[key.strip()] = val.strip()
            if colors:
                return colors
        except (subprocess.CalledProcessError, OSError, FileNotFoundError):
            pass

    if THEME_COLORS.is_file():
        for line in THEME_COLORS.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            colors[key.strip()] = val.strip().strip("\"'")
    return colors


def _css_from_colors(colors: dict[str, str]) -> str:
    """Libadwaita colors with clear surface hierarchy from the Omarchy palette."""
    mode = _theme_mode(colors)
    bg = _color(colors, "background", "bg", "color0", default="#1e1e2e")
    fg = _color(colors, "bright_foreground", "bright_fg", "color15", "foreground", default="#cdd6f4")
    muted_fg = _color(colors, "foreground", "fg", "color7", default=fg)
    dim = _color(colors, "muted", "dark_fg", "color8", default="#585b70")

    # Inset surfaces must differ from the window. Prefer darker_* on dark themes.
    if mode == "light":
        surface = _color(
            colors, "lighter_background", "lighter_bg", "color15", default="#ffffff"
        )
        if surface.lower() == bg.lower():
            surface = _color(colors, "dark_background", "color7", default="#f0f0f0")
    else:
        surface = _color(
            colors, "darker_background", "darker_bg", "dark_background", "dark_bg",
            default="#11111b",
        )
        if surface.lower() == bg.lower():
            # Theme didn't differentiate — nudge with color8 mix via explicit fallback
            surface = _color(colors, "color8", default="#11111b")
            # If still flat, use a near-black derived note: keep darker hex from palette
            if surface.lower() == bg.lower():
                surface = "#0a0a0f"

    red = _color(colors, "red", "color1", default="#f38ba8")
    green = _color(colors, "green", "color2", default="#a6e3a1")
    yellow = _color(colors, "yellow", "color3", default="#f9e2af")
    blue = _color(colors, "accent", "blue", "color4", default="#89b4fa")
    magenta = _color(colors, "magenta", "purple", "color5", default="#cba6f7")
    cyan = _color(colors, "cyan", "color6", default="#94e2d5")
    accent = _color(colors, "accent", "blue", "color4", default=blue)
    selection = _color(colors, "selection", "accent", default=accent)
    accent_fg = _color(colors, "background", "bg", "color0", default=bg)

    return f"""
@define-color accent_bg_color {accent};
@define-color accent_fg_color {accent_fg};
@define-color accent_color {accent};

@define-color window_bg_color {bg};
@define-color window_fg_color {fg};

@define-color view_bg_color {surface};
@define-color view_fg_color {fg};

@define-color headerbar_bg_color {bg};
@define-color headerbar_fg_color {fg};
@define-color headerbar_backdrop_color {bg};
@define-color headerbar_shade_color {surface};
@define-color headerbar_border_color {dim};

@define-color card_bg_color {surface};
@define-color card_fg_color {fg};
@define-color card_shade_color {dim};

@define-color popover_bg_color {surface};
@define-color popover_fg_color {fg};

@define-color secondary_sidebar_bg_color {surface};
@define-color sidebar_bg_color {surface};
@define-color sidebar_fg_color {fg};
@define-color sidebar_backdrop_color {bg};

@define-color destructive_bg_color {red};
@define-color destructive_fg_color {accent_fg};
@define-color success_bg_color {green};
@define-color success_fg_color {accent_fg};
@define-color warning_bg_color {yellow};
@define-color warning_fg_color {accent_fg};
@define-color error_bg_color {red};
@define-color error_fg_color {accent_fg};

@define-color dialog_bg_color {surface};
@define-color dialog_fg_color {fg};

@define-color borders {dim};
@define-color shade_color alpha({dim}, 0.35);

@define-color theme_fg_color {fg};
@define-color theme_text_color {fg};
@define-color theme_bg_color {bg};
@define-color theme_base_color {surface};
@define-color theme_selected_bg_color {accent};
@define-color theme_selected_fg_color {accent_fg};

/* Explicit surfaces — libadwaita cards / text views */
.card {{
  background-color: {surface};
  color: {fg};
  border: 1px solid alpha({dim}, 0.65);
  border-radius: 12px;
  box-shadow: none;
}}

scrolledwindow.card {{
  background-color: {surface};
  border: 1px solid alpha({dim}, 0.65);
  border-radius: 12px;
}}

scrolledwindow.card > scrollbar {{
  background: transparent;
}}

textview, textview > text {{
  background-color: {surface};
  color: {fg};
  caret-color: {accent};
}}

textview text selection {{
  background-color: alpha({accent}, 0.45);
  color: {fg};
}}

.dim-label, label.dim-label {{
  color: {muted_fg};
  opacity: 1;
}}

row, listview row {{
  border-bottom: 1px solid alpha({dim}, 0.35);
}}

button.flat {{
  color: {accent};
}}

button.flat:hover {{
  background-color: alpha({accent}, 0.15);
  color: {fg};
}}

switch {{
  background-color: {dim};
}}

switch:checked {{
  background-color: {accent};
}}

progressbar trough {{
  min-height: 8px;
  background-color: alpha({dim}, 0.55);
  border-radius: 4px;
}}

progressbar progress {{
  min-height: 8px;
  background-color: {accent};
  border-radius: 4px;
}}

toast {{
  background-color: {surface};
  color: {fg};
  border: 1px solid {dim};
}}
"""


def _theme_mode(colors: dict[str, str]) -> str:
    mode = (colors.get("mode") or "").strip().lower()
    if mode in ("light", "dark"):
        return mode
    bin_path = shutil.which("omarchy-theme-color")
    if bin_path:
        try:
            out = subprocess.check_output(
                [bin_path, "--file", str(THEME_COLORS), "mode"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            if out in ("light", "dark"):
                return out
        except (subprocess.CalledProcessError, OSError, FileNotFoundError):
            pass
    return "dark"


def _apply_color_scheme(mode: str) -> None:
    manager = Adw.StyleManager.get_default()
    if mode == "light":
        manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
    else:
        manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)


def _theme_css() -> bytes:
    # Prefer palette-driven CSS so cards/window stay distinct (theme gtk.css
    # files often set view == window and flatten the UI).
    colors = _load_colors()
    if colors:
        return _css_from_colors(colors).encode("utf-8")
    if THEME_GTK_CSS.is_file():
        return THEME_GTK_CSS.read_bytes()
    return b""


def apply_omarchy_theme() -> None:
    """Load Omarchy gtk.css / colors.toml into this app (and follow dark/light)."""
    global _provider, _provider_attached

    colors = _load_colors()
    _apply_color_scheme(_theme_mode(colors))

    css = _theme_css()
    if not css:
        return

    if _provider is None:
        _provider = Gtk.CssProvider()

    display = Gdk.Display.get_default()
    if display is not None and not _provider_attached:
        Gtk.StyleContext.add_provider_for_display(
            display,
            _provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        _provider_attached = True

    try:
        _provider.load_from_data(css)
    except GLib.Error:
        # Bad CSS from a theme file — fall back to generated palette.
        if THEME_GTK_CSS.is_file() and colors:
            try:
                _provider.load_from_data(_css_from_colors(colors).encode("utf-8"))
            except GLib.Error:
                pass


def _on_theme_changed(_monitor, _file, _other, _event) -> None:
    # Debounce slightly: theme-set rewrites several files in a burst.
    GLib.timeout_add(200, _reload_once)


_reload_pending = False


def _reload_once() -> bool:
    global _reload_pending
    _reload_pending = False
    apply_omarchy_theme()
    return False


def _queue_reload(_monitor=None, _file=None, _other=None, _event=None) -> None:
    global _reload_pending
    if _reload_pending:
        return
    _reload_pending = True
    GLib.timeout_add(250, _reload_once)


def watch_omarchy_theme() -> None:
    """Reload CSS when Omarchy switches themes."""
    if _monitors:
        return
    for path in (THEME_DIR, THEME_GTK_CSS, THEME_COLORS):
        parent = path if path.is_dir() else path.parent
        if not parent.exists():
            continue
        try:
            mon = Gio.File.new_for_path(str(parent)).monitor_directory(
                Gio.FileMonitorFlags.NONE, None
            )
            mon.connect("changed", _queue_reload)
            _monitors.append(mon)
        except GLib.Error:
            continue
    # Also watch the theme.name file (updated on every theme set).
    name = Path.home() / ".local/state/omarchy/current/theme.name"
    if name.parent.exists():
        try:
            mon = Gio.File.new_for_path(str(name.parent)).monitor_directory(
                Gio.FileMonitorFlags.NONE, None
            )
            mon.connect("changed", _queue_reload)
            _monitors.append(mon)
        except GLib.Error:
            pass


def init_theming() -> None:
    """Call once at app startup (after Adw.init / display available)."""
    # Prefer SYSTEM so we track gsettings when Omarchy flips light/dark,
    # then FORCE from colors.toml mode for consistency with themed apps.
    apply_omarchy_theme()
    watch_omarchy_theme()
    # Helpful for debugging in terminal
    if os.environ.get("MEDIA_DL_DEBUG_THEME"):
        print(f"omarchy theme dir: {THEME_DIR} exists={THEME_DIR.exists()}")
        print(f"gtk.css: {THEME_GTK_CSS.is_file()} colors: {THEME_COLORS.is_file()}")
