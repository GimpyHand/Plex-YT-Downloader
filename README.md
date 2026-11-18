# Media DL

Beta 0.5. Paste one or more video links and save a merged video, a FLAC, or both to the same folder.

## Install

```bash
git clone https://github.com/GimpyHand/Plex-YT-Downloader.git
cd Plex-YT-Downloader
./install.sh
```

That adds **Media DL** to the app menu and puts `media-dl` on your PATH via
`~/.local/bin`. Re-run `./install.sh` if you move the checkout.

Dependencies (Arch packages; `install.sh` checks them and prints the install
command if any are missing):

| Package | Why |
| --- | --- |
| `gtk4`, `libadwaita`, `python-gobject` | GUI |
| `yt-dlp` | downloads |
| `ffmpeg` | merging and FLAC conversion |
| `flac` | `metaflac` tags the FLAC files |
| `python-secretstorage` | decrypts the Brave cookies reused via `--cookies-from-browser` |

The app runs on the **system** Python (`/usr/bin/python3`) because that is where
the Arch packages above resolve. You can also run it without installing:
`./media-dl --gui`.
