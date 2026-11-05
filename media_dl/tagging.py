"""Write FLAC Vorbis comments + cover art via metaflac (no mutagen needed)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any


def _first(*values: Any) -> str | None:
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None


def _year(info: dict) -> str | None:
    for key in ("release_year", "upload_date"):
        raw = info.get(key)
        if not raw:
            continue
        s = str(raw)
        if len(s) >= 4 and s[:4].isdigit():
            return s[:4]
    return None


def tags_from_info(info: dict) -> dict[str, str]:
    """Map yt-dlp info dict → Vorbis comment names music apps expect."""
    artist = _first(
        info.get("artist"),
        info.get("album_artist"),
        info.get("creator"),
        info.get("uploader"),
        info.get("channel"),
        info.get("uploader_id"),
    )
    title = _first(info.get("track"), info.get("title"), info.get("id"))
    album = _first(info.get("album"), info.get("playlist_title"), info.get("playlist"))
    albumartist = _first(info.get("album_artist"), artist)
    genre = _first(info.get("genre"))
    track = _first(info.get("track_number"), info.get("playlist_index"))
    date = _year(info)
    comment = _first(info.get("description"))
    if comment and len(comment) > 500:
        comment = comment[:497] + "…"
    webpage = _first(info.get("webpage_url"), info.get("original_url"), info.get("url"))

    tags: dict[str, str] = {}
    if title:
        tags["TITLE"] = title
    if artist:
        tags["ARTIST"] = artist
    if album:
        tags["ALBUM"] = album
    if albumartist:
        tags["ALBUMARTIST"] = albumartist
    if date:
        tags["DATE"] = date
    if genre:
        tags["GENRE"] = genre
    if track:
        tags["TRACKNUMBER"] = str(track)
    if comment:
        tags["COMMENT"] = comment
    if webpage:
        tags["URL"] = webpage
    return tags


def _thumb_candidates(flac: Path, info: dict) -> list[Path]:
    stems = {flac.stem, flac.with_suffix("").stem}
    # yt-dlp may leave thumb next to final or pre-extract name
    prepared = info.get("filename") or info.get("_filename")
    if prepared:
        stems.add(Path(prepared).stem)
    thumbs: list[Path] = []
    for stem in stems:
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            p = flac.with_name(stem + ext)
            if p.is_file():
                thumbs.append(p)
    # Also common: same dir, any thumb written for this download
    for key in ("thumbnails",):
        pass
    return thumbs


def apply_flac_tags(flac: Path, info: dict, *, embed_cover: bool = True) -> None:
    """Set Vorbis tags (and optional cover) so library apps pick up the file."""
    metaflac = shutil.which("metaflac")
    if not metaflac:
        return
    if not flac.is_file():
        return

    tags = tags_from_info(info)
    if not tags and not embed_cover:
        return

    cmd = [metaflac, "--remove-all-tags"]
    for k, v in tags.items():
        # metaflac tag values must not contain raw newlines in argv cleanly —
        # replace control chars.
        clean = v.replace("\0", "").replace("\r", " ").replace("\n", " ").strip()
        if clean:
            cmd.append(f"--set-tag={k}={clean}")
    cmd.append(str(flac))
    subprocess.run(cmd, check=False, capture_output=True)

    if not embed_cover:
        return

    # Prefer ffmpeg for webp→embed; metaflac wants JPEG/PNG typically.
    thumb = None
    for cand in _thumb_candidates(flac, info):
        thumb = cand
        break
    if thumb is None:
        return

    img = thumb
    if thumb.suffix.lower() == ".webp":
        png = thumb.with_suffix(".png")
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            r = subprocess.run(
                [ffmpeg, "-y", "-i", str(thumb), str(png)],
                capture_output=True,
            )
            if r.returncode == 0 and png.is_file():
                img = png
            else:
                return
        else:
            return

    # Replace existing pictures then import
    subprocess.run(
        [metaflac, "--remove", "--block-type=PICTURE", str(flac)],
        capture_output=True,
    )
    subprocess.run(
        [metaflac, f"--import-picture-from={img}", str(flac)],
        capture_output=True,
    )

    # Clean leftover thumbnails
    for cand in _thumb_candidates(flac, info):
        try:
            cand.unlink(missing_ok=True)
        except OSError:
            pass
    png = thumb.with_suffix(".png") if thumb else None
    if png and png != img and png.is_file() and png.suffix == ".png":
        try:
            png.unlink(missing_ok=True)
        except OSError:
            pass
