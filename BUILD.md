# Standalone Windows build

Package the app as a folder with a `.exe` the client runs **without installing
Python**. Verified working: the frozen exe boots the full FastAPI stack, serves
the UI, and bundles the ffmpeg binary for offline Reels.

## For the developer — building it

Requirements: Windows, Python 3.11 with the project deps installed, plus
PyInstaller.

```powershell
pip install pyinstaller
.\build_exe.ps1
```

This produces:

- `dist\InstaContentEngine\` — `InstaContentEngine.exe` + `_internal\` (bundled
  Python and dependencies) + a loose `backend\` folder beside the exe.
- `InstaContentEngine-standalone.zip` — the deliverable (~210 MB).

### How it's put together

PyInstaller (`InstaContentEngine.spec`) bundles Python, every third-party
dependency, the ffmpeg binary (via `imageio_ffmpeg`, ~83 MB — this is what makes
Reels work offline), and the pywebview GUI backend. The app's own `backend/`
package is **not** frozen — the build script copies it loose next to the exe, so
`backend\.env`, `insta.db`, `uploads\` and `static\` all resolve to real,
editable files on disk (the launcher detects `sys.frozen` and anchors paths on
the exe's folder).

The spec excludes a heavy ML stack (torch, scipy, transformers, …) that happens
to be installed on some dev machines but that this app never imports — without
those excludes PyInstaller drags in ~4 GB.

### Verifying a build without clicking

```powershell
dist\InstaContentEngine\InstaContentEngine.exe --selfcheck
```

Starts the server headless, checks `/health`, writes `selfcheck_result.txt`
(`SELFCHECK OK`) and exits 0. If it prints a `ModuleNotFoundError`, add that
module to `hiddenimports` in `InstaContentEngine.spec` and rebuild — PyInstaller
of a stack this size usually needs a couple of such rounds.

Note: antivirus (e.g. Avast) briefly locks freshly built binaries; the build
script kills a stale process and retries the cleanup, but if a build fails on a
locked `dist\`, just rerun it.

## For the client — running it

1. Unzip `InstaContentEngine-standalone.zip` anywhere.
2. Open `backend\.env` in a text editor and set `OPENROUTER_API_KEY=...`
   (get a key at openrouter.ai → Keys). Optional keys — Unsplash/Pexels,
   Instagram, imgbb — are documented in `backend\.env.example`.
3. Double-click `InstaContentEngine.exe`. The app window opens in a few seconds.

No Python needed. Requires Windows 10/11 with the Edge WebView2 runtime (present
by default on modern Windows). **Don't move the folder after the first run** —
the database stores absolute image paths.
