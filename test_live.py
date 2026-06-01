"""
Live integration test — requires real API keys in backend/.env
and the server running (start.bat or manually).

Usage:
    python test_live.py                  # uses stock photos, default models
    python test_live.py --source ai_gen  # AI-generated images
    python test_live.py --format carousel_3 --topic "Top 5 AI tools"
"""
import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx

BASE = "http://localhost:8000"
OUT  = Path("test_output")

# ── ANSI colours ────────────────────────────────────────────────────────────
G  = "\033[92m"   # green
Y  = "\033[93m"   # yellow
R  = "\033[91m"   # red
B  = "\033[94m"   # blue
C  = "\033[96m"   # cyan
W  = "\033[97m"   # white bold
DIM= "\033[2m"
RST= "\033[0m"

def ok(msg):   print(f"  {G}✓{RST}  {msg}")
def info(msg): print(f"  {B}→{RST}  {msg}")
def warn(msg): print(f"  {Y}⚠{RST}  {msg}")
def err(msg):  print(f"  {R}✗{RST}  {msg}")
def head(msg): print(f"\n{W}{msg}{RST}")
def sep():     print(f"  {DIM}{'─'*60}{RST}")


async def check_server(client: httpx.AsyncClient) -> bool:
    head("1 / Checking server")
    for attempt in range(1, 16):
        try:
            r = await client.get("/health", timeout=3)
            if r.status_code == 200:
                ok(f"Server is up  ({BASE})")
                return True
        except Exception:
            pass
        info(f"Waiting for server… attempt {attempt}/15")
        await asyncio.sleep(2)
    err("Server did not respond. Run start.bat first.")
    return False


async def check_models(client: httpx.AsyncClient):
    head("2 / Checking configured models")
    r = await client.get("/api/models/defaults")
    d = r.json()
    ok(f"Text model  : {C}{d['text_model']}{RST}")
    ok(f"Image model : {C}{d['image_model']}{RST}")
    return d


async def generate(client: httpx.AsyncClient, args) -> dict:
    head("3 / Generating post")
    payload = {
        "topic":        args.topic,
        "format":       args.format,
        "tone":         args.tone,
        "apply_branding": True,
    }
    info(f"Topic  : {args.topic}")
    info(f"Format : {args.format}")
    info(f"Source : {args.source}")
    sep()
    print(f"  {Y}⏳ Calling API… this may take 30–90 s{RST}", flush=True)

    t0 = time.time()
    try:
        r = await client.post(
            "/api/posts/generate",
            json=payload,
            timeout=180,
        )
    except httpx.ReadTimeout:
        err("Timed out (180 s). Try a faster model in .env, e.g. google/gemini-2.5-flash")
        sys.exit(1)

    elapsed = time.time() - t0

    if r.status_code != 200:
        err(f"HTTP {r.status_code}")
        try:
            print(json.dumps(r.json(), indent=2))
        except Exception:
            print(r.text)
        sys.exit(1)

    post = r.json()
    ok(f"Generated in {elapsed:.1f} s  (post id: {DIM}{post['id']}{RST})")
    return post


def show_post(post: dict):
    head("4 / Results")

    sep()
    print(f"  {W}HOOK:{RST}")
    print(f"  {C}{post.get('hook', '—')}{RST}\n")

    print(f"  {W}CAPTION:{RST}")
    for line in post["caption"].splitlines():
        print(f"  {line}")
    print()

    tags = post.get("hashtags", [])
    print(f"  {W}HASHTAGS ({len(tags)}):{RST}")
    print(f"  {' '.join(tags)}\n")

    print(f"  {W}CTA:{RST}")
    print(f"  {post.get('cta', '—')}\n")

    slides = post.get("slides", [])
    print(f"  {W}SLIDES:{RST}  {len(slides)} slide(s)")
    for s in slides:
        print(f"    Slide {s['slide_number']}  •  source: {s['image_source']}  •  {s['image_url']}")


async def download_slides(client: httpx.AsyncClient, post: dict):
    head("5 / Downloading slide images")
    OUT.mkdir(exist_ok=True)

    saved = []
    for slide in post["slides"]:
        url  = f"{BASE}{slide['image_url']}"
        dest = OUT / f"slide_{slide['slide_number']:02d}.jpg"
        r = await client.get(url, timeout=30)
        dest.write_bytes(r.content)
        ok(f"Saved  {dest}  ({len(r.content)//1024} KB)")
        saved.append(dest)

    # Save caption text
    caption_file = OUT / "caption.txt"
    caption_file.write_text(
        f"{post['caption']}\n\n---\n{' '.join(post.get('hashtags', []))}\n",
        encoding="utf-8",
    )
    ok(f"Saved  {caption_file}")

    # Save full JSON
    json_file = OUT / "post.json"
    json_file.write_text(json.dumps(post, indent=2, ensure_ascii=False), encoding="utf-8")
    ok(f"Saved  {json_file}")

    return saved


async def export_zip(client: httpx.AsyncClient, post: dict):
    head("6 / Exporting ZIP")
    r = await client.post(f"/api/posts/{post['id']}/export", timeout=30)
    if r.status_code != 200:
        warn("Export failed — skipping")
        return
    zip_path = OUT / "post_template.zip"
    zip_path.write_bytes(r.content)
    ok(f"Saved  {zip_path}  ({len(r.content)//1024} KB)")


def summary(post: dict, saved: list[Path]):
    head("Done ✓")
    sep()
    print(f"  Output folder : {W}{OUT.resolve()}{RST}")
    print(f"  Slides saved  : {len(saved)}")
    print(f"  Post ID       : {DIM}{post['id']}{RST}")
    sep()
    print(f"\n  {G}Open the folder to see your generated images:{RST}")
    print(f"  {C}explorer {OUT.resolve()}{RST}\n")


async def main():
    parser = argparse.ArgumentParser(description="Live integration test for InstaContentEngine")
    parser.add_argument("--topic",  default="5 ways AI is changing social media marketing in 2026")
    parser.add_argument("--format", default="single",
                        choices=["single", "carousel_3", "carousel_5", "carousel_10", "infographic"])
    parser.add_argument("--source", default="stock", choices=["stock", "ai_gen"])
    parser.add_argument("--tone",   default="professional",
                        choices=["professional", "casual", "educational", "inspirational"])
    parser.add_argument("--no-zip", action="store_true", help="Skip ZIP export step")
    args = parser.parse_args()

    print(f"\n{W}{'═'*64}{RST}")
    print(f"{W}  InstaContentEngine — Live Integration Test{RST}")
    print(f"{W}{'═'*64}{RST}")

    async with httpx.AsyncClient(base_url=BASE) as client:
        if not await check_server(client):
            sys.exit(1)

        await check_models(client)
        post   = await generate(client, args)
        show_post(post)
        saved  = await download_slides(client, post)
        if not args.no_zip:
            await export_zip(client, post)
        summary(post, saved)


if __name__ == "__main__":
    # Windows fix for coloured output
    if sys.platform == "win32":
        os.system("")
    asyncio.run(main())
