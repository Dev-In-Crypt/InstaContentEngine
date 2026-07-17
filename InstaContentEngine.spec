# PyInstaller spec for the standalone Windows build.
#
# Design: the exe bundles Python + all third-party deps + the ffmpeg binary +
# pywebview. The app's own `backend/` package is NOT frozen here — build_exe.ps1
# copies it loose into dist/ beside the exe, so its .env / insta.db / uploads /
# static all resolve to real editable files (see InstaContentEngine.pyw FROZEN).
#
# Because backend/ is loose, PyInstaller can't discover the third-party packages
# it imports — they're listed explicitly in hiddenimports below.
from PyInstaller.utils.hooks import collect_all, collect_submodules, collect_data_files

datas, binaries, hiddenimports = [], [], []

# The two packages that ship data/binaries PyInstaller won't find on its own:
#   imageio_ffmpeg → the ~83MB ffmpeg.exe (offline Reels)
#   webview        → pywebview's GUI backend + JS assets
for _pkg in ("imageio_ffmpeg", "webview"):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

datas += collect_data_files("certifi")

# Every third-party package the loose backend/ imports, named explicitly.
hiddenimports += [
    "fastapi", "starlette", "pydantic", "pydantic_settings", "multipart",
    "sqlalchemy", "sqlalchemy.dialects.sqlite", "aiosqlite",
    "httpx", "httpcore", "certifi", "truststore", "cryptography",
    "PIL", "PIL._imaging", "numpy",
    "imageio", "imageio.plugins.ffmpeg",
    "aiofiles", "anyio", "h11", "sniffio",
    "authlib", "authlib.oauth1",   # X/Twitter OAuth 1.0a signing
]
# Dynamic submodules PyInstaller commonly misses (imported by string / lazily):
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("apscheduler")
hiddenimports += collect_submodules("fastapi")
hiddenimports += collect_submodules("starlette")

a = Analysis(
    ["InstaContentEngine.pyw"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Test-only and non-desktop deps.
        "pytest", "pytest_asyncio", "pytest_httpx", "respx",
        "telegram",   # Telegram bot is a separate entry point
        "asyncpg", "psycopg2",   # Postgres/cloud only; desktop uses sqlite
        # Heavy ML/scientific stack installed on the dev machine but never used by
        # this app (only numpy is). Without these excludes PyInstaller drags in
        # torch alone at ~3.5GB via transitive references. The app imports none of
        # them — see backend/ (Pillow + numpy + imageio-ffmpeg is the whole media
        # stack).
        "torch", "torchaudio", "torchvision", "transformers", "onnxruntime",
        "scipy", "sklearn", "pandas", "matplotlib", "nltk", "av",
        "grpc", "tensorflow", "sympy", "numba",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="InstaContentEngine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # windowed: no console window (matches the .pyw)
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="InstaContentEngine",   # → dist/InstaContentEngine/
)
