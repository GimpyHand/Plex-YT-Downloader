"""yt-dlp backed downloads: merged video, hi-res FLAC audio, or both."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yt_dlp

from media_dl.preview import clean_name, cookie_browser, split_artist_title, suffix_for
from media_dl.tagging import apply_flac_tags, tags_from_info

# url, kind ("video" | "audio"), status message, percent 0-100 or None
ProgressCb = Callable[[str, str, str, float | None], None]

MODES = {
    "video": ("video",),
    "audio": ("audio",),
    "both": ("video", "audio"),
}

QUALITIES = {
    "best": None,
    "2160p": 2160,
    "1440p": 1440,
    "1080p": 1080,
    "720p": 720,
    "480p": 480,
}

AUDIO_QUALITIES = {
    "best": None,
    "high": 192,
    "standard": 128,
}

_VIDEO_TEMPLATE = (
    "%(artist,creator,uploader,channel,uploader_id)s - "
    "%(track,title,id)s{suffix}.%(ext)s"
)
_AUDIO_TEMPLATE = (
    "%(artist,creator,uploader,channel,uploader_id)s - "
    "%(track,title,id)s.%(ext)s"
)


@dataclass
class DownloadResult:
    url: str
    ok: bool
    paths: list[Path] = field(default_factory=list)
    error: str | None = None


@dataclass
class DownloadBatch:
    results: list[DownloadResult] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.results if not r.ok)


_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def parse_urls(text: str) -> list[str]:
    """Pull unique http(s) URLs from freeform text (one per line or mixed)."""
    seen: set[str] = set()
    urls: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        found = _URL_RE.findall(line)
        candidates = found if found else ([line] if line.startswith(("http://", "https://")) else [])
        for url in candidates:
            url = url.rstrip(".,);]>\"'")
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def video_format(max_height: int | None, audio_abr: int | None = None) -> str:
    """Best video up to max_height + best audio, preferring H.264 (MP4-friendly)."""
    cap = f"[height<={max_height}]" if max_height else ""
    acap = f"[abr<={int(audio_abr * 1.05)}]" if audio_abr else ""
    return (
        f"bestvideo{cap}[vcodec^=avc1]+bestaudio[ext=m4a]{acap}/"
        f"bestvideo{cap}[vcodec^=avc1]+bestaudio{acap}/"
        f"bestvideo{cap}+bestaudio[ext=m4a]{acap}/"
        f"bestvideo{cap}+bestaudio{acap}/"
        f"bestvideo{cap}+bestaudio/"
        f"best{cap}/best"
    )


def audio_format(audio_abr: int | None = None) -> str:
    cap = f"[abr<={int(audio_abr * 1.05)}]" if audio_abr else ""
    return f"bestaudio{cap}/bestaudio/best"


def _audio_ext(info: dict) -> str:
    for rd in info.get("requested_downloads") or []:
        p = rd.get("filepath") or rd.get("_filename")
        if p:
            return Path(p).suffix.lstrip(".").lower() or "flac"
    return "flac"


def _final_paths(info: dict, ydl: yt_dlp.YoutubeDL, want: str) -> list[Path]:
    """Resolve the real output file(s) after postprocessing."""
    paths: list[Path] = []

    def add(value: str | None) -> None:
        if not value:
            return
        p = Path(value)
        if p.is_file() and p not in paths:
            paths.append(p)

    for rd in info.get("requested_downloads") or []:
        add(rd.get("filepath"))
        if want == "audio":
            ext = _audio_ext(info)
            add(str(Path(rd.get("filepath") or "").with_suffix(f".{ext}")))
    add(info.get("filepath"))
    if not paths:
        try:
            prep = Path(ydl.prepare_filename(info))
            for ext in (".mp4", ".mkv", ".webm", ".flac"):
                add(str(prep.with_suffix(ext)))
        except Exception:  # noqa: BLE001 — prepare_filename is best-effort
            pass
    return paths


def _normalize_names(info: dict) -> None:
    """Strip a duplicated artist prefix so video and FLAC share a clean base."""
    artist = (
        info.get("artist")
        or info.get("creator")
        or info.get("album_artist")
        or info.get("uploader")
        or info.get("channel")
        or ""
    )
    title = info.get("title") or info.get("track") or ""
    artist, title = split_artist_title(artist, title)
    if title:
        info["title"] = title
        info["track"] = title


def _named_ydl(title_override: str | None = None):
    """Apply Plex-friendly naming before yt-dlp builds output filenames."""

    class _NamedYDL(yt_dlp.YoutubeDL):
        def process_info(self, info):
            _normalize_names(info)
            if title_override:
                info["title"] = title_override
                info["track"] = title_override
            return super().process_info(info)

    return _NamedYDL


def _literal_outtmpl(out_dir: Path, name: str, suffix: str = "") -> str:
    """Use an edited name verbatim (yt-dlp treats % as a template escape)."""
    stem = clean_name(name).replace("%", "%%")
    return str(out_dir / f"{stem}{suffix}.%(ext)s")


class MediaDownloader:
    def __init__(
        self,
        out_dir: Path,
        *,
        mode: str = "video",
        max_height: int | None = 1080,
        audio_quality: str = "best",
        progress: ProgressCb | None = None,
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"unknown mode: {mode}")
        self.out_dir = Path(out_dir).expanduser().resolve()
        self.mode = mode
        self.max_height = max_height
        self.audio_abr = AUDIO_QUALITIES.get(audio_quality, None)
        self.progress = progress or (lambda *_: None)
        self._current_url = ""
        self._current_kind = "video"
        self._cancel = threading.Event()

    # ------------------------------------------------------------------ hooks
    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def _hook(self, d: dict) -> None:
        if self._cancel.is_set():
            raise yt_dlp.utils.DownloadCancelled("Cancelled")

        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            pct = (100.0 * done / total) if total else None
            speed = d.get("_speed_str") or ""
            eta = d.get("_eta_str") or ""
            msg = "Downloading"
            if speed:
                msg += f" · {speed}"
            if eta:
                msg += f" · ETA {eta}"
            self.progress(self._current_url, self._current_kind, msg, pct)
        elif status == "finished":
            msg = "Merging…" if self._current_kind == "video" else "Converting to FLAC…"
            self.progress(self._current_url, self._current_kind, msg, 100.0)
        elif status == "error":
            self.progress(self._current_url, self._current_kind, "Error", None)

    def _postprocessor_hook(self, d: dict) -> None:
        if d.get("status") != "finished":
            return
        name = str(d.get("postprocessor") or "")
        if name in ("MoveFiles", "FFmpegMerger", "FFmpegExtractAudio"):
            msg = "Finalizing…" if self._current_kind == "video" else "Writing metadata…"
            self.progress(self._current_url, self._current_kind, msg, 100.0)

    # ------------------------------------------------------------------- opts
    def _common(self) -> dict:
        opts = {
            "noplaylist": False,
            "ignoreerrors": False,
            "quiet": True,
            "no_warnings": True,
            "prefer_ffmpeg": True,
            "keepvideo": False,
            "noprogress": True,
            "concurrent_fragment_downloads": 4,
            "progress_hooks": [self._hook],
            "postprocessor_hooks": [self._postprocessor_hook],
            "retries": 5,
            "fragment_retries": 5,
        }
        cookies = cookie_browser()
        if cookies is not None:
            opts["cookiesfrombrowser"] = cookies
        return opts

    def _video_opts(self, suffix: str, name: str | None = None) -> dict:
        if name:
            outtmpl = _literal_outtmpl(self.out_dir, name, suffix)
        else:
            outtmpl = str(self.out_dir / _VIDEO_TEMPLATE.format(suffix=suffix))
        opts = self._common()
        if name:
            opts["noplaylist"] = True
        opts.update(
            {
                "format": video_format(self.max_height, self.audio_abr),
                "outtmpl": {"default": outtmpl},
                "paths": {"home": str(self.out_dir)},
                "merge_output_format": "mp4",
                "postprocessors": [{"key": "FFmpegMetadata", "add_metadata": True}],
            }
        )
        return opts

    def _audio_opts(self, name: str | None = None) -> dict:
        if name:
            outtmpl = _literal_outtmpl(self.out_dir, name)
        else:
            outtmpl = str(self.out_dir / _AUDIO_TEMPLATE)
        opts = self._common()
        if name:
            opts["noplaylist"] = True
        opts.update(
            {
                "format": audio_format(self.audio_abr),
                "outtmpl": {"default": outtmpl},
                "paths": {"home": str(self.out_dir)},
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "flac",
                        "preferredquality": "0",
                    },
                    {"key": "FFmpegMetadata", "add_metadata": True},
                ],
                "writethumbnail": True,
            }
        )
        return opts

    # --------------------------------------------------------------- helpers
    def _enrich(self, info: dict) -> dict:
        tags = tags_from_info(info)
        if tags.get("ARTIST") and not info.get("artist"):
            info["artist"] = tags["ARTIST"]
        if tags.get("TITLE") and not info.get("track"):
            info["track"] = tags["TITLE"]
        if tags.get("ALBUM") and not info.get("album"):
            info["album"] = tags["ALBUM"]
        return info

    def _tag_flac(self, path: Path, info: dict) -> None:
        if path.suffix.lower() != ".flac" or not path.is_file():
            return
        self.progress(self._current_url, "audio", "Writing metadata…", 100.0)
        apply_flac_tags(path, self._enrich(info), embed_cover=True)

    def _one_kind(
        self,
        url: str,
        kind: str,
        video_suffix: str = "-video",
        name: str | None = None,
    ) -> list[Path]:
        self._current_kind = kind
        if self._cancel.is_set():
            raise yt_dlp.utils.DownloadCancelled("Cancelled")

        if kind == "video":
            opts = self._video_opts(video_suffix, name)
        else:
            opts = self._audio_opts(name)
        out_dir = self.out_dir
        out_dir.mkdir(parents=True, exist_ok=True)

        with _named_ydl(name)(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                raise RuntimeError("No media found")
            _normalize_names(info)
            if name:
                info["title"] = name
                info["track"] = name

            if info.get("_type") == "playlist":
                entries = [e for e in (info.get("entries") or []) if e]
                if not entries:
                    raise RuntimeError("Empty playlist")
                paths: list[Path] = []
                for entry in entries:
                    _normalize_names(entry)
                    found = _final_paths(entry, ydl, kind)
                    if kind == "audio":
                        for p in found:
                            self._tag_flac(p, entry)
                    paths.extend(found)
                return paths

            paths = _final_paths(info, ydl, kind)
            if kind == "audio":
                for p in paths:
                    self._tag_flac(p, info)
            return paths

    # -------------------------------------------------------------- downloads
    def download_one(
        self, url: str, video_type: str = "video", name: str | None = None
    ) -> DownloadResult:
        self._current_url = url
        kinds = MODES[self.mode]
        suffix = suffix_for(video_type)
        clean = clean_name(name) if name else None
        found: list[Path] = []
        try:
            for kind in kinds:
                self.progress(url, kind, "Starting…", 0.0)
                found.extend(self._one_kind(url, kind, suffix, clean))
            if not found:
                return DownloadResult(url, False, error="No output file produced")
            names = ", ".join(p.name for p in found)
            self.progress(url, kinds[-1], f"Saved {names}", 100.0)
            return DownloadResult(url, True, paths=found)
        except yt_dlp.utils.DownloadCancelled:
            self.progress(url, self._current_kind, "Cancelled", None)
            return DownloadResult(url, False, paths=found, error="Cancelled")
        except Exception as exc:  # noqa: BLE001 — surface any yt-dlp failure
            err = str(exc).strip() or type(exc).__name__
            if err.startswith("ERROR:"):
                err = err[6:].strip()
            self.progress(url, self._current_kind, f"Failed: {err}", None)
            return DownloadResult(url, False, paths=found, error=err)

    def download_many(
        self, urls: list[str], video_types: dict[str, str] | None = None
    ) -> DownloadBatch:
        video_types = video_types or {}
        batch = DownloadBatch()
        for url in urls:
            if self._cancel.is_set():
                break
            batch.results.append(self.download_one(url, video_types.get(url, "video")))
        return batch
