"""GTK4 + libadwaita UI for Media DL (video, FLAC, or both)."""

from __future__ import annotations

import queue
import shutil
import subprocess
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk, Pango  # noqa: E402

from media_dl import config  # noqa: E402
from media_dl.downloader import (  # noqa: E402
    MODES,
    QUALITIES,
    MediaDownloader,
    parse_urls,
)
from media_dl.preview import (  # noqa: E402
    TYPE_KEYS,
    TYPE_LABEL,
    TYPE_SUFFIX,
    Probe,
    audio_summary,
    codec_name,
    detect_video_type,
    display_base,
    display_filename,
    strip_term,
    fetch_thumbnail,
    fmt_duration,
    pick_audio,
    pick_video,
    probe_url,
    video_summary,
)
from media_dl.theming import init_theming  # noqa: E402

APP_ID = "local.mediadl"

MODES_UI = ["Video (MP4, merged)", "Audio (FLAC)", "Both (video + FLAC)"]
MODE_VALUES = ["video", "audio", "both"]

QUALITY_UI = ["Best available", "2160p (4K)", "1440p (2K)", "1080p", "720p", "480p"]
QUALITY_VALUES = ["best", "2160p", "1440p", "1080p", "720p", "480p"]

AUDIO_UI = ["Best available", "High (≤192 kbps)", "Standard (≤128 kbps)"]
AUDIO_VALUES = ["best", "high", "standard"]

TYPE_UI = ["Auto"] + [TYPE_LABEL[key] for key in TYPE_KEYS]

# Left-edge color on each queue ticket, keyed by the Plex suffix.
_RAIL = {
    "video": "rail-video",
    "live": "rail-live",
    "lyrics": "rail-lyrics",
    "concert": "rail-concert",
    "interview": "rail-interview",
    "behindthescenes": "rail-bts",
}

APP_CSS = b"""
window.mdl-win {
  background-color: #1a1a1a;
  color: #d7d7d7;
  border-radius: 4px 18px 4px 12px;
}
window.mdl-win toolbarview,
window.mdl-win .top-bar,
window.mdl-win .bottom-bar {
  background-color: #2a2a2a;
}
.mdl-setting {
  margin: 0 10px 4px 10px;
  padding: 4px 6px;
  background-color: #141414;
  border: 1px solid #000;
  box-shadow: inset 0 1px 0 #333;
}
.mdl-setting-label {
  font-family: "Liberation Mono", monospace;
  font-size: 0.68em;
  font-weight: 700;
  letter-spacing: 0.16em;
  color: #7CFF6B;
  min-width: 72px;
}
.mdl-setting .mdl-phosphor {
  font-family: "Liberation Mono", monospace;
  color: #3dce55;
}
.mdl-split {
  margin: 0 10px;
}
.mdl-split separator {
  min-height: 12px;
  min-width: 12px;
  margin: 2px 0;
  background-color: #1a1a1a;
  background-image: linear-gradient(to bottom, #6a6a6a, #222 45%, #111);
  border: 1px solid #000;
  box-shadow: inset 0 1px 0 #888;
}

.mdl-lcd {
  margin: 8px 8px 4px 8px;
  padding: 8px;
  background-color: #041208;
  background-image: repeating-linear-gradient(
    to bottom,
    transparent 0 2px,
    alpha(black, 0.35) 2px 3px
  );
  border-radius: 2px;
  border: 2px solid #111;
  box-shadow:
    inset 0 0 0 1px #1c3a22,
    inset 0 0 18px alpha(black, 0.85),
    0 1px 0 #4a4a4a;
}
.mdl-lcd .mdl-phosphor,
.mdl-lcd .mdl-title {
  font-family: "Liberation Mono", monospace;
  color: #7CFF6B;
}
.mdl-lcd .mdl-title {
  font-weight: 700;
  font-size: 1.05em;
}
.mdl-lcd .caption {
  color: #3dce55;
}
.mdl-lcd button.flat {
  color: #7CFF6B;
  background: transparent;
  border: none;
  box-shadow: none;
}
.mdl-lcd button.flat:hover {
  background-color: alpha(#7CFF6B, 0.12);
}
.mdl-thumb {
  border-radius: 2px;
  background-color: #000;
  min-width: 112px;
  min-height: 64px;
  border: 1px solid #16331c;
  box-shadow: inset 0 0 0 1px #000, 0 0 0 1px #0a1a0e;
}
.mdl-thumb picture { border-radius: 1px; }
.mdl-badge {
  background-color: #000;
  color: #7CFF6B;
  border-radius: 1px;
  padding: 0 4px;
  font-family: "Liberation Mono", monospace;
  font-size: 0.68em;
  border: 1px solid #1f5a2a;
}

.mdl-strip { margin: 2px 10px 4px 10px; }
.mdl-modes button,
.mdl-dock button,
.mdl-setting dropdown button,
.mdl-playlist dropdown button {
  border-radius: 2px;
  border: 1px solid #111;
  font-family: "Liberation Mono", monospace;
  font-weight: 700;
  font-size: 0.82em;
  color: #eee;
  background-color: #3a3a3a;
  background-image: linear-gradient(to bottom, #6a6a6a, #2c2c2c 55%, #222);
  box-shadow: inset 0 1px 0 #8a8a8a, 0 1px 0 #000;
  padding: 3px 8px;
}
.mdl-modes button:checked {
  color: #041208;
  background-color: #7CFF6B;
  background-image: linear-gradient(to bottom, #b6ffaa, #3db84a);
  box-shadow: inset 0 2px 3px #063, 0 1px 0 #000;
}
.mdl-modes button:active,
.mdl-dock button:active {
  background-image: linear-gradient(to bottom, #222, #444);
  box-shadow: inset 0 2px 3px #000;
}

.mdl-plhead-label {
  font-family: "Liberation Mono", monospace;
  font-size: 0.68em;
  font-weight: 700;
  letter-spacing: 0.16em;
  color: #7CFF6B;
}
.mdl-plhead button,
.mdl-clean button {
  color: #7CFF6B;
  background: transparent;
  border: none;
  box-shadow: none;
  font-family: "Liberation Mono", monospace;
}
.mdl-clean {
  padding: 3px 6px;
  background-color: #0c140e;
  border-left: 1px solid #000;
  border-right: 1px solid #000;
}
.mdl-clean entry {
  font-family: "Liberation Mono", monospace;
  color: #7CFF6B;
  background-color: #050805;
  border: 1px solid #1f5a2a;
  border-radius: 2px;
  min-height: 0;
  padding: 1px 6px;
  box-shadow: inset 0 1px 2px #000;
}

.mdl-well {
  border-radius: 0;
  margin: 0;
  background-color: #050805;
  border: 1px solid #000;
  border-top: none;
  box-shadow: inset 0 2px 6px #000;
}
.mdl-well textview,
.mdl-well textview > text {
  background: transparent;
  color: #7CFF6B;
  font-family: "Liberation Mono", monospace;
}

.mdl-playlist {
  margin: 0;
  background-color: #050805;
  border: 2px solid #111;
  box-shadow: inset 0 0 0 1px #243828, inset 0 0 16px #000;
}
.mdl-playlist .mdl-ticket {
  border-radius: 0;
  margin: 0;
  background: transparent;
  border: none;
  border-bottom: 1px solid #102816;
  box-shadow: none;
}
.mdl-playlist .mdl-ticket.queue-selected {
  box-shadow: inset 3px 0 0 #7CFF6B;
}
.mdl-playlist .queue-name {
  font-family: "Liberation Mono", monospace;
  font-weight: 700;
  font-size: 0.92em;
  color: #c8ffc0;
  background: transparent;
  border: none;
  box-shadow: none;
}
.mdl-playlist .queue-name:focus {
  background-color: alpha(#7CFF6B, 0.12);
  color: #fff;
}
.mdl-playlist .queue-meta {
  font-family: "Liberation Mono", monospace;
  font-size: 0.75em;
  color: #3dce55;
}
.mdl-playlist .dim-label { color: #3dce55; }
.mdl-rail {
  min-width: 3px;
  border-radius: 0;
  margin: 4px 6px 4px 4px;
  background-color: #1c6b32;
  box-shadow: none;
}
.rail-video { background-color: #7CFF6B; }
.rail-live { background-color: #ffe14a; }
.rail-lyrics { background-color: #7CFF6B; }
.rail-concert { background-color: #ff8844; }
.rail-interview { background-color: #7ad0ff; }
.rail-bts { background-color: #ffe14a; }

.mdl-dock {
  margin: 0 10px 8px 10px;
  padding: 6px;
  background-color: #2a2a2a;
  background-image: repeating-linear-gradient(
    90deg,
    alpha(white, 0.05) 0 1px,
    transparent 1px 3px
  );
  border: 2px solid #111;
  box-shadow: inset 0 1px 0 #555, 0 2px 0 #000;
}
.mdl-dock button {
  border-radius: 2px;
  min-height: 0;
  padding: 3px 10px;
  font-family: "Liberation Mono", monospace;
  font-weight: 700;
  font-size: 0.82em;
}
.mdl-dock button.mdl-stop {
  color: #ffd0d4;
  background-color: #4a2024;
  background-image: linear-gradient(to bottom, #7a4048, #3a1518);
  border: 1px solid #200;
  border-radius: 2px;
  box-shadow: inset 0 1px 0 #a06068, 0 1px 0 #000;
}
.mdl-dock progressbar trough {
  min-height: 8px;
  border-radius: 0;
  background-color: #050805;
  border: 1px solid #000;
  box-shadow: inset 0 1px 3px #000;
}
.mdl-dock progressbar progress {
  min-height: 8px;
  border-radius: 0;
  background-color: #7CFF6B;
  background-image: linear-gradient(to bottom, #d8ffd0, #3db84a);
}
button.mdl-go {
  border-radius: 2px;
  padding: 3px 12px;
  font-family: "Liberation Mono", monospace;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: #041208;
  background-color: #7CFF6B;
  background-image: linear-gradient(to bottom, #d8ffd0, #3db84a 60%, #1e7a32);
  border: 1px solid #063;
  box-shadow: inset 0 1px 0 #fff, 0 1px 0 #042;
}
button.mdl-go:active {
  background-image: linear-gradient(to bottom, #1e7a32, #7CFF6B);
  box-shadow: inset 0 2px 4px #021;
}
"""


class QueueTicket(Gtk.Box):
    """Queue row whose progress fill is the full height of the row."""

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._fraction = 0.0
        self._kind = ""
        self._run = Gdk.RGBA()
        self._run.parse("rgba(124,255,107,0.28)")
        self._done = Gdk.RGBA()
        self._done.parse("rgba(124,255,107,0.55)")
        self._fail = Gdk.RGBA()
        self._fail.parse("rgba(255,68,85,0.55)")

    def set_progress(self, fraction: float, kind: str) -> None:
        self._fraction = min(max(fraction, 0.0), 1.0)
        self._kind = kind or ""
        self.queue_draw()

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        color = {"is-run": self._run, "is-done": self._done, "is-fail": self._fail}.get(self._kind)
        width = self.get_width()
        height = self.get_height()
        if color is not None and self._fraction > 0 and width > 0 and height > 0:
            rect = Graphene.Rect()
            rect.init(0, 0, width * self._fraction, height)
            snapshot.append_color(color, rect)
        Gtk.Box.do_snapshot(self, snapshot)


class MediaDlWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, initial_text: str = "") -> None:
        super().__init__(application=app, title="Media DL")
        self.add_css_class("mdl-win")
        self.set_decorated(False)
        self.set_default_size(773, 909)
        self._busy = False
        self._probe: Probe | None = None
        self._probe_timeout: int | None = None
        self._probes: dict[str, Probe] = {}
        self._probe_inflight: set[str] = set()
        self._probe_queue: queue.Queue[str] = queue.Queue()
        self._probe_worker_started = False
        self._thumb_inflight: set[str] = set()
        self._thumbs: dict[str, Gdk.Texture | None] = {}
        self._link_types: dict[str, str] = {}
        self._link_titles: dict[str, str] = {}
        self._link_rows: dict[str, QueueTicket] = {}
        self._link_entries: dict[str, Gtk.Entry] = {}
        self._link_meta: dict[str, Gtk.Label] = {}
        self._link_dropdowns: dict[str, Gtk.DropDown] = {}
        self._link_rails: dict[str, Gtk.Box] = {}
        self._selected_url = ""
        self._downloader: MediaDownloader | None = None
        self._progress_latest: tuple[str, str, str, float | None] | None = None
        self._progress_scheduled = False

        self.cfg = config.load()

        toolbar = Adw.ToolbarView()
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.set_margin_top(8)

        # Fixed strip. The thumbnail is an overlay, so a loaded image cannot resize it.
        preview = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        preview.add_css_class("mdl-preview")
        preview.add_css_class("mdl-lcd")
        preview.set_size_request(-1, 96)

        thumb_frame = Gtk.Overlay()
        thumb_frame.add_css_class("mdl-thumb")
        thumb_frame.set_overflow(Gtk.Overflow.HIDDEN)
        thumb_frame.set_size_request(128, 72)
        thumb_frame.set_valign(Gtk.Align.CENTER)
        thumb_sizer = Gtk.Box()
        thumb_sizer.set_size_request(128, 72)
        thumb_frame.set_child(thumb_sizer)

        self.thumb = Gtk.Picture()
        self.thumb.set_content_fit(Gtk.ContentFit.COVER)
        self.thumb.set_can_shrink(True)
        self.thumb.set_hexpand(True)
        self.thumb.set_vexpand(True)
        self.thumb.set_halign(Gtk.Align.FILL)
        self.thumb.set_valign(Gtk.Align.FILL)
        self.thumb.set_can_target(False)
        thumb_frame.add_overlay(self.thumb)

        self.standby = Gtk.Label(label="no still")
        self.standby.add_css_class("mdl-phosphor")
        self.standby.add_css_class("caption")
        self.standby.set_halign(Gtk.Align.CENTER)
        self.standby.set_valign(Gtk.Align.CENTER)
        self.standby.set_can_target(False)
        thumb_frame.add_overlay(self.standby)

        self.badge = Gtk.Label(label="")
        self.badge.add_css_class("mdl-badge")
        self.badge.set_halign(Gtk.Align.END)
        self.badge.set_valign(Gtk.Align.END)
        self.badge.set_margin_end(4)
        self.badge.set_margin_bottom(4)
        self.badge.set_visible(False)
        thumb_frame.add_overlay(self.badge)

        self.spinner = Gtk.Spinner()
        self.spinner.set_halign(Gtk.Align.CENTER)
        self.spinner.set_valign(Gtk.Align.CENTER)
        self.spinner.set_visible(False)
        thumb_frame.add_overlay(self.spinner)
        preview.append(thumb_frame)

        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        details.set_hexpand(True)
        details.set_valign(Gtk.Align.CENTER)

        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.title_label = Gtk.Label(
            label="No preview",
            xalign=0,
            lines=1,
            ellipsize=Pango.EllipsizeMode.END,
        )
        self.title_label.add_css_class("mdl-title")
        self.title_label.add_css_class("mdl-phosphor")
        self.title_label.set_hexpand(True)
        self.title_label.set_valign(Gtk.Align.CENTER)
        title_row.append(self.title_label)

        self.prev_btn = Gtk.Button(
            icon_name="go-previous-symbolic", valign=Gtk.Align.CENTER
        )
        self.prev_btn.add_css_class("flat")
        self.prev_btn.set_tooltip_text("Previous")
        self.prev_btn.set_sensitive(False)
        self.prev_btn.connect("clicked", lambda *_: self._cycle_preview(-1))
        title_row.append(self.prev_btn)

        self.nav_label = Gtk.Label(label="")
        self.nav_label.add_css_class("mdl-phosphor")
        self.nav_label.add_css_class("caption")
        self.nav_label.set_valign(Gtk.Align.CENTER)
        title_row.append(self.nav_label)

        self.next_btn = Gtk.Button(
            icon_name="go-next-symbolic", valign=Gtk.Align.CENTER
        )
        self.next_btn.add_css_class("flat")
        self.next_btn.set_tooltip_text("Next")
        self.next_btn.set_sensitive(False)
        self.next_btn.connect("clicked", lambda *_: self._cycle_preview(1))
        title_row.append(self.next_btn)

        self.play_btn = Gtk.Button(
            icon_name="media-playback-start-symbolic", valign=Gtk.Align.CENTER
        )
        self.play_btn.add_css_class("flat")
        self.play_btn.set_sensitive(False)
        self.play_btn.set_tooltip_text("Play this stream in mpv")
        self.play_btn.connect("clicked", self._on_play_preview)
        title_row.append(self.play_btn)
        details.append(title_row)

        self.meta_label = Gtk.Label(
            label="",
            xalign=0,
            lines=1,
            ellipsize=Pango.EllipsizeMode.END,
        )
        self.meta_label.add_css_class("mdl-phosphor")
        self.meta_label.add_css_class("caption")
        details.append(self.meta_label)

        # Kept for the download summary; the strip itself stays thumb + title.
        self.video_line = Gtk.Label(label="", xalign=0, lines=1, ellipsize=Pango.EllipsizeMode.END)
        self.video_line.set_use_markup(True)
        self.video_line.set_visible(False)
        self.audio_line = Gtk.Label(label="", xalign=0, lines=1, ellipsize=Pango.EllipsizeMode.END)
        self.audio_line.set_use_markup(True)
        self.audio_line.set_visible(False)
        self.saved_line = Gtk.Label(label="", xalign=0, lines=1, ellipsize=Pango.EllipsizeMode.END)
        self.saved_line.set_use_markup(True)
        self.saved_line.set_visible(False)
        preview.append(details)
        root.append(preview)

        pills = Gtk.Box(spacing=6)
        pills.add_css_class("mdl-modes")
        pills.set_valign(Gtk.Align.CENTER)
        leader = None
        self._mode_buttons: list[Gtk.ToggleButton] = []
        for label, tip in (
            ("Video", "Merged picture and sound, one file"),
            ("Audio", "Lossless audio"),
            ("Both", "Video plus a matching FLAC"),
        ):
            btn = Gtk.ToggleButton(label=label)
            btn.set_tooltip_text(tip)
            if leader is None:
                leader = btn
            else:
                btn.set_group(leader)
            self._mode_buttons.append(btn)
            pills.append(btn)
        self._mode_buttons[self._index(MODE_VALUES, self.cfg["mode"], 0)].set_active(True)
        for btn in self._mode_buttons:
            btn.connect("toggled", self._on_mode_toggled)

        type_row = Gtk.Box(spacing=8)
        type_row.add_css_class("mdl-setting")
        type_label = Gtk.Label(label="TYPE", xalign=0)
        type_label.add_css_class("mdl-setting-label")
        type_label.set_valign(Gtk.Align.CENTER)
        type_row.append(type_label)
        type_row.append(pills)
        root.append(type_row)

        self.quality_drop = Gtk.DropDown(model=Gtk.StringList.new(QUALITY_UI))
        self.quality_drop.set_selected(
            self._index(QUALITY_VALUES, self.cfg["video_quality"], 3)
        )
        self.quality_drop.set_tooltip_text(
            "Picture size. If that size is missing, the next smaller one is used"
        )
        self.audio_drop = Gtk.DropDown(model=Gtk.StringList.new(AUDIO_UI))
        self.audio_drop.set_selected(
            self._index(AUDIO_VALUES, self.cfg["audio_quality"], 0)
        )
        self.audio_drop.set_tooltip_text(
            "Audio source. FLAC is always written lossless."
        )
        self.quality_drop.set_valign(Gtk.Align.CENTER)
        self.audio_drop.set_valign(Gtk.Align.CENTER)
        quality_wrap = Gtk.Box(spacing=6)
        quality_wrap.set_valign(Gtk.Align.CENTER)
        picture_label = Gtk.Label(label="Picture", xalign=0)
        picture_label.add_css_class("caption")
        picture_label.add_css_class("mdl-phosphor")
        quality_wrap.append(picture_label)
        quality_wrap.append(self.quality_drop)
        sound_label = Gtk.Label(label="Sound", xalign=0)
        sound_label.add_css_class("caption")
        sound_label.add_css_class("mdl-phosphor")
        quality_row = Gtk.Box(spacing=8)
        quality_row.add_css_class("mdl-setting")
        quality_label = Gtk.Label(label="QUALITY", xalign=0)
        quality_label.add_css_class("mdl-setting-label")
        quality_label.set_valign(Gtk.Align.CENTER)
        quality_row.append(quality_label)
        quality_row.append(quality_wrap)
        quality_row.append(sound_label)
        quality_row.append(self.audio_drop)
        self.quality_drop.connect("notify::selected", self._on_quality_changed)
        self.audio_drop.connect("notify::selected", self._on_quality_changed)
        root.append(quality_row)

        paste_head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        paste_head.add_css_class("mdl-setting")
        pl_label = Gtk.Label(label="PLAYLIST", xalign=0, hexpand=True)
        pl_label.add_css_class("mdl-plhead-label")
        pl_label.set_valign(Gtk.Align.CENTER)
        paste_head.append(pl_label)
        root.append(paste_head)
        self._bar_heights = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.VERTICAL)
        self._bar_heights.add_widget(type_row)
        self._bar_heights.add_widget(quality_row)
        self._bar_heights.add_widget(paste_head)

        link_scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        link_scroll.set_propagate_natural_height(False)
        link_scroll.set_size_request(-1, 0)
        link_scroll.add_css_class("mdl-well")
        self.url_view = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            accepts_tab=False,
            top_margin=4,
            bottom_margin=4,
            left_margin=8,
            right_margin=8,
        )
        self.url_view.set_monospace(True)
        self.url_buffer = self.url_view.get_buffer()
        link_scroll.set_child(self.url_view)

        clean = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        clean.add_css_class("mdl-clean")
        self.strip_entry = Gtk.Entry()
        self.strip_entry.set_placeholder_text("term or *")
        self.strip_entry.set_width_chars(18)
        self.strip_entry.set_max_width_chars(32)
        self.strip_entry.set_hexpand(True)
        self.strip_entry.set_tooltip_text(
            "Removed from every title. * is a wildcard: ft. * drops that and the rest, [*] drops a bracket tag. A quote mark clears all quote styles."
        )
        self.strip_entry.connect("activate", lambda *_: self._on_strip_titles())
        clean.append(self.strip_entry)
        strip_btn = Gtk.Button(label="Strip")
        strip_btn.add_css_class("flat")
        strip_btn.set_tooltip_text("Remove that text from every title")
        strip_btn.connect("clicked", lambda *_: self._on_strip_titles())
        clean.append(strip_btn)

        self.queue_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.queue_box.set_margin_start(2)
        self.queue_box.set_margin_end(2)
        self.queue_box.set_margin_bottom(2)
        self.queue_empty = Gtk.Label(
            label="playlist empty — paste links above",
            xalign=0,
        )
        self.queue_empty.add_css_class("dim-label")
        self.queue_empty.add_css_class("caption")
        self.queue_empty.set_margin_bottom(8)
        self.queue_box.append(self.queue_empty)

        scrolled = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        scrolled.set_propagate_natural_height(False)
        scrolled.add_css_class("mdl-playlist")
        scrolled.set_child(self.queue_box)
        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        paned.add_css_class("mdl-split")
        paned.set_wide_handle(True)
        queue_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        queue_col.append(clean)
        queue_col.append(scrolled)
        paned.set_start_child(link_scroll)
        paned.set_end_child(queue_col)
        paned.set_resize_start_child(False)
        paned.set_resize_end_child(True)
        paned.set_shrink_start_child(True)
        paned.set_shrink_end_child(True)
        paned.set_position(120)
        root.append(paned)

        dock = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        dock.add_css_class("mdl-dock")
        self.progress = Gtk.ProgressBar(show_text=False)
        dock.append(self.progress)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.folder_btn = Gtk.Button(label=self._folder_caption())
        self.folder_btn.add_css_class("mdl-key")
        self.folder_btn.set_tooltip_text(self.cfg["output_dir"])
        self.folder_btn.connect("clicked", self._on_pick_folder)
        buttons.append(self.folder_btn)
        self.open_btn = Gtk.Button(label="Show")
        self.open_btn.add_css_class("mdl-key")
        self.open_btn.set_tooltip_text("Open the save folder")
        self.open_btn.connect("clicked", self._on_open_folder)
        buttons.append(self.open_btn)
        buttons.append(Gtk.Box(hexpand=True))
        self.download_btn = Gtk.Button(label="Download")
        self.download_btn.add_css_class("mdl-go")
        self.download_btn.set_hexpand(False)
        self.download_btn.connect("clicked", self._on_download)
        buttons.append(self.download_btn)
        self.stop_btn = Gtk.Button(label="Stop")
        self.stop_btn.add_css_class("mdl-stop")
        self.stop_btn.set_sensitive(False)
        self.stop_btn.connect("clicked", self._on_stop)
        buttons.append(self.stop_btn)
        dock.append(buttons)
        root.append(dock)

        toolbar.set_content(root)

        self.overlay = Adw.ToastOverlay()
        self.overlay.set_child(toolbar)
        self.set_content(self.overlay)


        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key)
        self.add_controller(key)

        self.url_buffer.connect("changed", self._on_urls_changed)
        self._sync_mode_ui()
        if initial_text:
            self.url_buffer.set_text(initial_text)
    def _focus_url(self) -> bool:
        self.url_view.grab_focus()
        return False

    # ----------------------------------------------------------- utilities
    @staticmethod
    def _index(values: list[str], value, fallback: int) -> int:
        try:
            return values.index(str(value))
        except ValueError:
            return fallback

    @property
    def mode(self) -> str:
        for i, btn in enumerate(self._mode_buttons):
            if btn.get_active():
                return MODE_VALUES[i]
        return MODE_VALUES[0]

    @property
    def max_height(self) -> int | None:
        idx = self.quality_drop.get_selected()
        if idx >= len(QUALITY_VALUES):
            idx = 3
        return QUALITIES[QUALITY_VALUES[idx]]

    @property
    def audio_quality(self) -> str:
        idx = self.audio_drop.get_selected()
        if idx >= len(AUDIO_VALUES):
            idx = 0
        return AUDIO_VALUES[idx]

    def _folder_caption(self) -> str:
        name = Path(self.cfg["output_dir"]).name or self.cfg["output_dir"]
        return f"Save to: {name}"

    def _toast(self, message: str) -> None:
        self.overlay.add_toast(Adw.Toast(title=message, timeout=3))

    def _sync_mode_ui(self) -> None:
        mode = self.mode
        has_video = mode in ("video", "both")
        self.quality_drop.get_parent().set_visible(has_video)
        self._refresh_preview_labels()

    def _save_cfg(self) -> None:
        vidx = self.quality_drop.get_selected()
        if vidx >= len(QUALITY_VALUES):
            vidx = 3
        config.save(
            {
                "mode": self.mode,
                "video_quality": QUALITY_VALUES[vidx],
                "audio_quality": self.audio_quality,
                "output_dir": self.cfg["output_dir"],
            }
        )

    # -------------------------------------------------------------- events
    def _on_mode_toggled(self, btn: Gtk.ToggleButton) -> None:
        if not btn.get_active():
            return
        self._sync_mode_ui()
        self._save_cfg()

    def _on_quality_changed(self, _row, _pspec) -> None:
        self._refresh_preview_labels()
        self._save_cfg()

    def _on_pick_folder(self, _btn: Gtk.Button) -> None:
        dialog = Gtk.FileDialog(title="Save downloads to…")
        dialog.select_folder(self, None, self._on_folder_chosen)

    def _on_folder_chosen(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        if folder is None:
            return
        self.cfg["output_dir"] = folder.get_path()
        self.folder_btn.set_label(self._folder_caption())
        self.folder_btn.set_tooltip_text(self.cfg["output_dir"])
        self._save_cfg()

    def _on_key(self, _controller, keyval, _keycode, state) -> bool:
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if ctrl and keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._on_download(self.download_btn)
            return True
        return False

    # -------------------------------------------------------------- preview
    def _on_urls_changed(self, _buffer) -> None:
        if self._probe_timeout is not None:
            GLib.source_remove(self._probe_timeout)
        self._probe_timeout = GLib.timeout_add(350, self._sync_queue_soon)

    def _sync_queue_soon(self) -> bool:
        self._probe_timeout = None
        self._sync_queue()
        return False

    def _sync_queue(self) -> None:
        start, end = self.url_buffer.get_bounds()
        urls = parse_urls(self.url_buffer.get_text(start, end, False))
        current = set(urls)

        for url in list(self._link_rows):
            if url not in current:
                row = self._link_rows.pop(url)
                self.queue_box.remove(row)
                self._link_dropdowns.pop(url, None)
                self._link_entries.pop(url, None)
                self._link_meta.pop(url, None)
                self._link_rails.pop(url, None)

        for url in urls:
            if url not in self._link_rows:
                self._add_link_row(url)
            self._ensure_probe(url)

        self.queue_empty.set_visible(not urls)

        if self._selected_url not in current:
            self._selected_url = urls[0] if urls else ""
        if urls:
            self._select_url(self._selected_url)
        else:
            self._clear_preview()

    def _add_link_row(self, url: str) -> None:
        frame = QueueTicket()
        frame.add_css_class("mdl-ticket")
        frame.set_overflow(Gtk.Overflow.HIDDEN)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row.set_hexpand(True)
        row.set_vexpand(True)

        rail = Gtk.Box()
        rail.add_css_class("mdl-rail")
        rail.set_hexpand(False)
        row.append(rail)

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        content.set_hexpand(True)
        content.set_margin_top(2)
        content.set_margin_bottom(2)
        content.set_margin_end(4)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_hexpand(True)

        # Editable name: click it, type, then Enter or click away to commit.
        entry = Gtk.Entry()
        entry.set_has_frame(False)
        entry.add_css_class("queue-name")
        entry.set_hexpand(True)
        entry.set_width_chars(14)
        entry.set_max_width_chars(120)
        entry.set_text(self._display_title(url))
        entry.set_tooltip_text("Click to rename — Enter or click away to save")
        entry.connect("activate", self._on_name_activated, url)
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", lambda *_: self._select_url(url))
        focus.connect("leave", lambda *_: self._commit_name(url))
        entry.add_controller(focus)
        text.append(entry)

        meta = Gtk.Label(label="Reading metadata…", xalign=0)
        meta.add_css_class("dim-label")
        meta.add_css_class("caption")
        meta.add_css_class("queue-meta")
        meta.set_ellipsize(Pango.EllipsizeMode.END)
        meta.set_max_width_chars(60)
        meta_click = Gtk.GestureClick()
        meta_click.connect("released", lambda *_: self._select_url(url))
        meta.add_controller(meta_click)
        text.append(meta)
        content.append(text)

        dropdown = Gtk.DropDown(
            model=Gtk.StringList.new(TYPE_UI), valign=Gtk.Align.CENTER
        )
        dropdown.set_size_request(148, -1)
        dropdown.set_hexpand(False)
        dropdown.set_tooltip_text("Plex suffix: -video, -live, -lyrics, …")
        dropdown.set_selected(self._type_index(self._link_types.get(url, "auto")))
        dropdown.connect("notify::selected", self._on_type_selected, url)
        content.append(dropdown)

        row.append(content)
        click = Gtk.GestureClick()
        click.connect("released", lambda *_a: self._select_url(url))
        text.add_controller(click)
        frame.append(row)
        self.queue_box.append(frame)
        self._link_rows[url] = frame
        self._link_entries[url] = entry
        self._link_meta[url] = meta
        self._link_dropdowns[url] = dropdown
        self._link_rails[url] = rail
        self._update_row(url)

    def _auto_title(self, url: str) -> str:
        probe = self._probes.get(url)
        if probe is not None and not probe.error:
            return display_base(probe) or probe.title or ""
        return ""

    def _display_title(self, url: str) -> str:
        return self._link_titles.get(url) or self._auto_title(url) or url

    def _on_name_activated(self, _entry: Gtk.Entry, url: str) -> None:
        self._commit_name(url)
        self.set_focus(None)

    def _rewrite_titles(self, edit) -> int:
        changed = 0
        for url, entry in self._link_entries.items():
            if url not in self._link_titles:
                probe = self._probes.get(url)
                if probe is None or probe.error:
                    continue
            current = entry.get_text().strip()
            if not current or current == url:
                continue
            updated = edit(current).strip()
            if not updated or updated == current:
                continue
            auto = self._auto_title(url)
            if auto and updated == auto:
                self._link_titles.pop(url, None)
            else:
                self._link_titles[url] = updated
            entry.set_text(updated)
            if url == self._selected_url:
                self._refresh_preview_labels()
                self._refresh_preview_title()
            changed += 1
        return changed

    def _on_strip_titles(self) -> None:
        term = self.strip_entry.get_text()
        if not term.strip():
            self._toast("Type a term or symbol to strip")
            return
        if not self._link_entries:
            self._toast("Nothing queued")
            return
        changed = self._rewrite_titles(lambda title: strip_term(title, term))
        if changed:
            self._toast(f"Stripped from {changed} title{'s' if changed != 1 else ''}")
        else:
            self._toast("No titles had that")

    def _commit_name(self, url: str) -> None:
        entry = self._link_entries.get(url)
        if entry is None:
            return
        text = entry.get_text().strip()
        auto = self._auto_title(url)
        if not text or (auto and text == auto) or (not auto and text == url):
            self._link_titles.pop(url, None)
            entry.set_text(auto or url)
        else:
            self._link_titles[url] = text
            entry.set_text(text)
        if url == self._selected_url:
            self._refresh_preview_labels()
            self._refresh_preview_title()

    @staticmethod
    def _type_index(key: str | None) -> int:
        key = key or "auto"
        return 0 if key == "auto" else TYPE_KEYS.index(key) + 1

    def _on_type_selected(self, dropdown: Gtk.DropDown, _pspec, url: str) -> None:
        index = dropdown.get_selected()
        self._link_types[url] = "auto" if index == 0 else TYPE_KEYS[index - 1]
        self._update_row(url)
        if url == self._selected_url:
            self._refresh_preview_labels()

    def _effective_type(self, url: str) -> str:
        override = self._link_types.get(url, "auto")
        if override != "auto":
            return override
        probe = self._probes.get(url)
        if probe is not None and not probe.error:
            return detect_video_type(probe) or "video"
        return "video"

    def _update_row(self, url: str) -> None:
        row = self._link_rows.get(url)
        if row is None:
            return
        self._paint_rail(url)
        probe = self._probes.get(url)
        override = self._link_types.get(url, "auto")
        entry = self._link_entries.get(url)
        meta = self._link_meta.get(url)

        if probe is None:
            if meta is not None:
                meta.set_text("Reading metadata…")
            return
        if probe.error:
            if meta is not None:
                meta.set_text("Couldn't read metadata — pick a type")
            if entry is not None and not entry.has_focus() and url not in self._link_titles:
                entry.set_text(url)
            return

        auto = display_base(probe) or probe.title or url
        if (
            entry is not None
            and not entry.has_focus()
            and url not in self._link_titles
        ):
            entry.set_text(auto)

        bits = []
        if probe.is_live:
            bits.append("LIVE")
        elif probe.duration:
            bits.append(fmt_duration(probe.duration))
        detected = detect_video_type(probe)
        if override == "auto":
            if detected:
                bits.append(f"Auto · {TYPE_LABEL[detected]} · {TYPE_SUFFIX[detected]}")
            else:
                bits.append("Type not detected · defaulting to -video")
        else:
            bits.append(f"{TYPE_LABEL[override]} · {TYPE_SUFFIX[override]}")
        if meta is not None:
            meta.set_text(" · ".join(bits))
            meta.set_tooltip_text(" · ".join(bits))

    def _paint_rail(self, url: str) -> None:
        rail = self._link_rails.get(url)
        if rail is None:
            return
        for css in list(rail.get_css_classes()):
            if css.startswith("rail-"):
                rail.remove_css_class(css)
        rail.add_css_class(_RAIL.get(self._effective_type(url), "rail-video"))

    def _ensure_probe(self, url: str) -> None:
        if url in self._probes or url in self._probe_inflight:
            return
        self._probe_inflight.add(url)
        self._probe_queue.put(url)
        if not self._probe_worker_started:
            self._probe_worker_started = True
            for _ in range(3):
                threading.Thread(target=self._probe_worker, daemon=True).start()

    def _probe_worker(self) -> None:
        """One probe at a time: yt-dlp runs out of process, off the UI thread."""
        while True:
            url = self._probe_queue.get()
            if url is None:
                return
            probe = probe_url(url)
            GLib.idle_add(self._on_probe_done, url, probe)

    def _on_probe_done(self, url: str, probe: Probe) -> bool:
        self._probe_inflight.discard(url)
        self._probes[url] = probe
        while len(self._probes) > 50:
            self._probes.pop(next(iter(self._probes)))
        self._update_row(url)
        if url == self._selected_url:
            self._show_probe(probe)
        return False

    def _select_url(self, url: str) -> None:
        if not url:
            return
        self._selected_url = url
        for link, row in self._link_rows.items():
            if link == url:
                row.add_css_class("queue-selected")
            else:
                row.remove_css_class("queue-selected")
        self._update_preview_nav()

        probe = self._probes.get(url)
        if probe is not None:
            self._show_probe(probe)
        else:
            self._preview_loading()
            self._ensure_probe(url)

    def _queue_urls(self) -> list[str]:
        start, end = self.url_buffer.get_bounds()
        return parse_urls(self.url_buffer.get_text(start, end, False))

    def _cycle_preview(self, step: int) -> None:
        urls = self._queue_urls()
        if len(urls) < 2:
            return
        try:
            index = urls.index(self._selected_url)
        except ValueError:
            index = 0
        self._select_url(urls[(index + step) % len(urls)])

    def _update_preview_nav(self) -> None:
        urls = self._queue_urls()
        many = len(urls) > 1
        self.prev_btn.set_sensitive(many)
        self.next_btn.set_sensitive(many)
        if urls and self._selected_url in urls:
            index = urls.index(self._selected_url) + 1
            self.nav_label.set_text(f"{index:02d}  /  {len(urls):02d}")
        else:
            self.nav_label.set_text("")

    def _refresh_preview_title(self) -> None:
        if self._probe is None or self._probe.error:
            return
        custom = self._link_titles.get(self._selected_url)
        title = custom or self._probe.title or "Untitled"
        if len(title) > 90:
            title = title[:89].rstrip() + "…"
        self.title_label.set_text(title)
        self.title_label.set_tooltip_text(custom or self._probe.title or "Untitled")

    def _clear_preview(self) -> None:
        self._probe = None
        self._selected_url = ""
        self._update_preview_nav()
        self.spinner.stop()
        self.spinner.set_visible(False)
        self._show_placeholder()

    def _show_placeholder(self) -> None:
        self.thumb.set_paintable(None)
        self.standby.set_visible(True)
        self.title_label.set_text("No preview")
        self.meta_label.set_text("")
        self.video_line.set_text("")
        self.audio_line.set_text("")
        self.saved_line.set_text("")
        self.badge.set_visible(False)
        self.play_btn.set_sensitive(False)

    def _preview_loading(self) -> None:
        self._probe = None
        self.thumb.set_paintable(None)
        self.standby.set_visible(False)
        self.spinner.set_visible(True)
        self.spinner.start()
        self.title_label.set_text("Reading streams")
        self.meta_label.set_text("Checking what this link can offer")
        self.video_line.set_text("")
        self.audio_line.set_text("")
        self.saved_line.set_text("")
        self.badge.set_visible(False)
        self.play_btn.set_sensitive(False)

    def _show_probe(self, probe: Probe) -> None:
        self.spinner.stop()
        self.spinner.set_visible(False)
        self._probe = probe

        if probe.error:
            self._show_placeholder()
            self.title_label.set_text("Preview unavailable")
            self.meta_label.set_text(probe.error[:180])
            return

        self._refresh_preview_title()
        bits = []
        if probe.uploader:
            bits.append(probe.uploader)
        if probe.is_live:
            bits.append("LIVE")
        elif probe.duration:
            bits.append(fmt_duration(probe.duration))
        if probe.playlist_count:
            bits.append(f"{probe.playlist_count} items")
        self.meta_label.set_text(" · ".join(bits))

        self._apply_thumbnail(probe.url, probe)
        self._refresh_preview_labels()
        self.play_btn.set_sensitive(True)

    # ------------------------------------------------------------ thumbnail
    def _apply_thumbnail(self, url: str, probe: Probe) -> None:
        if url in self._thumbs:
            self._set_thumb_texture(self._thumbs[url])
            return
        if url in self._thumb_inflight:
            self._set_thumb_texture(None)
            return
        if not probe.thumbnail:
            self._set_thumb_texture(None)
            return
        self._thumb_inflight.add(url)
        threading.Thread(
            target=self._thumb_worker, args=(url, probe.thumbnail), daemon=True
        ).start()

    def _thumb_worker(self, url: str, thumb_url: str) -> None:
        data = fetch_thumbnail(thumb_url)
        GLib.idle_add(self._on_thumb_done, url, data)

    def _on_thumb_done(self, url: str, data: bytes | None) -> bool:
        self._thumb_inflight.discard(url)
        texture = None
        if data:
            try:
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
            except GLib.Error:
                texture = None
        self._thumbs[url] = texture
        if url == self._selected_url:
            self._set_thumb_texture(texture)
        return False

    def _set_thumb_texture(self, texture) -> None:
        if texture is None:
            self.thumb.set_paintable(None)
            self.standby.set_visible(not self.spinner.get_visible())
        else:
            self.thumb.set_paintable(texture)
            self.standby.set_visible(False)

    def _selected_formats(self) -> tuple:
        if self._probe is None:
            return (None, None)
        video = pick_video(self._probe, self.max_height)
        audio = pick_audio(
            self._probe,
            self._audio_abr(),
            prefer_m4a=self.mode in ("video", "both"),
        )
        return (video, audio)

    def _audio_abr(self) -> float | None:
        return {"best": None, "high": 192, "standard": 128}.get(self.audio_quality)

    def _refresh_preview_labels(self) -> None:
        if self._probe is None or self._probe.error:
            return
        mode = self.mode
        video, audio = self._selected_formats()
        vtype = self._effective_type(self._selected_url)

        fallback = ""
        if self.max_height and video and video.height and video.height < self.max_height:
            fallback = (
                f" <span alpha='60%'>· {self.max_height}p unavailable, "
                f"using {video.height}p</span>"
            )

        if mode == "audio":
            self.video_line.set_markup(
                "<b>Video</b>  <span alpha='60%'>not downloaded in audio mode</span>"
            )
            self.audio_line.set_markup(
                f"<b>FLAC</b>  {GLib.markup_escape_text(audio_summary(audio))}"
                "  <span alpha='60%'>(lossless from source)</span>"
            )
        elif mode == "video":
            self.video_line.set_markup(
                f"<b>Video</b>  {GLib.markup_escape_text(video_summary(video))}"
                f"  <span alpha='60%'>({TYPE_SUFFIX[vtype]})</span>{fallback}"
            )
            self.audio_line.set_markup(
                f"<b>Audio</b>  {GLib.markup_escape_text(audio_summary(audio))}"
                "  <span alpha='60%'>(muxed)</span>"
            )
        else:
            self.video_line.set_markup(
                f"<b>Video</b>  {GLib.markup_escape_text(video_summary(video))}"
                f"  <span alpha='60%'>({TYPE_SUFFIX[vtype]})</span>{fallback}"
            )
            self.audio_line.set_markup(
                f"<b>Audio</b>  {GLib.markup_escape_text(audio_summary(audio))}"
                "  <span alpha='60%'>(+ FLAC)</span>"
            )

        if mode != "audio":
            vtext = video_summary(video)
            if fallback:
                vtext += f" · {self.max_height}p unavailable, using {video.height}p"
            self.video_line.set_tooltip_text(vtext)
        self.audio_line.set_tooltip_text(audio_summary(audio))

        custom = self._link_titles.get(self._selected_url)
        vname = display_filename(self._probe, vtype, "mp4", base=custom)
        aname = display_filename(self._probe, None, "flac", base=custom)
        prefix = "<span alpha='70%'>Saves as</span>  "
        if mode == "video":
            self.saved_line.set_markup(prefix + GLib.markup_escape_text(vname))
            tip = vname
        elif mode == "audio":
            self.saved_line.set_markup(prefix + GLib.markup_escape_text(aname))
            tip = aname
        else:
            self.saved_line.set_markup(
                prefix
                + GLib.markup_escape_text(vname)
                + "  <span alpha='70%'>+ matching .flac</span>"
            )
            tip = f"{vname}\n{aname}"
        self.saved_line.set_tooltip_text(tip)

        if video and video.height:
            text = f"{video.height}p"
            if video.fps:
                text += f"  ·  {video.fps:.0f}fps"
            self.badge.set_text(text)
            self.badge.set_visible(True)
        elif mode == "audio" and audio:
            self.badge.set_text(codec_name(audio.acodec))
            self.badge.set_visible(True)
        else:
            self.badge.set_visible(False)

    def _on_play_preview(self, _btn: Gtk.Button) -> None:
        if self._probe is None or self._probe.error:
            return
        mpv = shutil.which("mpv")
        if not mpv:
            self._toast("mpv not found")
            return
        video, audio = self._selected_formats()
        if self.mode == "audio":
            spec = audio.format_id if audio else None
        elif video and audio:
            spec = f"{video.format_id}+{audio.format_id}"
        elif video:
            spec = video.format_id
        else:
            spec = None

        args = [
            mpv,
            "--no-terminal",
            "--really-quiet",
            "--force-window=yes",
            "--geometry=960x540",
            "--title=Media DL preview",
        ]
        if spec:
            args.append(f"--ytdl-format={spec}")
        args.append(self._probe.webpage_url or self._probe.url)
        try:
            subprocess.Popen(
                args,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            self._toast(f"Could not start mpv: {exc}")

    # ------------------------------------------------------------- download
    def _set_track(self, url: str, fraction: float, state: str, detail: str = "") -> None:
        row = self._link_rows.get(url)
        if row is None:
            return
        row.set_progress(fraction, state)
        for css in ("is-run", "is-done", "is-fail"):
            row.remove_css_class(css)
        if state:
            row.add_css_class(state)
        if detail:
            row.set_tooltip_text(detail)

    def _reset_tracks(self) -> None:
        for url in list(self._link_rows):
            self._set_track(url, 0, "")
            row = self._link_rows.get(url)
            if row is not None:
                row.set_tooltip_text("")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.download_btn.set_sensitive(not busy)
        self.stop_btn.set_sensitive(busy)
        self.url_view.set_sensitive(not busy)
        for btn in self._mode_buttons:
            btn.set_sensitive(not busy)
        self.quality_drop.set_sensitive(not busy)
        self.audio_drop.set_sensitive(not busy)
        for dropdown in self._link_dropdowns.values():
            dropdown.set_sensitive(not busy)
        for entry in self._link_entries.values():
            entry.set_sensitive(not busy)

    def _on_stop(self, _btn: Gtk.Button) -> None:
        if self._downloader is not None:
            self._downloader.cancel()
            self.stop_btn.set_sensitive(False)

    def _on_download(self, _btn: Gtk.Button) -> None:
        if self._busy:
            return
        start, end = self.url_buffer.get_bounds()
        urls = parse_urls(self.url_buffer.get_text(start, end, False))
        if not urls:
            self._toast("Add at least one URL")
            return

        mode = self.mode
        for url in urls:
            self._commit_name(url)
        self._reset_tracks()
        self.progress.set_fraction(0.0)
        self._set_busy(True)

        self._downloader = MediaDownloader(
            Path(self.cfg["output_dir"]),
            mode=mode,
            max_height=self.max_height,
            audio_quality=self.audio_quality,
            progress=self._on_progress,
        )
        self._total_urls = len(urls)
        self._done_urls = 0
        types = {url: self._effective_type(url) for url in urls}
        titles = {url: self._link_titles.get(url) for url in urls}
        dl = self._downloader
        threading.Thread(
            target=self._run_batch,
            args=(dl, urls, mode, types, titles),
            daemon=True,
        ).start()

    def _target_dir(self) -> str:
        return str(self.cfg["output_dir"])

    def _on_progress(self, url: str, kind: str, message: str, pct: float | None) -> None:
        # yt-dlp fires progress hooks many times a second. Keep only the latest
        # snapshot and repaint once per main-loop turn instead of flooding it.
        self._progress_latest = (url, kind, message, pct)
        if not self._progress_scheduled:
            self._progress_scheduled = True
            GLib.idle_add(self._flush_progress)

    def _flush_progress(self) -> bool:
        self._progress_scheduled = False
        latest = self._progress_latest
        self._progress_latest = None
        if latest is not None:
            self._update_progress(*latest)
        return False

    def _update_progress(self, url: str, kind: str, message: str, pct: float | None) -> bool:
        if not self._busy:
            return False
        phases = MODES[self.mode]
        phase = phases.index(kind) if kind in phases else 0
        total = len(phases) or 1
        slot = (phase + (pct or 0) / 100.0) / total

        state = getattr(self, "_done_urls", 0)
        total_urls = getattr(self, "_total_urls", 1)
        frac = (state + slot) / max(total_urls, 1)
        self.progress.set_fraction(min(max(frac, 0.0), 1.0))

        if message.startswith("Saved"):
            self._set_track(url, 1.0, "is-done")
        elif message.startswith("Failed") or message == "Error":
            self._set_track(url, 1.0, "is-fail", message)
        elif message == "Cancelled":
            self._set_track(url, slot, "")
        else:
            self._set_track(url, slot, "is-run")
        return False

    def _run_batch(
        self,
        dl: MediaDownloader,
        urls: list[str],
        mode: str,
        types: dict[str, str],
        titles: dict[str, str | None],
    ) -> None:
        results = []
        for url in urls:
            if dl.cancelled:
                break
            result = dl.download_one(
                url, types.get(url, "video"), titles.get(url)
            )
            results.append(result)
            self._done_urls += 1
        GLib.idle_add(self._finish_batch, results, mode, dl.cancelled)

    def _finish_batch(self, results: list, mode: str, cancelled: bool) -> bool:
        ok = sum(1 for r in results if r.ok)
        fail = len(results) - ok
        if not cancelled:
            self.progress.set_fraction(1.0)
        for r in results:
            if r.ok:
                self._set_track(r.url, 1.0, "is-done")
            elif r.error != "Cancelled":
                self._set_track(r.url, 1.0, "is-fail", r.error or "Failed")
        self._set_busy(False)
        self._downloader = None
        if cancelled:
            self._toast("Stopped")
        elif fail == 0:
            self._toast(f"Saved {ok} item(s)")
        else:
            self._toast(f"{ok} ok, {fail} failed")
        return False

    def _on_open_folder(self, _btn: Gtk.Button) -> None:
        path = self._target_dir()
        try:
            subprocess.Popen(
                ["xdg-open", path],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            self._toast("Could not open folder")


class MediaDlApp(Adw.Application):
    def __init__(self, initial_text: str = "") -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self._initial_text = initial_text
        self.connect("startup", self._on_startup)
        self.connect("activate", self._on_activate)

    def _on_startup(self, _app: Adw.Application) -> None:
        init_theming()
        provider = Gtk.CssProvider()
        try:
            provider.load_from_data(APP_CSS)
            display = Gdk.Display.get_default()
            if display is not None:
                Gtk.StyleContext.add_provider_for_display(
                    display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                )
        except GLib.Error as exc:
            print(f"APP_CSS failed: {exc}")

    def _on_activate(self, app: Adw.Application) -> None:
        init_theming()
        win = app.get_active_window()
        if win is None:
            win = MediaDlWindow(app, self._initial_text)
        win.present()


def main(urls: list[str] | None = None) -> int:
    Adw.init()
    return MediaDlApp("\n".join(urls or [])).run(None)

