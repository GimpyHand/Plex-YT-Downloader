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

## Windows

To build a portable Windows x64 copy, open the repository's **Actions →
Windows build** workflow on GitHub and choose **Run workflow**. Download the
`MediaDL-windows-x64` artifact, extract it, and launch `MediaDL.exe`. The
package includes GTK, yt-dlp, and FFmpeg; recipients do not need Python or
MSYS2 installed.

The Windows build uses the same GTK 4 and libadwaita interface. Linux continues
to use the existing system packages and `./media-dl` launcher.
