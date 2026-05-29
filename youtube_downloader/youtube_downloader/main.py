"""
YouTube Video Downloader - Main Entry Point
==========================================
Initializes the application, loads configuration,
and launches the main GUI window.
"""

import sys
import os
import tkinter as tk
from tkinter import messagebox

# ── Dependency check before anything else ──────────────────────────────────────
REQUIRED = ["customtkinter", "yt_dlp", "PIL", "requests"]

missing = []
for pkg in REQUIRED:
    try:
        __import__(pkg)
    except ImportError:
        missing.append(pkg)

if missing:
    # Try to show a friendly GUI error if tkinter works
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Missing Dependencies",
            f"Please install missing packages:\n\n  pip install {' '.join(missing)}\n\n"
            "Or run:  pip install -r requirements.txt",
        )
        root.destroy()
    except Exception:
        print(f"[ERROR] Missing packages: {', '.join(missing)}")
        print("Run: pip install -r requirements.txt")
    sys.exit(1)

# ── Normal imports (dependencies are present) ─────────────────────────────────
import customtkinter as ctk
from utils import load_config, ensure_download_dir
from ui import App


def main() -> None:
    """Application entry point."""
    # Load / create config
    config = load_config()

    # Ensure default download folder exists
    ensure_download_dir(config.get("download_folder", os.path.expanduser("~/Downloads")))

    # Apply saved appearance settings
    ctk.set_appearance_mode(config.get("appearance_mode", "dark"))
    ctk.set_default_color_theme("blue")

    # Build and run the app
    app = App(config)
    app.mainloop()


if __name__ == "__main__":
    main()
