"""Lightweight metadata probing for the preview pane (no download)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Plex music-video suffixes: base name must match the audio track.
VIDEO_TYPES: list[tuple[str, str, str]] = [
    ("video", "Video", "-video"),
    ("live", "Live", "-live"),
    ("lyrics", "Lyrics", "-lyrics"),
    ("concert", "Concert", "-concert"),
    ("interview", "Interview", "-interview"),
    ("behindthescenes", "Behind the scenes", "-behindthescenes"),
]
TYPE_SUFFIX = {key: suffix for key, _label, suffix in VIDEO_TYPES}
TYPE_LABEL = {key: label for key, label, _suffix in VIDEO_TYPES}
TYPE_KEYS = [key for key, _label, _suffix in VIDEO_TYPES]

_PATTERNS: list[tuple[str, str]] = [
    ("behindthescenes", r"behind[\s-]the[\s-]scenes|making[\s-]of|\bbts\b"),
    ("interview", r"\binterview\b"),
    ("lyrics", r"\blyrics?\b"),
    ("concert", r"\b(full\s+concert|live\s+concert|concert)\b"),
    ("live", r"\blive\b"),
    ("video", r"\b(music\s+video|official\s+video|video)\b"),
]

# Tags are noisy: almost every music upload is tagged "lyrics" or "video",
# even when it is a plain music video. Only explicit phrases count.
_META_PATTERNS: list[tuple[str, str]] = [
    ("behindthescenes", r"behind[\s-]the[\s-]scenes|\bmaking[\s-]of\b"),
    ("interview", r"\binterview\b"),
    ("concert", r"\b(full|live)\s+concert\b"),
    ("live", r"\blive\s+(?:at|from|in|session|performance)\b|\brecorded\s+live\b"),
    ("video", r"\b(?:official\s+)?music\s+video\b|\bofficial\s+video\b"),
    ("lyrics", r"\blyric(?:s)?\s+video\b|\blyrics?\s+version\b"),
]

# Descriptions are noisier still (full lyrics pasted in, links, credits), so
# only use explicit production phrases and never infer "lyrics" from them.
_DESC_PATTERNS: list[tuple[str, str]] = [
    ("behindthescenes", r"behind[\s-]the[\s-]scenes|\bmaking[\s-]of\b"),
    ("interview", r"\binterview\b"),
    ("concert", r"\b(full|live)\s+concert\b"),
    ("live", r"\blive\s+(?:at|from|performance|session)\b|\brecorded\s+live\b"),
    ("video", r"\b(?:official\s+)?music\s+video\b|\bofficial\s+video\b"),
]


@dataclass
class Format:
    format_id: str
    ext: str = ""
    vcodec: str | None = None
    acodec: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    tbr: float | None = None
    abr: float | None = None
    asr: int | None = None
    channels: int | None = None
    filesize: int | None = None
    note: str = ""

    @property
    def is_video(self) -> bool:
        return bool(self.vcodec) and self.vcodec != "none"

    @property
    def is_audio(self) -> bool:
        return bool(self.acodec) and self.acodec != "none" and not self.is_video


@dataclass
class Probe:
    url: str
    title: str = ""
    uploader: str = ""
    artist: str = ""
    duration: float | None = None
    thumbnail: str = ""
    webpage_url: str = ""
    is_live: bool = False
    playlist_count: int = 0
    description: str = ""
    genre: str = ""
    tags: list[str] = field(default_factory=list)
    formats: list[Format] = field(default_factory=list)
    video: Format | None = None
    audio: Format | None = None
    error: str | None = None


def _match(text: str, patterns: list[tuple[str, str]]) -> str | None:
    for key, pattern in patterns:
        if re.search(pattern, text):
            return key
    return None


def _match_any(texts: list[str], patterns: list[tuple[str, str]]) -> str | None:
    """Check each pattern against each text, so tags never blend together."""
    for key, pattern in patterns:
        for text in texts:
            if text and re.search(pattern, text):
                return key
    return None


def detect_video_type(probe: Probe) -> str | None:
    """Guess the Plex music-video type from the title first, then metadata.

    Weak signals are deliberately ignored: a lone "lyrics" tag or the word
    "video" in a description does not make a regular video a lyric video.
    """
    found = _match((probe.title or "").lower(), _PATTERNS)
    if found:
        return found

    meta = [probe.genre or "", *probe.tags]
    found = _match_any([m.lower() for m in meta], _META_PATTERNS)
    if found:
        return found

    desc = (probe.description or "")[:1500].lower().replace("\n", " ")
    return _match(desc, _DESC_PATTERNS)


def type_from_value(value: str | None) -> str:
    """Accept a suffix ('-live'), key ('live') or label ('Live')."""
    if not value:
        return "video"
    raw = str(value).strip().lower()
    if raw in TYPE_SUFFIX:
        return raw
    for key, suffix in TYPE_SUFFIX.items():
        if raw == suffix or raw == TYPE_LABEL[key].lower():
            return key
    return "video"


def suffix_for(value: str | None) -> str:
    return TYPE_SUFFIX[type_from_value(value)]


def split_artist_title(artist: str | None, title: str | None) -> tuple[str, str]:
    """Avoid 'Artist - Artist - Title' when the metadata title already has it."""
    artist = (artist or "").strip()
    title = (title or "").strip()
    if artist and title.lower().startswith(artist.lower()):
        tail = title[len(artist):]
        match = re.match(r"\s*[-–—:|·]\s*", tail)
        if match:
            rest = tail[match.end():].strip()
            if rest:
                return artist, rest
    return artist, title


def display_base(probe: Probe) -> str:
    artist = probe.artist or probe.uploader or ""
    artist, title = split_artist_title(artist, probe.title)
    return f"{artist} - {title}" if artist and title else (title or artist)


def clean_name(name: str) -> str:
    """Make an edited name safe for a filename on Linux and Windows shares."""
    cleaned = re.sub(r'[/\\:*?"<>|\x00-\x1f]', "_", name or "")
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"_{2,}", "_", cleaned)
    return cleaned.strip(" ._")


_QUOTES = set("\"'`´«»“”„‟‘’‚‹›")


def tidy_title(name: str) -> str:
    """Collapse the gaps a removal leaves: double spaces, empty separators."""
    cleaned = re.sub(r"\s+", " ", name or "")
    cleaned = re.sub(r"\(\s*\)|\[\s*\]|\{\s*\}", "", cleaned)
    cleaned = re.sub(r"(?:\s*[-–—|]\s*){2,}", " - ", cleaned)
    cleaned = re.sub(r"^\s*[-–—|]\s*", "", cleaned)
    cleaned = re.sub(r"\s*[-–—|]\s*$", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _wildcard_pattern(pattern: str) -> re.Pattern[str]:
    """Turn a strip pattern into a regex. * is the only wildcard.

    A star at the end eats the rest of the title (`ft. *`).
    A star before more text stops at that text (` [*]`).
    """
    pieces = pattern.split("*")
    last = len(pieces) - 1
    body: list[str] = []
    for index, piece in enumerate(pieces):
        body.append(re.escape(piece))
        if index == last:
            break
        star_ends_the_pattern = pieces[index + 1] == "" and index + 1 == last
        body.append(".*" if star_ends_the_pattern else ".*?")
    return re.compile("".join(body), re.IGNORECASE | re.DOTALL)


def strip_term(name: str, term: str) -> str:
    """Remove a term, symbol, or wildcard pattern from a title.

    A quote character clears every kind of quote. * is a wildcard:
    `ft. *` drops that and everything after it, ` [*]` drops a bracket tag.
    """
    needle = (term or "").strip()
    if not needle:
        return name or ""
    if "*" not in needle and all(ch in _QUOTES for ch in needle):
        cleaned = "".join(ch for ch in (name or "") if ch not in _QUOTES)
    elif "*" in needle:
        cleaned = _wildcard_pattern(needle).sub("", name or "")
    else:
        cleaned = re.sub(re.escape(needle), "", name or "", flags=re.IGNORECASE)
    return tidy_title(cleaned)


def display_filename(
    probe: Probe, video_type: str | None, ext: str, base: str | None = None
) -> str:
    base = clean_name(base or display_base(probe)) or "Untitled"
    suffix = suffix_for(video_type) if video_type else ""
    return f"{base}{suffix}.{ext}"


def _num(value) -> float | None:
    try:
        f = float(value)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _format(raw: dict) -> Format:
    return Format(
        format_id=str(raw.get("format_id") or ""),
        ext=str(raw.get("ext") or ""),
        vcodec=raw.get("vcodec"),
        acodec=raw.get("acodec"),
        width=_int(raw.get("width")),
        height=_int(raw.get("height")),
        fps=_num(raw.get("fps")),
        tbr=_num(raw.get("tbr")),
        abr=_num(raw.get("abr")),
        asr=_int(raw.get("asr")),
        channels=_int(raw.get("audio_channels")),
        filesize=_int(raw.get("filesize") or raw.get("filesize_approx")),
        note=str(raw.get("format_note") or ""),
    )


def pick_video(probe: Probe, max_height: int | None) -> Format | None:
    """Mirror the yt-dlp format selector: H.264 first, best height/fps within cap."""
    videos = [f for f in probe.formats if f.is_video]
    if max_height:
        videos = [f for f in videos if (f.height or 0) <= max_height]
    if not videos:
        return None
    avc = [f for f in videos if (f.vcodec or "").startswith("avc1")]
    pool = avc or videos
    return max(pool, key=lambda f: (f.height or 0, f.fps or 0, f.tbr or 0, f.filesize or 0))


def pick_audio(
    probe: Probe, max_abr: float | None, *, prefer_m4a: bool = False
) -> Format | None:
    audios = [f for f in probe.formats if f.is_audio]
    if max_abr:
        cap = max_abr * 1.05  # nominal bitrates run a little over the label
        capped = [f for f in audios if (f.abr or 0) <= cap]
        audios = capped or audios
    if prefer_m4a:
        m4a = [f for f in audios if f.ext == "m4a"]
        audios = m4a or audios
    if not audios:
        # Some sites expose muxed-only streams.
        return next((f for f in probe.formats if f.acodec and f.acodec != "none"), None)
    return max(audios, key=lambda f: (f.abr or 0, f.tbr or 0, f.filesize or 0))


def cookie_browser() -> tuple[str, str, str, None] | None:
    """Browser profile whose YouTube login yt-dlp should reuse.

    Hyprland is not GNOME, so yt-dlp would skip the keyring and YouTube
    answers with a bot check. Brave on this machine stores that login with
    the GNOME secret service.
    """
    home = Path.home()
    candidates = (
        ("brave", home / ".config/BraveSoftware/Brave-Origin/Default"),
        ("brave", home / ".config/BraveSoftware/Brave-Browser/Default"),
        ("chromium", home / ".config/chromium/Default"),
        ("chrome", home / ".config/google-chrome/Default"),
    )
    for browser, profile in candidates:
        if (profile / "Cookies").is_file():
            return (browser, str(profile), "GNOMEKEYRING", None)
    return None


def _cookie_args() -> list[str]:
    spec = cookie_browser()
    if spec is None:
        return []
    browser, profile, keyring, _container = spec
    return ["--cookies-from-browser", f"{browser}+{keyring}:{profile}"]


def _ytdlp_command() -> list[str]:
    binary = shutil.which("yt-dlp")
    if binary:
        return [binary]
    return [sys.executable, "-m", "yt_dlp"]


def _extract_json(url: str, *, playlist: bool) -> dict:
    """Run yt-dlp in a child process.

    Probing in-process loaded the whole yt-dlp extraction stack into the GTK
    main process; with several links queued those threads held the GIL and the
    window stopped repainting long enough for the compositor to offer to kill
    it. A subprocess keeps all of that work off the UI thread.
    """
    cmd = [
        *_ytdlp_command(),
        "--dump-single-json",
        "--skip-download",
        "--no-warnings",
        "--ignore-config",
        "--no-progress",
        "--socket-timeout",
        "20",
        *_cookie_args(),
    ]
    if not playlist:
        cmd.append("--no-playlist")
    cmd.append(url)

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        msg = (proc.stderr or proc.stdout or "").strip()
        if not msg:
            msg = f"yt-dlp exited with code {proc.returncode}"
        raise RuntimeError(msg.splitlines()[-1])

    data = json.loads(proc.stdout)
    if not isinstance(data, dict):
        raise RuntimeError("No media found")
    return data


def probe_url(url: str, *, playlist: bool = False) -> Probe:
    """Fetch metadata (and pick formats) without downloading."""
    try:
        info = _extract_json(url, playlist=playlist)
        if not info:
            return Probe(url=url, error="No media found")

        count = 0
        if info.get("_type") == "playlist":
            entries = [e for e in (info.get("entries") or []) if e]
            count = len(entries)
            if not entries:
                return Probe(url=url, error="Empty playlist")
            info = entries[0]
            if info.get("_type") == "playlist":
                entries = [e for e in (info.get("entries") or []) if e]
                if not entries:
                    return Probe(url=url, error="Empty playlist")
                info = entries[0]

        probe = Probe(
            url=url,
            title=str(info.get("title") or ""),
            uploader=str(
                info.get("uploader")
                or info.get("channel")
                or info.get("artist")
                or ""
            ),
            artist=str(
                info.get("artist")
                or info.get("creator")
                or info.get("album_artist")
                or info.get("uploader")
                or info.get("channel")
                or ""
            ),
            duration=_num(info.get("duration")),
            thumbnail=str(info.get("thumbnail") or ""),
            webpage_url=str(info.get("webpage_url") or url),
            is_live=bool(info.get("is_live")),
            playlist_count=count,
            description=str(info.get("description") or ""),
            genre=str(info.get("genre") or ""),
            tags=[str(t) for t in (info.get("tags") or [])][:40],
            formats=[_format(f) for f in (info.get("formats") or [])],
        )
        probe.video = pick_video(probe, 1080)
        probe.audio = pick_audio(probe, None, prefer_m4a=True)
        return probe
    except subprocess.TimeoutExpired:
        return Probe(url=url, error="Timed out reading metadata")
    except Exception as exc:  # noqa: BLE001 — preview must never crash the app
        msg = str(exc).strip() or type(exc).__name__
        if msg.startswith("ERROR:"):
            msg = msg[6:].strip()
        return Probe(url=url, error=msg)


def fetch_thumbnail(url: str, *, timeout: float = 12.0) -> bytes | None:
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(8 * 1024 * 1024)
            return data or None
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ display
def fmt_size(num: int | float | None) -> str:
    if not num:
        return "?"
    value = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "live"
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_fps(fps: float | None) -> str:
    if not fps:
        return ""
    return f"{fps:.0f} fps" if abs(fps - round(fps)) < 0.01 else f"{fps:.2f} fps"


def codec_name(codec: str | None) -> str:
    if not codec or codec == "none":
        return "?"
    codec = codec.split(".")[0]
    names = {
        "avc1": "H.264",
        "h264": "H.264",
        "hev1": "HEVC",
        "hvc1": "HEVC",
        "vp9": "VP9",
        "vp09": "VP9",
        "av01": "AV1",
        "av1": "AV1",
        "opus": "Opus",
        "mp4a": "AAC",
        "aac": "AAC",
        "vorbis": "Vorbis",
        "flac": "FLAC",
    }
    return names.get(codec.lower(), codec.upper())


def video_summary(fmt: Format | None) -> str:
    if fmt is None:
        return "No video stream found"
    bits = []
    if fmt.width and fmt.height:
        bits.append(f"{fmt.width}×{fmt.height}")
    elif fmt.height:
        bits.append(f"{fmt.height}p")
    if fmt.fps:
        bits.append(fmt_fps(fmt.fps))
    bits.append(codec_name(fmt.vcodec))
    if fmt.filesize:
        bits.append(f"≈ {fmt_size(fmt.filesize)}")
    return " · ".join(bits)


def audio_summary(fmt: Format | None) -> str:
    if fmt is None:
        return "No audio stream found"
    bits = [codec_name(fmt.acodec)]
    if fmt.abr:
        bits.append(f"{fmt.abr:.0f} kbps")
    if fmt.asr:
        bits.append(f"{fmt.asr / 1000:.1f} kHz".replace(".0 kHz", " kHz"))
    channels = {1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}.get(fmt.channels or 0)
    if channels:
        bits.append(channels)
    if fmt.filesize:
        bits.append(f"≈ {fmt_size(fmt.filesize)}")
    return " · ".join(bits)
