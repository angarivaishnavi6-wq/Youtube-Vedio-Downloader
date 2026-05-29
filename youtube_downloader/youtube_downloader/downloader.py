"""
downloader.py — yt-dlp integration & download engine
=====================================================
Responsibilities:
  * fetch_info()     – Extract video/playlist metadata (no download)
  * DownloadTask     – A single download job run in its own thread
  * DownloadManager  – Queue & manage multiple concurrent DownloadTasks
"""

import threading
import time
import os
import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Any

import yt_dlp

from utils import sanitize_filename, format_size, format_speed, format_eta


# ─────────────────────────────────────────────────────────────────────────────
#  Enums & status types
# ─────────────────────────────────────────────────────────────────────────────

class DownloadStatus(Enum):
    QUEUED    = auto()
    FETCHING  = auto()
    RUNNING   = auto()
    PAUSED    = auto()
    COMPLETED = auto()
    CANCELLED = auto()
    ERROR     = auto()


@dataclass
class ProgressInfo:
    """Snapshot of a download's current progress."""
    status: DownloadStatus = DownloadStatus.QUEUED
    percent: float = 0.0          # 0–100
    speed: str = "—"
    eta: str = "—"
    downloaded: str = "—"
    total: str = "—"
    filename: str = ""
    message: str = ""
    error: str = ""


# ─────────────────────────────────────────────────────────────────────────────
#  Metadata extraction
# ─────────────────────────────────────────────────────────────────────────────

def fetch_info(url: str, cookies_from_browser: str | None = None) -> dict[str, Any]:
    """
    Extract video/playlist metadata without downloading.
    Returns yt-dlp's info dict.
    Raises yt_dlp.utils.DownloadError on failure.
    """
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,      # full metadata for single videos
        "noplaylist": False,        # allow playlists
        "socket_timeout": 15,
    }
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)

    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def list_formats(info: dict) -> list[dict]:
    """
    Return a sorted, de-duplicated list of available format descriptors.
    Each item: {"label": "1080p", "format_id": "137+251", "ext": "mp4", ...}
    """
    if "formats" not in info:
        return []

    seen_heights: set = set()
    result = []

    for fmt in reversed(info["formats"]):  # best quality last in yt-dlp
        height = fmt.get("height")
        vcodec = fmt.get("vcodec", "none")
        acodec = fmt.get("acodec", "none")

        # Only video+audio combos or video-only (we merge later)
        if vcodec == "none":
            continue
        if height is None:
            continue

        label = f"{height}p"
        if label in seen_heights:
            continue
        seen_heights.add(label)

        result.append({
            "label": label,
            "height": height,
            "format_id": fmt.get("format_id", "bestvideo"),
            "ext": fmt.get("ext", "mp4"),
            "fps": fmt.get("fps"),
            "filesize": fmt.get("filesize") or fmt.get("filesize_approx"),
        })

    # Sort descending by resolution
    result.sort(key=lambda x: x["height"], reverse=True)
    # Ensure common quality labels are present
    standard = ["2160p", "1440p", "1080p", "720p", "480p", "360p", "240p", "144p"]
    labels_present = {r["label"] for r in result}
    for q in standard:
        if q not in labels_present:
            h = int(q[:-1])
            result.append({"label": q, "height": h, "format_id": f"bestvideo[height<={h}]+bestaudio/best[height<={h}]", "ext": "mp4"})
    result.sort(key=lambda x: x["height"], reverse=True)
    return result


# ─────────────────────────────────────────────────────────────────────────────
#  Single download task
# ─────────────────────────────────────────────────────────────────────────────

class DownloadTask:
    """
    Wraps a single yt-dlp download running in a background thread.

    Parameters
    ----------
    task_id        : unique identifier (string)
    url            : YouTube URL
    download_folder: destination directory
    mode           : "video", "audio", or "video+audio"
    quality        : e.g. "1080p", "720p", "best"
    fmt            : output container format, e.g. "mp4", "mp3", "mkv"
    subtitles      : download subtitles if True
    sub_lang       : subtitle language code, e.g. "en"
    on_progress    : callback(task_id, ProgressInfo) called from worker thread
    on_done        : callback(task_id, ProgressInfo) called on finish/error
    """

    def __init__(
        self,
        task_id: str,
        url: str,
        download_folder: str,
        mode: str = "video+audio",
        quality: str = "1080p",
        fmt: str = "mp4",
        subtitles: bool = False,
        sub_lang: str = "en",
        on_progress: Callable | None = None,
        on_done: Callable | None = None,
    ) -> None:
        self.task_id = task_id
        self.url = url
        self.download_folder = download_folder
        self.mode = mode
        self.quality = quality
        self.fmt = fmt
        self.subtitles = subtitles
        self.sub_lang = sub_lang
        self.on_progress = on_progress
        self.on_done = on_done

        self.progress = ProgressInfo()
        self._pause_event = threading.Event()
        self._pause_event.set()   # not paused initially
        self._cancel_flag = False
        self._thread: threading.Thread | None = None

    # ── Public controls ────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the download in a daemon thread."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def pause(self) -> None:
        """Pause the download (yt-dlp does not support mid-stream pause,
        so we cancel and remember position – best-effort)."""
        self._pause_event.clear()
        self.progress.status = DownloadStatus.PAUSED
        self.progress.message = "Paused"

    def resume(self) -> None:
        """Resume a paused download."""
        self._pause_event.set()
        self.progress.status = DownloadStatus.RUNNING
        self.progress.message = "Resuming…"

    def cancel(self) -> None:
        """Cancel the download."""
        self._cancel_flag = True
        self._pause_event.set()   # unblock if paused
        self.progress.status = DownloadStatus.CANCELLED
        self.progress.message = "Cancelled"

    # ── yt-dlp options builder ─────────────────────────────────────────────

    def _build_ydl_opts(self) -> dict[str, Any]:
        height = int(re.sub(r"[^\d]", "", self.quality) or "1080")

        # Format selection
        if self.mode == "audio":
            fmt_sel = "bestaudio/best"
        elif self.mode == "video":
            fmt_sel = f"bestvideo[height<={height}]/bestvideo"
        else:  # video+audio (default)
            fmt_sel = (
                f"bestvideo[height<={height}]+bestaudio/"
                f"bestvideo[height<={height}]/best[height<={height}]/best"
            )

        # Post-processors
        postprocessors = []
        if self.mode == "audio":
            postprocessors.append({
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            })
        elif self.fmt == "mp4":
            postprocessors.append({"key": "FFmpegVideoConvertor", "preferedformat": "mp4"})

        if self.subtitles:
            postprocessors.append({"key": "FFmpegSubtitlesConvertor", "format": "srt"})

        output_tmpl = os.path.join(
            self.download_folder,
            "%(title)s [%(id)s].%(ext)s",
        )

        opts: dict[str, Any] = {
            "format": fmt_sel,
            "outtmpl": output_tmpl,
            "progress_hooks": [self._progress_hook],
            "postprocessors": postprocessors,
            "merge_output_format": self.fmt if self.mode != "audio" else None,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 30,
            "retries": 5,
            "continuedl": True,       # resume partial downloads
            "noplaylist": True,       # single task = single video
        }

        if self.subtitles:
            opts.update({
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": [self.sub_lang],
            })

        # Remove None values
        return {k: v for k, v in opts.items() if v is not None}

    # ── Progress hook (called by yt-dlp in worker thread) ─────────────────

    def _progress_hook(self, d: dict) -> None:
        """Translate yt-dlp progress dict → ProgressInfo and fire callback."""
        if self._cancel_flag:
            raise yt_dlp.utils.DownloadCancelled("User cancelled")

        # Respect pause
        self._pause_event.wait()

        status_str = d.get("status", "")

        if status_str == "downloading":
            total_bytes = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded  = d.get("downloaded_bytes", 0)
            speed_raw   = d.get("speed") or 0
            eta_raw     = d.get("eta")

            percent = (downloaded / total_bytes * 100) if total_bytes else 0

            self.progress.status     = DownloadStatus.RUNNING
            self.progress.percent    = round(percent, 1)
            self.progress.speed      = format_speed(speed_raw)
            self.progress.eta        = format_eta(eta_raw)
            self.progress.downloaded = format_size(downloaded)
            self.progress.total      = format_size(total_bytes)
            self.progress.filename   = os.path.basename(d.get("filename", ""))
            self.progress.message    = f"Downloading… {percent:.1f}%"

        elif status_str == "finished":
            self.progress.status  = DownloadStatus.RUNNING
            self.progress.percent = 99.0
            self.progress.message = "Processing…"

        if self.on_progress:
            self.on_progress(self.task_id, self.progress)

    # ── Worker thread body ─────────────────────────────────────────────────

    def _run(self) -> None:
        self.progress.status  = DownloadStatus.RUNNING
        self.progress.message = "Starting download…"
        if self.on_progress:
            self.on_progress(self.task_id, self.progress)

        try:
            opts = self._build_ydl_opts()
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([self.url])

            if not self._cancel_flag:
                self.progress.status  = DownloadStatus.COMPLETED
                self.progress.percent = 100.0
                self.progress.message = "Download complete!"
                self.progress.speed   = "—"
                self.progress.eta     = "—"

        except yt_dlp.utils.DownloadCancelled:
            self.progress.status  = DownloadStatus.CANCELLED
            self.progress.message = "Cancelled by user"

        except yt_dlp.utils.DownloadError as e:
            msg = str(e)
            self.progress.status  = DownloadStatus.ERROR
            self.progress.message = "Download error"
            self.progress.error   = _friendly_error(msg)

        except Exception as e:
            self.progress.status  = DownloadStatus.ERROR
            self.progress.message = "Unexpected error"
            self.progress.error   = str(e)

        finally:
            if self.on_done:
                self.on_done(self.task_id, self.progress)


# ─────────────────────────────────────────────────────────────────────────────
#  Download manager (multiple concurrent tasks)
# ─────────────────────────────────────────────────────────────────────────────

class DownloadManager:
    """
    Manages a pool of DownloadTask objects.
    Enforces max_concurrent limit using a semaphore.
    """

    def __init__(self, max_concurrent: int = 2) -> None:
        self.max_concurrent = max_concurrent
        self._tasks: dict[str, DownloadTask] = {}
        self._lock = threading.Lock()
        self._semaphore = threading.Semaphore(max_concurrent)
        self._id_counter = 0

    def _next_id(self) -> str:
        self._id_counter += 1
        return f"task_{self._id_counter}_{int(time.time())}"

    def add_task(
        self,
        url: str,
        download_folder: str,
        mode: str = "video+audio",
        quality: str = "1080p",
        fmt: str = "mp4",
        subtitles: bool = False,
        sub_lang: str = "en",
        on_progress: Callable | None = None,
        on_done: Callable | None = None,
    ) -> str:
        """Create, register, and start a new DownloadTask. Returns task_id."""
        task_id = self._next_id()

        def _wrapped_done(tid, prog):
            self._semaphore.release()
            if on_done:
                on_done(tid, prog)

        task = DownloadTask(
            task_id=task_id,
            url=url,
            download_folder=download_folder,
            mode=mode,
            quality=quality,
            fmt=fmt,
            subtitles=subtitles,
            sub_lang=sub_lang,
            on_progress=on_progress,
            on_done=_wrapped_done,
        )

        with self._lock:
            self._tasks[task_id] = task

        # Acquire semaphore in a thread so callers aren't blocked
        def _acquire_and_start():
            self._semaphore.acquire()
            if not task._cancel_flag:
                task.start()

        threading.Thread(target=_acquire_and_start, daemon=True).start()
        return task_id

    def get_task(self, task_id: str) -> DownloadTask | None:
        return self._tasks.get(task_id)

    def pause(self, task_id: str) -> None:
        if t := self._tasks.get(task_id):
            t.pause()

    def resume(self, task_id: str) -> None:
        if t := self._tasks.get(task_id):
            t.resume()

    def cancel(self, task_id: str) -> None:
        if t := self._tasks.get(task_id):
            t.cancel()

    def cancel_all(self) -> None:
        for t in list(self._tasks.values()):
            t.cancel()

    def active_count(self) -> int:
        return sum(
            1 for t in self._tasks.values()
            if t.progress.status in (DownloadStatus.RUNNING, DownloadStatus.FETCHING)
        )

    def remove_task(self, task_id: str) -> None:
        with self._lock:
            self._tasks.pop(task_id, None)


# ─────────────────────────────────────────────────────────────────────────────
#  Error message translator
# ─────────────────────────────────────────────────────────────────────────────

def _friendly_error(raw: str) -> str:
    """Convert technical yt-dlp errors to user-friendly messages."""
    raw_lower = raw.lower()
    if "video unavailable" in raw_lower:
        return "Video is unavailable (private, deleted, or region-locked)."
    if "copyright" in raw_lower or "has been removed" in raw_lower:
        return "Video removed due to copyright restrictions."
    if "private video" in raw_lower:
        return "This is a private video — sign in required."
    if "sign in" in raw_lower or "login" in raw_lower:
        return "This video requires sign-in. Try using browser cookies."
    if "unable to extract" in raw_lower:
        return "Could not extract video info. URL may be invalid."
    if "network" in raw_lower or "connection" in raw_lower:
        return "Network error. Check your internet connection."
    if "ffmpeg" in raw_lower:
        return "FFmpeg not found. Please install FFmpeg and add it to PATH."
    # Default: first sentence of the error
    return raw.split("\n")[0][:150]
