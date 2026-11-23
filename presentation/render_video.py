"""Render a presentation page to an MP4, frame by frame, with headless Chrome and ffmpeg.

    uv run python presentation/render_video.py                 # demo30.html -> TrailProof_demo_30s.mp4 (30 s)
    uv run python presentation/render_video.py --page index.html --seconds 62 --fps 5 --out presentation/TrailProof_demo.mp4

Both pages draw any moment of their timeline on request (``?t=<seconds>``),
so every frame is exact. demo30.html computes its motion from the time
itself, so the rendered video moves as smoothly as the live page.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHROME = [r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
          r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
          "/usr/bin/google-chrome", "/usr/bin/chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]


def find_chrome() -> str | None:
    return next((c for c in CHROME if Path(c).exists()), None)


def screenshot(chrome: str, url: str, out: Path, profile: Path, size: str = "1920,1080") -> None:
    subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--window-size={size}",
                    f"--user-data-dir={profile}", "--virtual-time-budget=4000", f"--screenshot={out}", url],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--page", default="demo30.html")
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--fps", type=int, default=12)
    ap.add_argument("--out", default=str(HERE / "TrailProof_demo_30s.mp4"))
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    chrome, ffmpeg = find_chrome(), shutil.which("ffmpeg")
    if not chrome or not ffmpeg:
        print("needs Chrome or Edge, and ffmpeg on PATH", file=sys.stderr)
        return 2
    frames = HERE / "_frames"
    shutil.rmtree(frames, ignore_errors=True)
    frames.mkdir()
    url = (HERE / args.page).as_uri()
    n = int(round(args.seconds * args.fps))

    def chunk(worker: int) -> None:          # one Chrome profile per worker, so they do not fight over a lock
        for i in range(worker, n, args.workers):
            t = min(i / args.fps, args.seconds - 0.001)
            screenshot(chrome, f"{url}?t={t:.4f}", frames / f"f{i:05d}.png", frames / f"p{worker}")

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(chunk, range(args.workers)))
    missing = [i for i in range(n) if not (frames / f"f{i:05d}.png").exists()]
    if missing:
        print(f"{len(missing)} frames failed, e.g. {missing[:5]}", file=sys.stderr)
        return 1
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-framerate", str(args.fps), "-i", str(frames / "f%05d.png"),
                    "-vf", "scale=1920:1080,format=yuv420p", "-c:v", "libx264", "-crf", "18", "-r", "30", "-movflags", "+faststart", args.out],
                   check=True)
    shutil.rmtree(frames, ignore_errors=True)
    print(f"wrote {args.out} ({n} frames at {args.fps} fps, {args.seconds:g} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
