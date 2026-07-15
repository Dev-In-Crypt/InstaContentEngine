"""
InstaContentEngine — single desktop entry-point.

Workflow when the user double-clicks this file:
  1. Verify Python deps; if any are missing, install requirements.txt
     (showing a small Tk progress window — Tk is in stdlib).
  2. Ensure backend/.env exists (copy .env.example if needed).
  3. If OPENROUTER_API_KEY isn't set, open backend/.env in Notepad
     and exit so the user can paste the key.
  4. Pick a free local TCP port.
  5. Start FastAPI/uvicorn in a background thread.
  6. Wait until the server answers /health.
  7. Open a native window via pywebview pointing at the local server.
  8. When the window closes, shut down the server cleanly.

No browser. No CMD window. No second launcher.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent.resolve()
BACKEND = HERE / "backend"
ENV = BACKEND / ".env"
ENV_EX = BACKEND / ".env.example"
REQ = BACKEND / "requirements.txt"


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Dependency check + pip install (with Tk progress window)
# ─────────────────────────────────────────────────────────────────────────────

_REQUIRED_MODULES = (
    "webview",            # pywebview
    "fastapi",
    "uvicorn",
    "sqlalchemy",
    "httpx",
    "PIL",                # pillow
    "aiosqlite",
    "pydantic_settings",
)


def _deps_ok() -> bool:
    import importlib
    for mod in _REQUIRED_MODULES:
        try:
            importlib.import_module(mod)
        except ImportError:
            return False
    return True


def _show_tk_progress(message: str):
    """Return a Tk root displaying `message`. Caller must `.destroy()` it later."""
    import tkinter as tk
    root = tk.Tk()
    root.title("InstaContentEngine — Setup")
    root.geometry("520x140")
    root.configure(bg="#0d0d1a")
    root.resizable(False, False)
    tk.Label(
        root, text=message, font=("Segoe UI", 11),
        bg="#0d0d1a", fg="#e2e8f0", pady=24, justify="center",
    ).pack(expand=True, fill="both")
    root.update()
    return root


def _show_error(title: str, body: str) -> None:
    try:
        import tkinter as tk
        import tkinter.messagebox as mb
        tk.Tk().withdraw()
        mb.showerror(title, body)
    except Exception:
        # Last-ditch: write a sibling .txt the user can find.
        (HERE / "InstaContentEngine_error.log").write_text(
            f"{title}\n\n{body}\n", encoding="utf-8"
        )


def _install_dependencies() -> None:
    if not REQ.exists():
        _show_error("Missing requirements.txt", f"Expected at:\n{REQ}")
        sys.exit(1)

    root = None
    try:
        root = _show_tk_progress(
            "📦  Installing dependencies, please wait…\n"
            "This runs once on first launch (about a minute)."
        )
    except Exception:
        pass

    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(REQ),
         "-q", "--no-warn-script-location"],
        capture_output=True, text=True,
    )
    if root is not None:
        try:
            root.destroy()
        except Exception:
            pass

    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "")[-1200:]
        _show_error(
            "Dependency installation failed",
            f"`pip install -r {REQ.name}` failed:\n\n{tail}\n\n"
            f"Try running manually:\n  pip install -r backend\\requirements.txt",
        )
        sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# 2 & 3.  .env bootstrap + API key check
# ─────────────────────────────────────────────────────────────────────────────

_KEY_PLACEHOLDERS = {"", "...", "sk-or-v1-...", "your-key-here", "changeme"}


def _ensure_env() -> None:
    if ENV.exists():
        return
    if ENV_EX.exists():
        import shutil
        shutil.copy(ENV_EX, ENV)
    else:
        ENV.write_text(
            "# Edit this file and add your OpenRouter API key.\n"
            "OPENROUTER_API_KEY=\n"
            "DEFAULT_TEXT_MODEL=anthropic/claude-sonnet-4\n"
            "DEFAULT_IMAGE_MODEL=openai/dall-e-3\n",
            encoding="utf-8",
        )


def _api_key_missing() -> bool:
    try:
        lines = ENV.read_text(encoding="utf-8", errors="ignore").splitlines()
    except FileNotFoundError:
        return True
    for line in lines:
        line = line.strip()
        if line.startswith("OPENROUTER_API_KEY="):
            val = line.split("=", 1)[1].strip().strip('"').strip("'")
            return val in _KEY_PLACEHOLDERS
    return True


def _prompt_for_api_key() -> None:
    """Open .env in Notepad and show a message, then exit so user can paste and restart."""
    try:
        import tkinter as tk
        import tkinter.messagebox as mb
        root = tk.Tk()
        root.withdraw()
        if mb.askokcancel(
            "One-time setup",
            "Almost there!\n\n"
            "Your backend\\.env file needs an OpenRouter API key.\n\n"
            "Click OK to open .env in Notepad.\n"
            "1) Get a key from openrouter.ai → Keys\n"
            "2) Set  OPENROUTER_API_KEY=sk-or-v1-...\n"
            "3) Save the file and re-launch InstaContentEngine.",
        ):
            subprocess.Popen(["notepad.exe", str(ENV)])
    except Exception:
        # Fallback: just open Notepad without prompting.
        try:
            subprocess.Popen(["notepad.exe", str(ENV)])
        except Exception:
            pass
    sys.exit(0)


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Pick a free port
# ─────────────────────────────────────────────────────────────────────────────

def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ─────────────────────────────────────────────────────────────────────────────
# 5 & 6.  Start uvicorn in background, wait for /health
# ─────────────────────────────────────────────────────────────────────────────

def _start_server(port: int):
    """Return (server, thread). Caller can stop via `server.should_exit = True`."""
    # Make sure backend/ is importable as the working directory of the FastAPI app.
    os.chdir(BACKEND)
    sys.path.insert(0, str(BACKEND))

    import uvicorn
    from main import app  # backend/main.py:app

    config = uvicorn.Config(
        app, host="127.0.0.1", port=port,
        log_level="warning", access_log=False,
    )
    server = uvicorn.Server(config)

    def _run():
        try:
            asyncio.run(server.serve())
        except Exception as exc:
            # Server crashed — log to file for diagnosis.
            (HERE / "InstaContentEngine_error.log").write_text(
                f"Server crashed:\n{exc!r}\n", encoding="utf-8"
            )

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return server, thread


def _wait_for_server(port: int, timeout_s: int = 30) -> bool:
    deadline = time.time() + timeout_s
    url = f"http://127.0.0.1:{port}/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# 7 & 8.  Native window via pywebview, shutdown on close
# ─────────────────────────────────────────────────────────────────────────────

def _open_window(port: int) -> None:
    import webview
    webview.create_window(
        "InstaContentEngine",
        f"http://127.0.0.1:{port}",
        width=1200, height=820,
        min_size=(900, 600),
        background_color="#0d0d1a",
    )
    # Blocks the main thread until the window is closed.
    webview.start()


def main() -> None:
    # Step 1: deps
    if not _deps_ok():
        _install_dependencies()
        if not _deps_ok():
            _show_error(
                "Dependencies still missing",
                "Some required packages are still not importable after install.\n\n"
                "Open a terminal and run:\n"
                "  pip install -r backend\\requirements.txt",
            )
            sys.exit(1)

    # Step 2: .env
    _ensure_env()

    # Step 3: api key
    if _api_key_missing():
        _prompt_for_api_key()  # exits

    # Step 4-6: server
    port = _pick_free_port()
    server, server_thread = _start_server(port)
    if not _wait_for_server(port):
        _show_error(
            "Server did not start",
            "InstaContentEngine could not start the local backend within 30 seconds.\n\n"
            f"Check  {HERE / 'InstaContentEngine_error.log'}  for details.",
        )
        sys.exit(1)

    # Step 7: window (blocks)
    try:
        _open_window(port)
    finally:
        # Step 8: clean shutdown
        try:
            server.should_exit = True
        except Exception:
            pass
        server_thread.join(timeout=5)


if __name__ == "__main__":
    main()
