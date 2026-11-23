#!/usr/bin/env bash
# Install Media DL for the current user: CLI symlink + app-menu entry.
# Re-run after moving the checkout; the entry stores the repo's absolute path.
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
bindir="$HOME/.local/bin"
appdir="$HOME/.local/share/applications"

# SecretStorage is optional; without it, public videos still download normally.
missing=()
for pkg in gtk4 libadwaita python-gobject yt-dlp ffmpeg flac; do
    pacman -Qq "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
done
if ((${#missing[@]})); then
    echo "Missing packages: ${missing[*]}" >&2
    echo "Install them with: sudo pacman -S --needed ${missing[*]}" >&2
    exit 1
fi

chmod +x "$root/media-dl"
mkdir -p "$bindir" "$appdir"

ln -sfn "$root/media-dl" "$bindir/media-dl"
# Quote the path so a checkout in a directory with spaces still launches.
sed "s|^Exec=.*|Exec=\"$root/media-dl\" --gui|" \
    "$root/media-dl.desktop" >"$appdir/media-dl.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$appdir" || true
fi

echo "App menu entry: $appdir/media-dl.desktop"
echo "Command line:   $bindir/media-dl"
