# YT Downloader Pro 🎬

A modern, feature-rich YouTube Video Downloader with a clean dark GUI built with Python, CustomTkinter, and yt-dlp.

---

## ✨ Features

| Feature | Details |
|---|---|
| Video info | Fetches title, thumbnail, duration, channel, views |
| Download modes | Video+Audio, Video Only, Audio (MP3) |
| Quality selector | 144p → 1080p (auto-detected from video) |
| Format selector | MP4, MKV, WebM, AVI |
| Progress tracking | Live progress bar, speed, ETA, file size |
| Pause / Resume / Cancel | Per-download controls |
| Batch download | Paste multiple URLs, download all at once |
| Playlist support | Auto-detects YouTube playlists |
| Clipboard detection | Auto-fills URL from clipboard |
| Download history | Searchable, with folder shortcuts |
| Subtitles | Optional subtitle download |
| Dark / Light mode | Toggle in sidebar or Settings |
| Settings panel | Persistent JSON config |
| Error handling | Friendly messages for all failure cases |
| Threading | GUI never freezes during downloads |

---

## 🖥️ Requirements

- **Python 3.10+**
- **FFmpeg** (required for merging video+audio, MP3 conversion)

### Install FFmpeg

**Windows:**
```
winget install Gyan.FFmpeg
```
Or download from https://ffmpeg.org/download.html and add to PATH.

**macOS:**
```
brew install ffmpeg
```

**Linux:**
```
sudo apt install ffmpeg
```

---

## 🚀 Installation

### 1. Clone or extract the project
```bash
cd youtube_downloader
```

### 2. Create a virtual environment (recommended)
```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Run the app
```bash
python main.py
```

---

## 📁 Project Structure

```
youtube_downloader/
├── main.py          # Entry point – init, config load, launch window
├── ui.py            # Full CustomTkinter GUI (all panels)
├── downloader.py    # yt-dlp integration, DownloadTask, DownloadManager
├── utils.py         # Config I/O, URL validators, formatters, helpers
├── config.json      # User settings (auto-created if missing)
├── requirements.txt # Python package list
└── README.md        # This file
```

---

## 🎮 Usage

1. **Paste** a YouTube video or playlist URL in the URL bar.
2. Click **Fetch Info** — thumbnail, title, duration appear.
3. Choose **mode** (Video+Audio / Video Only / Audio MP3).
4. Select **quality** and **format**.
5. Optionally set a **save folder** or enable **subtitles**.
6. Click **⬇ Start Download**.
7. Watch real-time progress in the **Active Downloads** section.
8. Use **⏸ Pause / ▶ Resume / ✕ Cancel** per download.

### Batch Download
- Click **📋 Batch** in the sidebar.
- Paste one URL per line.
- Choose quality/mode and click **⬇ Download All**.

### History
- Click **🕒 History** to see past downloads.
- Search by title or URL.
- Open folder or re-open URL from history.

---

## ⚙️ Configuration (`config.json`)

| Key | Default | Description |
|---|---|---|
| `download_folder` | `~/Downloads/YT-Downloader` | Default save directory |
| `appearance_mode` | `dark` | `dark`, `light`, or `system` |
| `default_quality` | `1080p` | Pre-selected quality |
| `default_format` | `mp4` | Output container |
| `max_concurrent` | `2` | Max simultaneous downloads |
| `auto_detect_clipboard` | `true` | Auto-fill URL from clipboard |
| `download_subtitles` | `false` | Auto-download subs |
| `subtitle_language` | `en` | Subtitle language code |

---

## 📦 Build Executable with PyInstaller

### 1. Install PyInstaller
```bash
pip install pyinstaller
```

### 2. Build single-file executable
```bash
# Windows
pyinstaller --onefile --windowed --name "YT-Downloader-Pro" main.py

# macOS / Linux
pyinstaller --onefile --windowed --name "yt-downloader-pro" main.py
```

### 3. Find your executable
```
dist/
└── YT-Downloader-Pro.exe   (Windows)
    YT-Downloader-Pro        (macOS/Linux)
```

### 4. Advanced build (with icon + hidden imports)
```bash
pyinstaller \
  --onefile \
  --windowed \
  --name "YT-Downloader-Pro" \
  --add-data "config.json;." \
  --hidden-import "customtkinter" \
  --hidden-import "PIL" \
  --hidden-import "yt_dlp" \
  main.py
```

> **Note:** FFmpeg must still be installed on the target machine or bundled separately.

---

## 🐛 Troubleshooting

| Problem | Solution |
|---|---|
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` |
| FFmpeg errors | Install FFmpeg and ensure it's in PATH |
| Video unavailable | Try a different quality or check the URL |
| Slow thumbnail | Check internet connection; thumbnail loads async |
| Black screen on exe | Run from terminal to see error output |
| Age-restricted videos | Use `--cookies-from-browser` via yt-dlp CLI |

---

## 📜 License

MIT License — free to use, modify, and distribute.

---

## 🙏 Credits

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — the download engine
- [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) — modern Tkinter UI
- [Pillow](https://python-pillow.org/) — image processing
