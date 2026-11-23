"""Lightweight metadata probing for the preview pane (no download)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
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
    playlist_urls: list[str] = field(default_factory=list)
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
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u2060"), None)
_DASHES = str.maketrans({c: "-" for c in "–—−‐‑‒―"})
_PUNCT = str.maketrans({
    "“": '"', "”": '"', "„": '"', "‟": '"', "«": '"', "»": '"',
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "´": "'", "`": "'", "‹": "'", "›": "'",
    "【": "[", "】": "]", "〖": "[", "〗": "]",
})
_BRACKETS = (("(", ")"), ("[", "]"), ("{", "}"))


def _drop_unmatched(name: str, opener: str, closer: str) -> str:
    stack: list[int] = []
    drop: set[int] = set()
    for index, ch in enumerate(name):
        if ch == opener:
            stack.append(index)
        elif ch == closer:
            if stack:
                stack.pop()
            else:
                drop.add(index)
    drop.update(stack)
    if not drop:
        return name
    return "".join(ch for index, ch in enumerate(name) if index not in drop)


def tidy_title(name: str) -> str:
    """Collapse the gaps a removal leaves: double spaces, empty separators."""
    cleaned = name or ""
    for _ in range(3):
        cleaned = re.sub(r"\s+", " ", cleaned)
        cleaned = re.sub(r"\(\s*\)|\[\s*\]|\{\s*\}", "", cleaned)
        for opener, closer in _BRACKETS:
            cleaned = _drop_unmatched(cleaned, opener, closer)
        cleaned = re.sub(r"(?:\s*[-–—|]\s*){2,}", " - ", cleaned)
        cleaned = re.sub(r"^\s*[-–—|]\s*", "", cleaned)
        cleaned = re.sub(r"\s*[-–—|]\s*$", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _canon_map(text: str) -> tuple[str, list[int]]:
    """Fold lookalike letters and punctuation, and remember original indexes.

    YouTube titles mix spaces, dashes, and quote marks that look the same.
    Matching on this folded text is what makes an exact phrase hit all of them.
    """
    out: list[str] = []
    starts: list[int] = []
    previous_space = False
    for index, ch in enumerate(text or ""):
        if ch in "\u200b\u200c\u200d\ufeff\u2060":
            continue
        folded = unicodedata.normalize("NFKC", ch).translate(_INVISIBLE)
        folded = folded.translate(_DASHES).translate(_PUNCT).casefold()
        if not folded:
            continue
        if folded.isspace() or all(piece.isspace() for piece in folded):
            if previous_space or not out:
                continue
            out.append(" ")
            starts.append(index)
            previous_space = True
            continue
        previous_space = False
        for piece in folded:
            out.append(piece)
            starts.append(index)
    if out and out[-1] == " ":
        out.pop()
        starts.pop()
    starts.append(len(text or ""))
    return "".join(out), starts


def _words(text: str) -> list[str]:
    return re.findall(r"[0-9a-z]+(?:'[0-9a-z]+)?", text)


def _contains_words(text: str, words: list[str]) -> bool:
    pos = 0
    for word in words:
        match = re.search(rf"\b{re.escape(word)}\b", text[pos:])
        if match is None:
            return False
        pos += match.end()
    return True


def _wrapped(term: str) -> tuple[str, str, str] | None:
    if len(term) >= 2 and (term[0], term[-1]) in _BRACKETS:
        return term[0], term[-1], term[1:-1].strip()
    return None


def _literal_spans(text: str, phrase: str) -> list[tuple[int, int]]:
    if not phrase:
        return []
    return [match.span() for match in re.finditer(re.escape(phrase), text)]


def _wildcard_spans(text: str, pattern: str) -> list[tuple[int, int]]:
    pieces = pattern.split("*")
    last = len(pieces) - 1
    body: list[str] = []
    for index, piece in enumerate(pieces):
        if index > 0:
            piece = piece.lstrip(" ")
        if index < last:
            piece = piece.rstrip(" ")
        if piece:
            body.append(re.escape(piece))
        if index == last:
            break
        star_ends = pieces[index + 1].strip(" ") == "" and index + 1 == last
        body.append(".*" if star_ends else ".*?")
    if not body:
        return []
    return [
        match.span()
        for match in re.finditer("".join(body), text, flags=re.DOTALL)
        if match.end() > match.start()
    ]


def _match_spans(text: str, term: str) -> list[tuple[int, int]]:
    if "*" in term:
        return _wildcard_spans(text, term)
    wrapped = _wrapped(term)
    if wrapped is None:
        return _literal_spans(text, term)
    opener, closer, inner = wrapped
    spans = _literal_spans(text, term)
    if inner:
        spans.extend(_literal_spans(text, inner))
    words = _words(inner)
    if not words:
        return spans
    group = re.compile(
        re.escape(opener) + r"[^" + re.escape(opener + closer) + r"]*" + re.escape(closer)
    )
    for match in group.finditer(text):
        if _contains_words(match.group()[1:-1], words):
            spans.append(match.span())
    return spans


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    ordered = sorted((start, end) for start, end in spans if start < end)
    if not ordered:
        return []
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def strip_term(name: str, term: str) -> str:
    """Remove a term, symbol, or wildcard pattern from a title.

    A quote character clears every kind of quote. Spacing, dashes, and quote
    marks do not have to match exactly. A bracketed term such as
    `(Official Video)` removes any same-style tag that contains those words,
    so `(Official Music Video)` goes too. * is a wildcard: `ft. *` drops that
    and everything after it, `[*]` drops a bracket tag, `(*)` drops a
    parenthetical.
    """
    source = name or ""
    needle = (term or "").strip()
    if not needle or not source:
        return source
    if "*" not in needle and all(ch in _QUOTES for ch in needle):
        cleaned = "".join(ch for ch in source if ch not in _QUOTES)
        return source if cleaned == source else tidy_title(cleaned)

    canon, starts = _canon_map(source)
    canon_term, _term_starts = _canon_map(needle)
    if not canon or not canon_term:
        return source
    spans = [
        (starts[start], starts[end])
        for start, end in _merge_spans(_match_spans(canon, canon_term))
    ]
    if not spans:
        return source
    cleaned = source
    for start, end in reversed(spans):
        cleaned = cleaned[:start] + cleaned[end:]
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
    """Optionally reuse a Brave login when explicitly enabled.

    Automatic browser-cookie extraction invokes SecretStorage and can fail on
    otherwise healthy desktop installs. Public URLs should never require that
    dependency, so opt in only with MEDIA_DL_USE_BROWSER_COOKIES=1.
    """
    if os.environ.get("MEDIA_DL_USE_BROWSER_COOKIES") != "1":
        return None
    try:
        import secretstorage  # noqa: F401
    except ImportError:
        return None

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
    if getattr(sys, "frozen", False):
        bundle_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidates = (bundle_dir / "yt-dlp.exe", Path(sys.executable).with_name("yt-dlp.exe"))
        for bundled in candidates:
            if bundled.is_file():
                return [str(bundled)]
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
        # Let yt-dlp expose playlist entries so the queue can turn them into
        # individual tickets with their own title and type controls.
        info = _extract_json(url, playlist=True)
        if not info:
            return Probe(url=url, error="No media found")

        count = 0
        playlist_urls: list[str] = []
        if info.get("_type") == "playlist":
            entries = [e for e in (info.get("entries") or []) if e]
            count = len(entries)
            playlist_urls = [
                str(e.get("webpage_url") or e.get("original_url") or "")
                for e in entries
                if e.get("webpage_url") or e.get("original_url")
            ]
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
            playlist_urls=playlist_urls,
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
