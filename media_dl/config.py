"""Tiny JSON config so the app remembers quality/folder choices."""

from __future__ import annotations

import json
from pathlib import Path

CONFIG_PATH = Path.home() / ".config/media-dl/config.json"

DEFAULTS: dict[str, object] = {
    "mode": "video",
    "video_quality": "1080p",
    "audio_quality": "best",
    "output_dir": str(Path.home() / "Videos"),
}


def load() -> dict:
    data = dict(DEFAULTS)
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            data.update({k: v for k, v in raw.items() if k in DEFAULTS})
            # Older versions stored separate video/audio folders.
            if "output_dir" not in raw:
                legacy = raw.get("video_dir") or raw.get("audio_dir")
                if legacy:
                    data["output_dir"] = legacy
    except (OSError, ValueError):
        pass
    return data


def save(data: dict) -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps({k: data.get(k, v) for k, v in DEFAULTS.items()}, indent=2)
            + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass
