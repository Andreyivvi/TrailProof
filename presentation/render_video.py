"""Render presentation/index.html to an MP4, frame by frame, with headless Chrome and ffmpeg.

    uv run python presentation/render_video.py            # writes presentation/TrailProof_demo.mp4

Each frame is a still of the page at ``?t=<seconds>`` (the page renders any
moment of its timeline on request), so the result is exact but without the
in-between easing of the live page. For the smoothest version, screen-record
the live page instead.
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fps", type=int, default=5)
    ap.add_argument("--seconds", type=float, default=62.0, help="60 s of story plus a hold on the last card")
    ap.add_argument("--out", default=str(HERE / "TrailProof_demo.mp4"))
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    chrome = next((c for c in CHROME if Path(c).exists()), None)
    ffmpeg = shutil.which("ffmpeg")
    if not chrome or not ffmpeg:
        print("needs Chrome or Edge, and ffmpeg on PATH", file=sys.stderr)
        return 2
    frames = HERE / "_frames"
    shutil.rmtree(frames, ignore_errors=True)
    frames.mkdir()
    url = (HERE / "index.html").as_uri()
    n = int(args.seconds * args.fps)

    def shot(i: int) -> None:
        t = min(i / args.fps, 59.99)
        profile = frames / f"p{i % args.workers}"
        subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--window-size=1920,1080",
                        f"--user-data-dir={profile}", "--virtual-time-budget=4000",
                        f"--screenshot={frames / f'f{i:05d}.png'}", f"{url}?t={t:.3f}"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)

    # one Chrome profile per worker, so parallel instances do not fight over a profile lock
    for w in range(args.workers):
        with ThreadPoolExecutor(1) as pool:
            pass
    with ThreadPoolExecutor(args.workers) as pool:
        chunks = [range(w, n, args.workers) for w in range(args.workers)]
        list(pool.map(lambda chunk: [shot(i) for i in chunk], chunks))
    missing = [i for i in range(n) if not (frames / f"f{i:05d}.png").exists()]
    if missing:
        print(f"{len(missing)} frames failed, e.g. {missing[:5]}", file=sys.stderr)
        return 1
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-framerate", str(args.fps), "-i", str(frames / "f%05d.png"),
                    "-vf", "scale=1920:1080,format=yuv420p", "-c:v", "libx264", "-r", "30", "-movflags", "+faststart", args.out],
                   check=True)
    shutil.rmtree(frames, ignore_errors=True)
    print(f"wrote {args.out} ({n} frames at {args.fps} fps)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
