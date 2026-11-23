"""Build the four final-project slides.

    uv run --with python-pptx python usability/make_slides.py

The slides are designed in ``usability/slides.html`` (open it in a browser to
see or edit them). This script photographs each one at 1920 x 1080 with
headless Chrome and places it full-bleed on a 16:9 slide, so the deck looks
identical in PowerPoint and in Google Slides, whatever fonts are installed.
Speaker notes carry the talk track, and each picture carries its text as a
description for screen readers.

Writes ``usability/slides/slide1..4.png`` and ``usability/TrailProof_slides.pptx``.

In Google Slides: File > Import slides > Upload, select all four. On slide 4,
Insert > Video, paste the YouTube link, drag the video over the dark box, then
Format options > Video playback > Play (automatically).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from pptx import Presentation
from pptx.util import Inches

HERE = Path(__file__).resolve().parent
PAGE = HERE / "slides.html"
OUT = HERE / "TrailProof_slides.pptx"
CHROME = [r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
          r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
          "/usr/bin/google-chrome", "/usr/bin/chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]

# What is on each slide, in words (picture description), and what to say (speaker notes). About 2.5 minutes in all.
SLIDES = [
    ("Slide 1. People are starting to send an AI assistant instead of going themselves. Today: 53 percent of web traffic is "
     "already software, not people (Imperva Bad Bot Report 2026). By 2028: 90 percent of business buying runs through AI "
     "agents (Gartner forecast, October 2025). By 2030: 3 to 5 trillion dollars of consumer shopping a year is done by AI agents "
     "(McKinsey forecast, October 2025). The catch: the web knows people; it does not know their assistants. 40 percent of web traffic "
     "today is hostile bots, and from the outside an assistant looks like one.",
     "People are starting to send an AI assistant instead of going themselves. Already more than half of all web traffic is "
     "software, not people. Gartner expects nine in ten business purchases to run through AI agents by 2028, and McKinsey puts "
     "consumer shopping done by agents at three to five trillion dollars a year by 2030. Those are forecasts, but the direction is "
     "clear. Here is the catch: the web knows people, but it does not know their assistants. Four in every ten web requests come "
     "from hostile bots, and from the outside your assistant looks like one of them. Why is that?"),
    ("Slide 2. On the web, you are known. Your AI assistant is a stranger. In a browser you are "
     "known, because a cookie vouches for you. Your AI assistant has no cookie, no history and no proof, so it is blocked as a bot, "
     "starts from zero every time, and gets no personalised offers. TrailProof is the cookie, rebuilt for AI assistants: you hold it, it cannot "
     "be faked, it cannot track you.",
     "Because you carry something your assistant does not. On the web, a small file called a cookie lets a site recognise you. "
     "That is why you stay signed in and get your usual terms. Your assistant has no cookie. To a restaurant, a box office or a "
     "shop, your assistant looks exactly like a bot: no history, no personalised offers. TrailProof gives it something to show: a proof that you hold, that cannot be "
     "faked, and that cannot be used to track you."),
    ("Slide 3. Whoever meets your assistant asks three questions, and we answer each with proof. One: does it have a good record? "
     "A neutral record keeper counts what it has done, numbers only. Two: is the record really its own? Each proof is signed and "
     "tied to one assistant, so a copy stops working. Three: did its owner allow this? You set limits once; inside them the "
     "assistant acts alone, outside them it is stopped. "
     "Architecture: your assistant carries a wallet, their assistant runs a plug-in, and a neutral record keeper sits between them. "
     "AI agents plug in directly through MCP; there is no website and no human in the loop.",
     "Anyone meeting an AI assistant asks three things. Does it have a good record? Is that record really its own? And did its "
     "owner allow this? We answer each with proof instead of a promise. A neutral record keeper counts what the assistant has "
     "done: numbers only, never what you bought or where. Each proof is tied to one assistant, so stealing a good record does not "
     "work. And you set limits once, like restaurants up to 100 euros a month; inside them your assistant acts alone, outside "
     "them it is stopped. It is built for agents: three pieces of software that AI assistants plug into directly. No website, "
     "no human in the loop."),
    ("Slide 4. Thirty seconds, three answers: the demo video, a replay of the running system. Beside it, one proof, many doors. "
     "Running today: restaurants and hotels, ticket sales, contracts and sign-ups, shops. Same proof, next: rentals and customer "
     "support. Along the bottom, where it stands. Tested: two AI models we never set up used it correctly from the manual alone. "
     "Not yet: the restaurant, box office and gym are simulated, and every test was run by us. Next: one real partner, and another "
     "team's assistant using it.",
     "Here it is running. A restaurant that has never met the assistant gives it the regulars' table, because its record is proven. A scalper bot with a copied proof is "
     "turned away from a presale. And when a gym's agent pushes a contract, the assistant cannot sign, because its user only allowed "
     "restaurant bookings. Every check you see was really made. The same proof opens the other doors on the right. Where it stands: "
     "we gave two AI models nothing but the manual and both used it correctly on the first try; that test also exposed a real bug, "
     "which we fixed. What is not real yet: the restaurant, box office and gym are simulated, and we ran every test ourselves. Next "
     "is one real partner, and another team's assistant using it."),
]


def photograph() -> list[Path]:
    chrome = next((c for c in CHROME if Path(c).exists()), None)
    if not chrome:
        raise SystemExit("needs Chrome or Edge to photograph the slides")
    out = HERE / "slides"
    out.mkdir(exist_ok=True)
    shots = []
    for n in range(1, len(SLIDES) + 1):
        png = out / f"slide{n}.png"
        subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--window-size=1920,1080",
                        f"--user-data-dir={out / '_profile'}", "--virtual-time-budget=4000", f"--screenshot={png}",
                        f"{PAGE.as_uri()}?s={n}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120, check=True)
        shots.append(png)
    shutil.rmtree(out / "_profile", ignore_errors=True)
    return shots


def build(shots: list[Path]) -> Path:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    for png, (described, notes) in zip(shots, SLIDES):
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        pic = slide.shapes.add_picture(str(png), 0, 0, width=prs.slide_width, height=prs.slide_height)
        pic._element.xpath("./p:nvPicPr/p:cNvPr")[0].set("descr", described)
        slide.notes_slide.notes_text_frame.text = notes
    out = OUT
    try:
        prs.save(out)
    except PermissionError:   # the file is open in PowerPoint
        out = OUT.with_name(OUT.stem + "_new.pptx")
        prs.save(out)
    return out


if __name__ == "__main__":
    pictures = photograph()
    print(f"wrote {build(pictures)}  ({len(pictures)} slides from {PAGE.name})")
