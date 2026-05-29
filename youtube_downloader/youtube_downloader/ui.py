"""
ui.py — CustomTkinter GUI for YouTube Downloader
=================================================
Panels:
  1. URL bar + Fetch button (clipboard auto-detect)
  2. Video info card  (thumbnail, title, duration, channel)
  3. Download options (mode, quality, format, folder, subtitles)
  4. Active downloads  (progress bars, controls per task)
  5. Batch download    (paste multiple URLs)
  6. Download history  (searchable table)
  7. Settings          (appearance, concurrency, defaults…)
"""

from __future__ import annotations

import io
import os
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any

import customtkinter as ctk
import requests
from PIL import Image, ImageTk

from downloader import (
    DownloadManager,
    DownloadStatus,
    ProgressInfo,
    fetch_info,
    list_formats,
)
from utils import (
    add_to_history,
    clear_history,
    detect_clipboard_url,
    format_duration,
    format_timestamp,
    is_playlist_url,
    is_valid_youtube_url,
    save_config,
)


# ─────────────────────────────────────────────────────────────────────────────
#  Theme palette (used everywhere)
# ─────────────────────────────────────────────────────────────────────────────

ACCENT   = "#FF4444"        # YouTube-ish red
ACCENT2  = "#FF7043"        # warm orange accent
SUCCESS  = "#4CAF50"
WARNING  = "#FFC107"
MUTED    = "#888888"
DARK_BG  = "#0F0F0F"
CARD_BG  = "#1A1A1A"


# ─────────────────────────────────────────────────────────────────────────────
#  Helper: thumbnail loader (runs in thread)
# ─────────────────────────────────────────────────────────────────────────────

def _load_thumbnail(url: str, callback, size=(260, 146)) -> None:
    """Fetch thumbnail in background thread, call callback(CTkImage) on success."""
    def _fetch():
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            img.thumbnail(size, Image.LANCZOS)
            # Create a black letterbox canvas
            canvas = Image.new("RGB", size, (0, 0, 0))
            offset = ((size[0] - img.width) // 2, (size[1] - img.height) // 2)
            canvas.paste(img, offset)
            ctk_img = ctk.CTkImage(light_image=canvas, dark_image=canvas, size=size)
            callback(ctk_img)
        except Exception:
            callback(None)

    threading.Thread(target=_fetch, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
#  Status badge label
# ─────────────────────────────────────────────────────────────────────────────

STATUS_COLORS = {
    DownloadStatus.QUEUED:    ("#FFC107", "#333300"),
    DownloadStatus.FETCHING:  ("#64B5F6", "#003366"),
    DownloadStatus.RUNNING:   (ACCENT,    "#330000"),
    DownloadStatus.PAUSED:    ("#FFA726", "#332200"),
    DownloadStatus.COMPLETED: (SUCCESS,   "#003300"),
    DownloadStatus.CANCELLED: (MUTED,     "#222222"),
    DownloadStatus.ERROR:     ("#FF5252", "#330000"),
}

STATUS_LABELS = {
    DownloadStatus.QUEUED:    "QUEUED",
    DownloadStatus.FETCHING:  "FETCHING",
    DownloadStatus.RUNNING:   "DOWNLOADING",
    DownloadStatus.PAUSED:    "PAUSED",
    DownloadStatus.COMPLETED: "DONE",
    DownloadStatus.CANCELLED: "CANCELLED",
    DownloadStatus.ERROR:     "ERROR",
}


# ─────────────────────────────────────────────────────────────────────────────
#  Per-download row widget
# ─────────────────────────────────────────────────────────────────────────────

class DownloadRow(ctk.CTkFrame):
    """One row in the active-downloads panel."""

    def __init__(self, master, task_id: str, title: str, manager: DownloadManager, remove_cb, **kw):
        super().__init__(master, fg_color=CARD_BG, corner_radius=10, **kw)
        self.task_id   = task_id
        self.manager   = manager
        self.remove_cb = remove_cb
        self._status   = DownloadStatus.QUEUED

        self.columnconfigure(0, weight=1)

        # Title row
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        top.columnconfigure(0, weight=1)

        self.title_lbl = ctk.CTkLabel(
            top, text=title[:80], anchor="w",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
        )
        self.title_lbl.grid(row=0, column=0, sticky="ew")

        self.status_lbl = ctk.CTkLabel(
            top, text="QUEUED", width=90,
            fg_color="#333300", text_color=WARNING,
            corner_radius=4, font=ctk.CTkFont(size=10, weight="bold"),
        )
        self.status_lbl.grid(row=0, column=1, padx=(8, 0))

        # Progress bar
        self.progress_bar = ctk.CTkProgressBar(self, height=6, progress_color=ACCENT)
        self.progress_bar.grid(row=1, column=0, sticky="ew", padx=12, pady=2)
        self.progress_bar.set(0)

        # Stats row
        stats = ctk.CTkFrame(self, fg_color="transparent")
        stats.grid(row=2, column=0, sticky="ew", padx=12, pady=(2, 4))

        self.pct_lbl    = self._stat_lbl(stats, "0%",   0)
        self.speed_lbl  = self._stat_lbl(stats, "— /s", 1)
        self.eta_lbl    = self._stat_lbl(stats, "ETA —", 2)
        self.size_lbl   = self._stat_lbl(stats, "—",    3)
        stats.columnconfigure(4, weight=1)

        # Controls
        ctrl = ctk.CTkFrame(self, fg_color="transparent")
        ctrl.grid(row=3, column=0, sticky="e", padx=12, pady=(0, 10))

        self.pause_btn  = self._ctrl_btn(ctrl, "⏸ Pause",  self._pause,  0)
        self.resume_btn = self._ctrl_btn(ctrl, "▶ Resume", self._resume, 1)
        self.cancel_btn = self._ctrl_btn(ctrl, "✕ Cancel", self._cancel, 2)
        self.remove_btn = self._ctrl_btn(ctrl, "🗑 Remove", self._remove, 3, fg=ACCENT)
        self.resume_btn.configure(state="disabled")

        # Error label (hidden by default)
        self.error_lbl = ctk.CTkLabel(
            self, text="", text_color="#FF5252", anchor="w",
            font=ctk.CTkFont(size=11),
        )

    # ── Layout helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _stat_lbl(parent, text, col):
        lbl = ctk.CTkLabel(
            parent, text=text, text_color=MUTED,
            font=ctk.CTkFont(size=11),
        )
        lbl.grid(row=0, column=col, padx=(0, 16), sticky="w")
        return lbl

    @staticmethod
    def _ctrl_btn(parent, text, cmd, col, fg=None):
        kw = {}
        if fg:
            kw["fg_color"] = fg
        btn = ctk.CTkButton(
            parent, text=text, command=cmd, width=88, height=28,
            font=ctk.CTkFont(size=11), **kw,
        )
        btn.grid(row=0, column=col, padx=(0, 6))
        return btn

    # ── Control actions ────────────────────────────────────────────────────

    def _pause(self):
        self.manager.pause(self.task_id)
        self.pause_btn.configure(state="disabled")
        self.resume_btn.configure(state="normal")

    def _resume(self):
        self.manager.resume(self.task_id)
        self.resume_btn.configure(state="disabled")
        self.pause_btn.configure(state="normal")

    def _cancel(self):
        self.manager.cancel(self.task_id)
        self.pause_btn.configure(state="disabled")
        self.resume_btn.configure(state="disabled")
        self.cancel_btn.configure(state="disabled")

    def _remove(self):
        self.manager.cancel(self.task_id)
        self.remove_cb(self.task_id)

    # ── Progress update (called from main thread via after()) ──────────────

    def update_progress(self, prog: ProgressInfo) -> None:
        self._status = prog.status
        fg, bg = STATUS_COLORS.get(prog.status, (MUTED, "#222"))
        self.status_lbl.configure(text=STATUS_LABELS[prog.status], fg_color=bg, text_color=fg)

        self.progress_bar.set(prog.percent / 100)
        self.pct_lbl.configure(text=f"{prog.percent:.1f}%")
        self.speed_lbl.configure(text=prog.speed)
        self.eta_lbl.configure(text=f"ETA {prog.eta}")
        self.size_lbl.configure(text=f"{prog.downloaded} / {prog.total}")

        if prog.status == DownloadStatus.COMPLETED:
            self.progress_bar.configure(progress_color=SUCCESS)
            self.pause_btn.configure(state="disabled")
            self.cancel_btn.configure(state="disabled")

        elif prog.status == DownloadStatus.ERROR:
            self.progress_bar.configure(progress_color=ACCENT)
            self.error_lbl.configure(text=f"⚠ {prog.error}")
            self.error_lbl.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 8))
            self.pause_btn.configure(state="disabled")
            self.cancel_btn.configure(state="disabled")


# ─────────────────────────────────────────────────────────────────────────────
#  Main application window
# ─────────────────────────────────────────────────────────────────────────────

class App(ctk.CTk):
    """Main application window."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__()
        self.config_data = config
        self.manager = DownloadManager(config.get("max_concurrent", 2))
        self._info: dict | None = None            # last fetched video info
        self._formats: list[dict] = []            # available quality list
        self._download_rows: dict[str, DownloadRow] = {}

        # ── Window setup ───────────────────────────────────────────────────
        self.title("YT Downloader Pro")
        self.geometry("960x760")
        self.minsize(800, 600)
        self.configure(fg_color=DARK_BG)

        # App icon (graceful fallback)
        try:
            self.iconbitmap()
        except Exception:
            pass

        # ── Layout: left nav + right content ──────────────────────────────
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_content_area()

        # ── Clipboard polling ──────────────────────────────────────────────
        if config.get("auto_detect_clipboard", True):
            self._last_clipboard = ""
            self._poll_clipboard()

        # ── Drag-and-drop hint (tkinter DnD is platform-limited; handled
        #    via a drop-target frame with a border hint instead) ─────────────

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ═════════════════════════════════════════════════════════════════════════
    #  Sidebar navigation
    # ═════════════════════════════════════════════════════════════════════════

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self, width=200, fg_color=CARD_BG, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.columnconfigure(0, weight=1)

        # Logo area
        logo_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        logo_frame.grid(row=0, column=0, pady=(20, 6), padx=16, sticky="ew")
        ctk.CTkLabel(
            logo_frame, text="▶", text_color=ACCENT,
            font=ctk.CTkFont(size=32),
        ).pack(side="left")
        ctk.CTkLabel(
            logo_frame, text="YT\nDownloader", text_color="white",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            justify="left",
        ).pack(side="left", padx=(6, 0))

        ctk.CTkLabel(sidebar, text="Pro Edition", text_color=MUTED,
                     font=ctk.CTkFont(size=10)).grid(row=1, column=0)

        ctk.CTkFrame(sidebar, height=1, fg_color="#333").grid(row=2, column=0, sticky="ew", padx=16, pady=10)

        # Nav buttons
        self._nav_btns: dict[str, ctk.CTkButton] = {}
        nav_items = [
            ("⬇  Download",   "download"),
            ("📋  Batch",      "batch"),
            ("🕒  History",    "history"),
            ("⚙   Settings",  "settings"),
        ]
        for row_idx, (label, key) in enumerate(nav_items, start=3):
            btn = ctk.CTkButton(
                sidebar, text=label, anchor="w",
                fg_color="transparent", hover_color="#2A2A2A",
                text_color="white", height=44,
                font=ctk.CTkFont(size=13),
                command=lambda k=key: self._show_panel(k),
            )
            btn.grid(row=row_idx, column=0, sticky="ew", padx=8, pady=2)
            self._nav_btns[key] = btn

        # Spacer
        sidebar.rowconfigure(20, weight=1)

        # Theme toggle at bottom
        theme_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        theme_frame.grid(row=21, column=0, sticky="ew", padx=16, pady=(0, 16))
        ctk.CTkLabel(theme_frame, text="🌙 Theme", text_color=MUTED,
                     font=ctk.CTkFont(size=11)).pack(side="left")
        self._theme_switch = ctk.CTkSwitch(
            theme_frame, text="", width=44,
            command=self._toggle_theme,
            onvalue="dark", offvalue="light",
        )
        self._theme_switch.pack(side="right")
        if self.config_data.get("appearance_mode", "dark") == "dark":
            self._theme_switch.select()

        # Active downloads badge
        self._active_badge = ctk.CTkLabel(
            sidebar, text="", fg_color=ACCENT, corner_radius=8,
            font=ctk.CTkFont(size=10, weight="bold"),
        )
        self._active_badge.grid(row=22, column=0, pady=(0, 8))

    # ═════════════════════════════════════════════════════════════════════════
    #  Content area (tabbed panels)
    # ═════════════════════════════════════════════════════════════════════════

    def _build_content_area(self) -> None:
        self._panels: dict[str, ctk.CTkFrame] = {}
        container = ctk.CTkFrame(self, fg_color=DARK_BG, corner_radius=0)
        container.grid(row=0, column=1, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        for key, builder in [
            ("download", self._build_download_panel),
            ("batch",    self._build_batch_panel),
            ("history",  self._build_history_panel),
            ("settings", self._build_settings_panel),
        ]:
            frame = ctk.CTkFrame(container, fg_color=DARK_BG, corner_radius=0)
            frame.grid(row=0, column=0, sticky="nsew")
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(0, weight=1)
            builder(frame)
            self._panels[key] = frame

        self._show_panel("download")

    def _show_panel(self, key: str) -> None:
        for k, frame in self._panels.items():
            frame.grid_remove()
        self._panels[key].grid()

        # Highlight active nav button
        for k, btn in self._nav_btns.items():
            btn.configure(
                fg_color=ACCENT if k == key else "transparent",
                text_color="white",
            )

    # ═════════════════════════════════════════════════════════════════════════
    #  Download panel
    # ═════════════════════════════════════════════════════════════════════════

    def _build_download_panel(self, parent: ctk.CTkFrame) -> None:
        scroll = ctk.CTkScrollableFrame(parent, fg_color=DARK_BG)
        scroll.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
        scroll.columnconfigure(0, weight=1)

        # ── URL input card ─────────────────────────────────────────────────
        url_card = self._card(scroll, row=0)
        ctk.CTkLabel(url_card, text="YouTube URL", text_color=MUTED,
                     font=ctk.CTkFont(size=11)).pack(anchor="w", padx=16, pady=(12, 2))

        url_row = ctk.CTkFrame(url_card, fg_color="transparent")
        url_row.pack(fill="x", padx=16, pady=(0, 12))
        url_row.columnconfigure(0, weight=1)

        self._url_var = tk.StringVar()
        self._url_entry = ctk.CTkEntry(
            url_row, textvariable=self._url_var,
            placeholder_text="Paste YouTube URL here  (or drop a link)",
            height=44, font=ctk.CTkFont(size=13),
            border_color=ACCENT, border_width=2,
        )
        self._url_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self._fetch_btn = ctk.CTkButton(
            url_row, text="Fetch Info", width=110, height=44,
            fg_color=ACCENT, hover_color=ACCENT2,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._on_fetch,
        )
        self._fetch_btn.grid(row=0, column=1)

        self._clear_btn = ctk.CTkButton(
            url_row, text="✕", width=44, height=44,
            fg_color="#333", hover_color="#444",
            command=lambda: self._url_var.set(""),
        )
        self._clear_btn.grid(row=0, column=2, padx=(4, 0))

        # Clipboard hint
        self._clip_hint = ctk.CTkLabel(url_card, text="", text_color=ACCENT,
                                        font=ctk.CTkFont(size=11))
        self._clip_hint.pack(anchor="w", padx=16, pady=(0, 4))

        # ── Fetch status ───────────────────────────────────────────────────
        self._fetch_status = ctk.CTkLabel(
            scroll, text="", text_color=MUTED,
            font=ctk.CTkFont(size=12),
        )
        self._fetch_status.grid(row=1, column=0, pady=4, padx=16, sticky="w")

        # ── Video info card (hidden until fetched) ─────────────────────────
        self._info_card = self._card(scroll, row=2)
        self._info_card.grid_remove()

        # Thumbnail
        self._thumb_lbl = ctk.CTkLabel(self._info_card, text="", width=260, height=146)
        self._thumb_lbl.grid(row=0, column=0, rowspan=5, padx=16, pady=16, sticky="nw")

        # Metadata labels
        self._title_lbl    = self._meta_lbl(self._info_card, "Title",    1)
        self._channel_lbl  = self._meta_lbl(self._info_card, "Channel",  2)
        self._duration_lbl = self._meta_lbl(self._info_card, "Duration", 3)
        self._views_lbl    = self._meta_lbl(self._info_card, "Views",    4)

        # Open in browser
        self._open_btn = ctk.CTkButton(
            self._info_card, text="🔗 Open in Browser", width=160, height=30,
            fg_color="#333", hover_color="#444", font=ctk.CTkFont(size=11),
            command=self._open_in_browser,
        )
        self._open_btn.grid(row=5, column=1, columnspan=2, sticky="w", padx=8, pady=(0, 12))

        # ── Download options card ──────────────────────────────────────────
        self._opts_card = self._card(scroll, row=3)
        self._opts_card.grid_remove()

        ctk.CTkLabel(self._opts_card, text="Download Options",
                     font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, columnspan=4, padx=16, pady=(14, 8), sticky="w")

        # Mode selector
        ctk.CTkLabel(self._opts_card, text="Mode", text_color=MUTED,
                     font=ctk.CTkFont(size=11)).grid(row=1, column=0, padx=16, sticky="w")
        self._mode_var = tk.StringVar(value="video+audio")
        mode_seg = ctk.CTkSegmentedButton(
            self._opts_card,
            values=["Video + Audio", "Video Only", "Audio (MP3)"],
            variable=self._mode_var,
            command=self._on_mode_change,
        )
        # Map display → internal value
        self._mode_map = {"Video + Audio": "video+audio", "Video Only": "video", "Audio (MP3)": "audio"}
        mode_seg.grid(row=1, column=1, columnspan=3, padx=8, pady=4, sticky="ew")
        self._mode_seg = mode_seg
        self._mode_seg.set("Video + Audio")

        # Quality selector
        ctk.CTkLabel(self._opts_card, text="Quality", text_color=MUTED,
                     font=ctk.CTkFont(size=11)).grid(row=2, column=0, padx=16, sticky="w")
        self._quality_var = tk.StringVar(value=self.config_data.get("default_quality", "1080p"))
        self._quality_combo = ctk.CTkComboBox(
            self._opts_card, variable=self._quality_var,
            values=["1080p", "720p", "480p", "360p", "240p", "144p"],
            width=150,
        )
        self._quality_combo.grid(row=2, column=1, padx=8, pady=4, sticky="w")

        # Format selector
        ctk.CTkLabel(self._opts_card, text="Format", text_color=MUTED,
                     font=ctk.CTkFont(size=11)).grid(row=2, column=2, padx=(24, 8), sticky="w")
        self._format_var = tk.StringVar(value=self.config_data.get("default_format", "mp4"))
        ctk.CTkComboBox(
            self._opts_card, variable=self._format_var,
            values=["mp4", "mkv", "webm", "avi"],
            width=100,
        ).grid(row=2, column=3, padx=8, pady=4, sticky="w")

        # Folder selector
        ctk.CTkLabel(self._opts_card, text="Save to", text_color=MUTED,
                     font=ctk.CTkFont(size=11)).grid(row=3, column=0, padx=16, sticky="w")
        folder_row = ctk.CTkFrame(self._opts_card, fg_color="transparent")
        folder_row.grid(row=3, column=1, columnspan=3, padx=8, pady=4, sticky="ew")
        folder_row.columnconfigure(0, weight=1)

        self._folder_var = tk.StringVar(value=self.config_data.get(
            "download_folder", str(Path.home() / "Downloads")))
        ctk.CTkEntry(folder_row, textvariable=self._folder_var,
                     font=ctk.CTkFont(size=11)).grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(
            folder_row, text="Browse", width=70, height=28,
            fg_color="#333", command=self._browse_folder,
        ).grid(row=0, column=1, padx=(6, 0))

        # Subtitles
        self._sub_var = tk.BooleanVar(value=self.config_data.get("download_subtitles", False))
        ctk.CTkCheckBox(self._opts_card, text="Download Subtitles",
                        variable=self._sub_var).grid(row=4, column=0, columnspan=2,
                                                     padx=16, pady=(4, 8), sticky="w")
        ctk.CTkLabel(self._opts_card, text="Language", text_color=MUTED,
                     font=ctk.CTkFont(size=11)).grid(row=4, column=2, padx=(24, 8), sticky="w")
        self._sub_lang_var = tk.StringVar(
            value=self.config_data.get("subtitle_language", "en"))
        ctk.CTkEntry(self._opts_card, textvariable=self._sub_lang_var, width=60).grid(
            row=4, column=3, padx=8, pady=4, sticky="w")

        # Playlist hint
        self._playlist_hint = ctk.CTkLabel(
            self._opts_card, text="", text_color=WARNING,
            font=ctk.CTkFont(size=11),
        )
        self._playlist_hint.grid(row=5, column=0, columnspan=4, padx=16, sticky="w")

        # Download button
        self._dl_btn = ctk.CTkButton(
            self._opts_card, text="⬇  Start Download", height=46,
            fg_color=ACCENT, hover_color=ACCENT2,
            font=ctk.CTkFont(size=15, weight="bold"),
            command=self._on_download,
        )
        self._dl_btn.grid(row=6, column=0, columnspan=4, padx=16, pady=(8, 16), sticky="ew")

        # ── Active downloads card ──────────────────────────────────────────
        self._dl_header_card = self._card(scroll, row=4)

        header_row = ctk.CTkFrame(self._dl_header_card, fg_color="transparent")
        header_row.pack(fill="x", padx=16, pady=(12, 8))
        ctk.CTkLabel(header_row, text="Active Downloads",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        ctk.CTkButton(header_row, text="Cancel All", width=90, height=28,
                      fg_color="#333", hover_color=ACCENT,
                      font=ctk.CTkFont(size=11),
                      command=self.manager.cancel_all).pack(side="right")

        self._dl_rows_frame = ctk.CTkFrame(self._dl_header_card, fg_color="transparent")
        self._dl_rows_frame.pack(fill="x", padx=16, pady=(0, 12))
        self._dl_rows_frame.columnconfigure(0, weight=1)

        self._no_dl_lbl = ctk.CTkLabel(
            self._dl_rows_frame, text="No active downloads",
            text_color=MUTED, font=ctk.CTkFont(size=12),
        )
        self._no_dl_lbl.grid(row=0, column=0, pady=12)

    # ── Download panel helpers ─────────────────────────────────────────────

    @staticmethod
    def _meta_lbl(parent, label: str, row: int):
        ctk.CTkLabel(parent, text=label + ":", text_color=MUTED,
                     font=ctk.CTkFont(size=11)).grid(row=row, column=1, padx=(16, 4), sticky="w", pady=2)
        val = ctk.CTkLabel(parent, text="—", anchor="w",
                           font=ctk.CTkFont(size=12), wraplength=460)
        val.grid(row=row, column=2, sticky="w", pady=2)
        return val

    def _card(self, parent, row: int) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, fg_color=CARD_BG, corner_radius=12)
        card.grid(row=row, column=0, sticky="ew", padx=16, pady=(0, 12))
        card.columnconfigure(0, weight=1)
        return card

    # ─── Fetch ────────────────────────────────────────────────────────────

    def _on_fetch(self) -> None:
        url = self._url_var.get().strip()
        if not url:
            self._fetch_status.configure(text="⚠ Please enter a URL", text_color=WARNING)
            return
        if not is_valid_youtube_url(url):
            self._fetch_status.configure(
                text="⚠ Invalid YouTube URL. Check and try again.", text_color=ACCENT)
            return

        self._fetch_btn.configure(state="disabled", text="Fetching…")
        self._fetch_status.configure(text="🔍 Fetching video info…", text_color=MUTED)
        self._info_card.grid_remove()
        self._opts_card.grid_remove()

        threading.Thread(target=self._fetch_thread, args=(url,), daemon=True).start()

    def _fetch_thread(self, url: str) -> None:
        try:
            info = fetch_info(url)
            self.after(0, self._on_fetch_success, info, url)
        except Exception as e:
            self.after(0, self._on_fetch_error, str(e))

    def _on_fetch_success(self, info: dict, url: str) -> None:
        self._info = info
        self._formats = list_formats(info)
        self._fetch_btn.configure(state="normal", text="Fetch Info")

        is_playlist = info.get("_type") == "playlist"
        title = info.get("title") or info.get("playlist_title") or "Unknown"
        entries = info.get("entries", [])
        count_str = f" ({len(entries)} videos)" if entries else ""

        self._fetch_status.configure(
            text=f"✓ Fetched: {title[:60]}{count_str}", text_color=SUCCESS)

        # Fill info card (single video)
        if not is_playlist:
            self._title_lbl.configure(text=title[:80])
            self._channel_lbl.configure(text=info.get("uploader", "—"))
            self._duration_lbl.configure(text=format_duration(info.get("duration")))
            views = info.get("view_count")
            self._views_lbl.configure(text=f"{views:,}" if views else "—")

            # Async thumbnail
            thumb_url = self._best_thumbnail(info)
            if thumb_url:
                _load_thumbnail(thumb_url, self._on_thumbnail)

            self._info_card.grid()
            self._playlist_hint.configure(text="")
        else:
            self._info_card.grid_remove()
            self._playlist_hint.configure(
                text=f"⚡ Playlist detected – {len(entries)} videos will be downloaded.")

        # Populate quality dropdown
        if self._formats:
            labels = [f["label"] for f in self._formats]
            self._quality_combo.configure(values=labels)
            pref = self.config_data.get("default_quality", "1080p")
            self._quality_var.set(pref if pref in labels else labels[0])

        self._opts_card.grid()

    def _on_fetch_error(self, err: str) -> None:
        self._fetch_btn.configure(state="normal", text="Fetch Info")
        msg = self._friendly_fetch_error(err)
        self._fetch_status.configure(text=f"✗ {msg}", text_color=ACCENT)

    @staticmethod
    def _friendly_fetch_error(err: str) -> str:
        e = err.lower()
        if "video unavailable" in e:
            return "Video unavailable (private, deleted, or region-locked)."
        if "network" in e or "connection" in e:
            return "Network error. Check your internet connection."
        if "invalid url" in e or "unable to extract" in e:
            return "Invalid URL or unsupported format."
        return err[:120]

    @staticmethod
    def _best_thumbnail(info: dict) -> str | None:
        thumbs = info.get("thumbnails") or []
        if thumbs:
            # Prefer 16:9 ~ 1280×720
            for t in reversed(thumbs):
                w = t.get("width", 0)
                if w and w <= 1280:
                    return t.get("url")
            return thumbs[-1].get("url")
        return info.get("thumbnail")

    def _on_thumbnail(self, img) -> None:
        if img:
            self._thumb_lbl.configure(image=img, text="")

    def _open_in_browser(self) -> None:
        url = self._url_var.get().strip()
        if url:
            webbrowser.open(url)

    # ─── Mode change ──────────────────────────────────────────────────────

    def _on_mode_change(self, value: str) -> None:
        internal = self._mode_map.get(value, "video+audio")
        self._mode_var.set(internal)
        # Disable quality for audio-only
        state = "disabled" if internal == "audio" else "normal"
        self._quality_combo.configure(state=state)

    # ─── Download ─────────────────────────────────────────────────────────

    def _on_download(self) -> None:
        if not self._info:
            self._fetch_status.configure(text="⚠ Fetch video info first", text_color=WARNING)
            return

        url    = self._url_var.get().strip()
        folder = self._folder_var.get().strip()
        mode   = self._mode_map.get(self._mode_seg.get(), "video+audio")
        qual   = self._quality_var.get()
        fmt    = self._format_var.get()
        subs   = self._sub_var.get()
        lang   = self._sub_lang_var.get().strip() or "en"

        if not folder:
            folder = self.config_data.get("download_folder", str(Path.home() / "Downloads"))
        os.makedirs(folder, exist_ok=True)

        is_playlist = self._info.get("_type") == "playlist"
        entries = self._info.get("entries", []) if is_playlist else [self._info]

        for entry in entries:
            entry_url = entry.get("url") or entry.get("webpage_url") or url
            title = entry.get("title", "Video")
            self._start_download_task(entry_url, title, folder, mode, qual, fmt, subs, lang)

    def _start_download_task(self, url, title, folder, mode, quality, fmt, subs, lang) -> None:
        def on_progress(tid, prog):
            self.after(0, self._update_row, tid, prog)

        def on_done(tid, prog):
            self.after(0, self._on_task_done, tid, prog, title, url)

        task_id = self.manager.add_task(
            url=url, download_folder=folder, mode=mode,
            quality=quality, fmt=fmt, subtitles=subs, sub_lang=lang,
            on_progress=on_progress, on_done=on_done,
        )
        self.after(0, self._add_row, task_id, title)

    def _add_row(self, task_id: str, title: str) -> None:
        self._no_dl_lbl.grid_remove()
        row_idx = len(self._download_rows)
        row = DownloadRow(
            self._dl_rows_frame, task_id=task_id, title=title,
            manager=self.manager, remove_cb=self._remove_row,
        )
        row.grid(row=row_idx, column=0, sticky="ew", pady=(0, 6))
        self._download_rows[task_id] = row
        self._update_badge()

    def _update_row(self, task_id: str, prog: ProgressInfo) -> None:
        if row := self._download_rows.get(task_id):
            row.update_progress(prog)

    def _on_task_done(self, task_id: str, prog: ProgressInfo, title: str, url: str) -> None:
        self._update_badge()
        if prog.status == DownloadStatus.COMPLETED:
            add_to_history(self.config_data, {
                "title": title, "url": url,
                "timestamp": time.time(),
                "folder": self._folder_var.get(),
                "status": "completed",
            })
            self._refresh_history()

    def _remove_row(self, task_id: str) -> None:
        if row := self._download_rows.pop(task_id, None):
            row.destroy()
        self.manager.remove_task(task_id)
        if not self._download_rows:
            self._no_dl_lbl.grid(row=0, column=0, pady=12)
        self._update_badge()

    def _update_badge(self) -> None:
        n = self.manager.active_count()
        if n:
            self._active_badge.configure(text=f"  {n} active  ")
        else:
            self._active_badge.configure(text="")

    # ─── Browse ───────────────────────────────────────────────────────────

    def _browse_folder(self) -> None:
        current = self._folder_var.get() or str(Path.home())
        folder = filedialog.askdirectory(initialdir=current, title="Select Download Folder")
        if folder:
            self._folder_var.set(folder)
            self.config_data["download_folder"] = folder
            save_config(self.config_data)

    # ═════════════════════════════════════════════════════════════════════════
    #  Batch panel
    # ═════════════════════════════════════════════════════════════════════════

    def _build_batch_panel(self, parent: ctk.CTkFrame) -> None:
        parent.rowconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(parent, fg_color=DARK_BG)
        scroll.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
        scroll.columnconfigure(0, weight=1)

        ctk.CTkLabel(scroll, text="Batch Download",
                     font=ctk.CTkFont(size=20, weight="bold")).grid(
            row=0, column=0, padx=24, pady=(20, 4), sticky="w")
        ctk.CTkLabel(scroll, text="One URL per line. Supports videos and playlists.",
                     text_color=MUTED, font=ctk.CTkFont(size=12)).grid(
            row=1, column=0, padx=24, sticky="w")

        card = ctk.CTkFrame(scroll, fg_color=CARD_BG, corner_radius=12)
        card.grid(row=2, column=0, sticky="ew", padx=16, pady=12)
        card.columnconfigure(0, weight=1)

        self._batch_box = ctk.CTkTextbox(card, height=200, font=ctk.CTkFont(size=12))
        self._batch_box.grid(row=0, column=0, columnspan=2, padx=16, pady=(12, 8), sticky="ew")

        # Options row
        opts_row = ctk.CTkFrame(card, fg_color="transparent")
        opts_row.grid(row=1, column=0, columnspan=2, padx=16, pady=4, sticky="ew")

        ctk.CTkLabel(opts_row, text="Quality:", text_color=MUTED).pack(side="left")
        self._batch_quality = ctk.CTkComboBox(
            opts_row, values=["1080p", "720p", "480p", "360p", "240p"],
            width=100)
        self._batch_quality.set("720p")
        self._batch_quality.pack(side="left", padx=(6, 16))

        ctk.CTkLabel(opts_row, text="Mode:", text_color=MUTED).pack(side="left")
        self._batch_mode = ctk.CTkComboBox(
            opts_row, values=["video+audio", "audio", "video"],
            width=120)
        self._batch_mode.set("video+audio")
        self._batch_mode.pack(side="left", padx=(6, 16))

        # Batch download button
        ctk.CTkButton(
            card, text="⬇  Download All", height=44,
            fg_color=ACCENT, hover_color=ACCENT2,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._on_batch_download,
        ).grid(row=2, column=0, columnspan=2, padx=16, pady=(8, 16), sticky="ew")

        # Status
        self._batch_status = ctk.CTkLabel(scroll, text="", text_color=MUTED,
                                           font=ctk.CTkFont(size=12))
        self._batch_status.grid(row=3, column=0, padx=24, pady=4, sticky="w")

    def _on_batch_download(self) -> None:
        raw = self._batch_box.get("1.0", "end").strip()
        if not raw:
            self._batch_status.configure(text="⚠ Enter at least one URL", text_color=WARNING)
            return

        urls = [u.strip() for u in raw.splitlines() if u.strip()]
        valid = [u for u in urls if is_valid_youtube_url(u)]
        invalid = len(urls) - len(valid)

        if not valid:
            self._batch_status.configure(text="✗ No valid YouTube URLs found.", text_color=ACCENT)
            return

        folder = self.config_data.get("download_folder", str(Path.home() / "Downloads"))
        quality = self._batch_quality.get()
        mode = self._batch_mode.get()

        for url in valid:
            self._start_download_task(url, url[:60], folder, mode, quality, "mp4", False, "en")

        self._show_panel("download")
        msg = f"✓ Queued {len(valid)} download(s)."
        if invalid:
            msg += f" {invalid} invalid URL(s) skipped."
        self._batch_status.configure(text=msg, text_color=SUCCESS)

    # ═════════════════════════════════════════════════════════════════════════
    #  History panel
    # ═════════════════════════════════════════════════════════════════════════

    def _build_history_panel(self, parent: ctk.CTkFrame) -> None:
        parent.rowconfigure(1, weight=1)

        # Header
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 8))
        hdr.columnconfigure(1, weight=1)
        ctk.CTkLabel(hdr, text="Download History",
                     font=ctk.CTkFont(size=20, weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(hdr, text="🗑 Clear History", width=120, height=32,
                      fg_color=ACCENT, hover_color=ACCENT2,
                      command=self._clear_history).grid(row=0, column=2)

        # Search
        self._history_search = ctk.CTkEntry(
            parent, placeholder_text="🔍 Search history…", height=36,
            font=ctk.CTkFont(size=12))
        self._history_search.grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 8))
        self._history_search.bind("<KeyRelease>", lambda _: self._refresh_history())

        # Scrollable list
        self._history_scroll = ctk.CTkScrollableFrame(parent, fg_color=DARK_BG)
        self._history_scroll.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 16))
        self._history_scroll.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        self._refresh_history()

    def _refresh_history(self) -> None:
        for w in self._history_scroll.winfo_children():
            w.destroy()

        query = ""
        if hasattr(self, "_history_search"):
            query = self._history_search.get().lower()

        history = self.config_data.get("download_history", [])
        if query:
            history = [h for h in history if query in h.get("title", "").lower()
                       or query in h.get("url", "").lower()]

        if not history:
            ctk.CTkLabel(self._history_scroll, text="No history yet.",
                         text_color=MUTED, font=ctk.CTkFont(size=13)).grid(
                row=0, column=0, pady=32)
            return

        for i, entry in enumerate(history):
            row = ctk.CTkFrame(self._history_scroll, fg_color=CARD_BG, corner_radius=8)
            row.grid(row=i, column=0, sticky="ew", pady=(0, 6))
            row.columnconfigure(0, weight=1)

            ctk.CTkLabel(row, text=entry.get("title", "—")[:80], anchor="w",
                         font=ctk.CTkFont(size=12, weight="bold")).grid(
                row=0, column=0, padx=14, pady=(8, 2), sticky="ew")
            ts = format_timestamp(entry.get("timestamp", 0))
            ctk.CTkLabel(row, text=f"{ts}  •  {entry.get('folder', '—')}",
                         text_color=MUTED, anchor="w",
                         font=ctk.CTkFont(size=10)).grid(
                row=1, column=0, padx=14, pady=(0, 2), sticky="ew")

            btn_row = ctk.CTkFrame(row, fg_color="transparent")
            btn_row.grid(row=0, column=1, rowspan=2, padx=10, pady=4)
            ctk.CTkButton(btn_row, text="↗ URL", width=70, height=26,
                          fg_color="#333", font=ctk.CTkFont(size=10),
                          command=lambda u=entry.get("url", ""): webbrowser.open(u)
                          ).pack(side="left", padx=2)
            ctk.CTkButton(btn_row, text="📂 Folder", width=80, height=26,
                          fg_color="#333", font=ctk.CTkFont(size=10),
                          command=lambda f=entry.get("folder", ""): self._open_folder(f)
                          ).pack(side="left", padx=2)

    def _clear_history(self) -> None:
        if messagebox.askyesno("Clear History", "Clear all download history?"):
            clear_history(self.config_data)
            self._refresh_history()

    @staticmethod
    def _open_folder(path: str) -> None:
        if path and os.path.isdir(path):
            import subprocess, sys
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])

    # ═════════════════════════════════════════════════════════════════════════
    #  Settings panel
    # ═════════════════════════════════════════════════════════════════════════

    def _build_settings_panel(self, parent: ctk.CTkFrame) -> None:
        scroll = ctk.CTkScrollableFrame(parent, fg_color=DARK_BG)
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.columnconfigure(0, weight=1)

        ctk.CTkLabel(scroll, text="Settings",
                     font=ctk.CTkFont(size=20, weight="bold")).grid(
            row=0, column=0, padx=24, pady=(20, 16), sticky="w")

        def section(row, title):
            ctk.CTkLabel(scroll, text=title, text_color=ACCENT,
                         font=ctk.CTkFont(size=12, weight="bold")).grid(
                row=row, column=0, padx=24, pady=(12, 4), sticky="w")

        def row_widget(row, label, widget_fn):
            f = ctk.CTkFrame(scroll, fg_color=CARD_BG, corner_radius=10)
            f.grid(row=row, column=0, sticky="ew", padx=16, pady=3)
            f.columnconfigure(0, weight=1)
            ctk.CTkLabel(f, text=label, font=ctk.CTkFont(size=12)).grid(
                row=0, column=0, padx=16, pady=12, sticky="w")
            widget_fn(f)
            return f

        # Appearance
        section(1, "APPEARANCE")

        def _theme_widget(parent_frame):
            seg = ctk.CTkSegmentedButton(
                parent_frame, values=["Dark", "Light", "System"],
                command=self._set_theme_from_settings)
            seg.set(self.config_data.get("appearance_mode", "dark").capitalize())
            seg.grid(row=0, column=1, padx=16, pady=8)

        row_widget(2, "Color Theme", _theme_widget)

        # Downloads
        section(3, "DOWNLOADS")

        def _folder_widget(parent_frame):
            v = tk.StringVar(value=self.config_data.get("download_folder", ""))
            e = ctk.CTkEntry(parent_frame, textvariable=v, width=260, font=ctk.CTkFont(size=11))
            e.grid(row=0, column=1, padx=8, pady=8)
            def _browse():
                d = filedialog.askdirectory()
                if d:
                    v.set(d)
                    self.config_data["download_folder"] = d
                    self._folder_var.set(d)
                    save_config(self.config_data)
            ctk.CTkButton(parent_frame, text="Browse", width=70, height=28,
                          fg_color="#333", command=_browse).grid(row=0, column=2, padx=8)

        row_widget(4, "Default Download Folder", _folder_widget)

        def _concurrent_widget(parent_frame):
            v = tk.IntVar(value=self.config_data.get("max_concurrent", 2))
            slider = ctk.CTkSlider(parent_frame, from_=1, to=5, number_of_steps=4,
                                   variable=v, width=200)
            slider.grid(row=0, column=1, padx=16, pady=12)
            lbl = ctk.CTkLabel(parent_frame, text=str(v.get()), width=24)
            lbl.grid(row=0, column=2, padx=4)
            def _update(val):
                lbl.configure(text=str(int(val)))
                self.config_data["max_concurrent"] = int(val)
                save_config(self.config_data)
            slider.configure(command=_update)

        row_widget(5, "Max Concurrent Downloads", _concurrent_widget)

        # Subtitles
        section(6, "SUBTITLES")

        def _sub_widget(parent_frame):
            v = tk.BooleanVar(value=self.config_data.get("download_subtitles", False))
            def _toggle():
                self.config_data["download_subtitles"] = v.get()
                save_config(self.config_data)
            ctk.CTkSwitch(parent_frame, text="Auto-download subtitles",
                          variable=v, command=_toggle).grid(row=0, column=1, padx=16, pady=12)

        row_widget(7, "Subtitles", _sub_widget)

        # Clipboard
        section(8, "CLIPBOARD")

        def _clip_widget(parent_frame):
            v = tk.BooleanVar(value=self.config_data.get("auto_detect_clipboard", True))
            def _toggle():
                self.config_data["auto_detect_clipboard"] = v.get()
                save_config(self.config_data)
            ctk.CTkSwitch(parent_frame, text="Auto-detect YouTube URLs from clipboard",
                          variable=v, command=_toggle).grid(row=0, column=1, padx=16, pady=12)

        row_widget(9, "Clipboard Detection", _clip_widget)

        # About
        section(10, "ABOUT")
        about = ctk.CTkFrame(scroll, fg_color=CARD_BG, corner_radius=10)
        about.grid(row=11, column=0, sticky="ew", padx=16, pady=3)
        ctk.CTkLabel(about, text="YT Downloader Pro  •  v1.0.0  •  Powered by yt-dlp",
                     text_color=MUTED, font=ctk.CTkFont(size=11)).pack(padx=16, pady=12)

    def _set_theme_from_settings(self, value: str) -> None:
        mode = value.lower()
        ctk.set_appearance_mode(mode)
        self.config_data["appearance_mode"] = mode
        save_config(self.config_data)

    # ═════════════════════════════════════════════════════════════════════════
    #  Theme toggle (sidebar switch)
    # ═════════════════════════════════════════════════════════════════════════

    def _toggle_theme(self) -> None:
        mode = self._theme_switch.get()
        ctk.set_appearance_mode(mode)
        self.config_data["appearance_mode"] = mode
        save_config(self.config_data)

    # ═════════════════════════════════════════════════════════════════════════
    #  Clipboard polling
    # ═════════════════════════════════════════════════════════════════════════

    def _poll_clipboard(self) -> None:
        if self.config_data.get("auto_detect_clipboard", True):
            url = detect_clipboard_url(self)
            if url and url != self._last_clipboard:
                self._last_clipboard = url
                current = self._url_var.get().strip()
                if not current:
                    self._url_var.set(url)
                self._clip_hint.configure(text="📋 YouTube URL detected from clipboard")
        self.after(2000, self._poll_clipboard)

    # ═════════════════════════════════════════════════════════════════════════
    #  Close handler
    # ═════════════════════════════════════════════════════════════════════════

    def _on_close(self) -> None:
        active = self.manager.active_count()
        if active and not messagebox.askyesno(
            "Quit", f"There are {active} active download(s).\nQuit anyway?"):
            return
        self.manager.cancel_all()
        save_config(self.config_data)
        self.destroy()
