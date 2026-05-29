"""
utils.py — Configuration helpers & shared utilities
=====================================================
* load_config / save_config  – persistent JSON settings
* ensure_download_dir         – create folder if missing
* format_size / format_speed  – human-readable strings
* is_valid_youtube_url        – quick URL validator
* sanitize_filename           – safe filename builder
* detect_clipboard_url        – auto-detect YT links
"""

import json
import os
import re
import time
from pathlib import Path
from typing import Any

# ── Config paths ───────────────────────────────────────────────────────────────
APP_DIR = Path(__file__).parent
CONFIG_PATH = APP_DIR / "config.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "download_folder": str(Path.home() / "Downloads" / "YT-Downloader"),
    "appearance_mode": "dark",
    "default_quality": "1080p",
    "default_format": "mp4",
    "max_concurrent": 2,
    "auto_detect_clipboard": True,
    "download_subtitles": False,
    "subtitle_language": "en",
    "download_history": [],
    "language": "en",
    "last_folder": "",
    "show_speed": True,
    "auto_convert_mp4": True,
}

# ── YouTube URL patterns ───────────────────────────────────────────────────────
_YT_PATTERNS = [
    r"(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)"
    r"[A-Za-z0-9_\-]{11}",
    r"(https?://)?(www\.)?youtube\.com/playlist\?list=[A-Za-z0-9_\-]+",
]


# ─────────────────────────────────────────────────────────────────────────────
#  Config I/O
# ─────────────────────────────────────────────────────────────────────────────

def load_config() -> dict[str, Any]:
    """Load config.json; create with defaults if absent or corrupted."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Merge missing keys from defaults
            for key, val in DEFAULT_CONFIG.items():
                data.setdefault(key, val)
            return data
        except (json.JSONDecodeError, OSError):
            pass  # Fall through to defaults
    save_config(DEFAULT_CONFIG.copy())
    return DEFAULT_CONFIG.copy()


def save_config(config: dict[str, Any]) -> None:
    """Persist config to config.json."""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except OSError as e:
        print(f"[WARN] Could not save config: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  File system helpers
# ─────────────────────────────────────────────────────────────────────────────

def ensure_download_dir(path: str) -> str:
    """Create directory (and parents) if it does not exist. Return path."""
    Path(path).mkdir(parents=True, exist_ok=True)
    return path


def sanitize_filename(name: str, max_len: int = 100) -> str:
    """Remove characters unsafe for filenames and truncate."""
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = name.strip(". ")
    return name[:max_len] if name else "download"


# ─────────────────────────────────────────────────────────────────────────────
#  Human-readable formatters
# ─────────────────────────────────────────────────────────────────────────────

def format_size(bytes_: float) -> str:
    """Convert bytes to human-readable size string."""
    if bytes_ < 0:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if bytes_ < 1024:
            return f"{bytes_:.1f} {unit}"
        bytes_ /= 1024
    return f"{bytes_:.1f} TB"


def format_speed(bps: float) -> str:
    """Convert bytes/sec to human-readable speed string."""
    return format_size(bps) + "/s"


def format_duration(seconds: float | None) -> str:
    """Convert seconds to HH:MM:SS or MM:SS string."""
    if seconds is None or seconds < 0:
        return "—"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def format_eta(seconds: float | None) -> str:
    """Convert ETA seconds to readable string."""
    if seconds is None or seconds < 0:
        return "—"
    return format_duration(seconds)


def format_timestamp(ts: float) -> str:
    """Convert Unix timestamp to local date-time string."""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


# ─────────────────────────────────────────────────────────────────────────────
#  URL helpers
# ─────────────────────────────────────────────────────────────────────────────

def is_valid_youtube_url(url: str) -> bool:
    """Return True if *url* looks like a YouTube video / playlist URL."""
    url = url.strip()
    return any(re.search(p, url) for p in _YT_PATTERNS)


def is_playlist_url(url: str) -> bool:
    """Return True if the URL points to a YouTube playlist."""
    return "playlist?list=" in url or "&list=" in url


def clean_url(url: str) -> str:
    """Strip leading/trailing whitespace and newlines from URL."""
    return url.strip()


def detect_clipboard_url(widget) -> str | None:
    """
    Try to read the system clipboard via the Tk widget.
    Return the URL string if it looks like a YouTube link, else None.
    """
    try:
        text = widget.clipboard_get().strip()
        if is_valid_youtube_url(text):
            return text
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Download history helpers
# ─────────────────────────────────────────────────────────────────────────────

def add_to_history(config: dict, entry: dict) -> None:
    """Append a download entry to history (max 200 items) and save config."""
    history: list = config.setdefault("download_history", [])
    history.insert(0, entry)
    config["download_history"] = history[:200]
    save_config(config)


def clear_history(config: dict) -> None:
    """Wipe the download history and save config."""
    config["download_history"] = []
    save_config(config)
