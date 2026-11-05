"""Entry points: GUI by default, CLI with URLs or --cli."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from media_dl.preview import TYPE_KEYS

QUALITY_CHOICES = ["best", "2160p", "1440p", "1080p", "720p", "480p"]
AUDIO_CHOICES = ["best", "high", "standard"]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    parser = argparse.ArgumentParser(
        prog="media-dl",
        description="Download merged 1080p video, hi-res FLAC audio, or both.",
    )
    parser.add_argument("urls", nargs="*", help="One or more media URLs")
    parser.add_argument(
        "-m",
        "--mode",
        choices=["video", "audio", "both"],
        default="video",
        help="Download video (merged), FLAC audio, or both (default: video)",
    )
    parser.add_argument(
        "-q",
        "--quality",
        choices=QUALITY_CHOICES,
        default="1080p",
        help="Maximum video resolution (default: 1080p)",
    )
    parser.add_argument(
        "--audio-quality",
        choices=AUDIO_CHOICES,
        default="best",
        help="Source audio bitrate cap (default: best)",
    )
    parser.add_argument(
        "--video-type",
        choices=["auto"] + TYPE_KEYS,
        default="auto",
        help="Plex video suffix; auto detects from metadata (default: auto)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path.home() / "Videos",
        help="Output directory for video and FLAC (default: ~/Videos)",
    )
    parser.add_argument(
        "-f",
        "--file",
        type=Path,
        help="Read URLs from a text file",
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Force CLI mode (no GUI)",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Open the GUI (default when no URLs given)",
    )
    args = parser.parse_args(argv)

    raw_urls: list[str] = list(args.urls)
    if args.file:
        raw_urls.extend(args.file.read_text(encoding="utf-8").splitlines())

    from media_dl.downloader import MediaDownloader, parse_urls

    urls = parse_urls("\n".join(raw_urls)) if raw_urls else []

    want_gui = args.gui or (not args.cli and not urls and args.file is None)
    if want_gui:
        from media_dl.app import main as gui_main

        return gui_main(urls)

    if not urls:
        parser.error("No URLs provided (pass URLs, -f FILE, or --gui)")

    out_dir = args.output

    def progress(url: str, kind: str, message: str, pct: float | None) -> None:
        short = url if len(url) < 70 else url[:67] + "…"
        prefix = f"[{kind}] " if args.mode == "both" else ""
        if pct is not None and message.startswith("Downloading"):
            print(f"\r{prefix}{message} ({pct:.0f}%) {short}    ", end="", flush=True)
        else:
            print(f"\n{prefix}{message}: {short}" if pct is not None else f"{prefix}{message}: {short}")

    dl = MediaDownloader(
        out_dir,
        mode=args.mode,
        max_height=None if args.quality == "best" else int(args.quality.rstrip("p")),
        audio_quality=args.audio_quality,
        progress=progress,
    )

    types: dict[str, str] = {}
    if args.mode in ("video", "both"):
        if args.video_type == "auto":
            from media_dl.preview import detect_video_type, probe_url

            for url in urls:
                probe = probe_url(url)
                types[url] = (
                    detect_video_type(probe) if not probe.error else None
                ) or "video"
        else:
            types = {url: args.video_type for url in urls}

    batch = dl.download_many(urls, types)
    print()
    for r in batch.results:
        if r.ok:
            for p in r.paths:
                print(f"OK  {p}")
        else:
            print(f"ERR {r.error} — {r.url}", file=sys.stderr)
    return 0 if batch.fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
