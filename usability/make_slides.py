"""Build the four final-project slides as a .pptx, for importing into the shared Google Slides deck.

    uv run --with python-pptx python usability/make_slides.py     # writes usability/TrailProof_final_slides_v3.pptx

In Google Slides: File > Import slides > Upload, pick this file, select all
four, import. Then put the YouTube video on slide 4 (Insert > Video), over
the dark box, and set Format options > Video playback > Play (automatically).
Review them without opening PowerPoint: export_slides.ps1 writes one PNG per slide.
"""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches, Pt

PAPER, INK, WHITE, MARK, QUIET, SOFT = (RGBColor.from_string(c) for c in ("E6EEEA", "14213D", "FFFFFF", "FFD23F", "5C6B7A", "9FB0C3"))
OUT = Path(__file__).resolve().parent / "TrailProof_final_slides_v3.pptx"


def text(slide, x, y, w, h, runs, size=17, color=INK, bold=False):
    """runs: a string, or a list of paragraphs; a paragraph is a string or a list of (text, bold) pieces."""
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    for m in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
        setattr(tf, m, 0)
    paras = runs if isinstance(runs, list) else [runs]
    for i, para in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        for piece in (para if isinstance(para, list) else [(para, bold)]):
            r = p.add_run()
            r.text, r.font.size, r.font.bold, r.font.color.rgb, r.font.name = piece[0], Pt(size), piece[1], color, "Segoe UI"
        p.space_after = Pt(size * 0.3)
    return tb


def card(slide, x, y, w, h, fill, title, body, title_color=QUIET, body_color=INK, size=15, big=None):
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.adjustments[0] = 0.06
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    shape.line.fill.background()
    shape.shadow.inherit = False
    text(slide, x + 0.22, y + 0.17, w - 0.44, 0.35, title, 13, title_color, True)
    top = y + 0.55
    if big:
        text(slide, x + 0.22, top, w - 0.44, 0.6, big, 32, body_color, True)
        top += 0.62
    text(slide, x + 0.22, top, w - 0.44, h - (top - y) - 0.15, body, size, body_color)


def base(prs, tag, n):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    bg = s.background.fill
    bg.solid()
    bg.fore_color.rgb = PAPER
    text(s, 0.7, 0.42, 8, 0.3, tag, 12, QUIET)
    text(s, 10.3, 0.42, 2.4, 0.3, f"{n} / 4", 12, QUIET).text_frame.paragraphs[0].alignment = 3
    return s


def step(slide, y, n, title, body):
    dot = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(0.7), Inches(y), Inches(0.62), Inches(0.62))
    dot.fill.solid()
    dot.fill.fore_color.rgb = INK
    dot.line.fill.background()
    tf = dot.text_frame
    tf.paragraphs[0].alignment = 2
    r = tf.paragraphs[0].add_run()
    r.text, r.font.size, r.font.bold, r.font.color.rgb, r.font.name = str(n), Pt(20), True, MARK, "Segoe UI"
    text(slide, 1.5, y - 0.03, 5.2, 0.4, title, 17, INK, True)
    text(slide, 1.5, y + 0.38, 5.2, 0.9, body, 13, RGBColor.from_string("2D3B58"))


def tile(slide, x, y, w, h, fill, where, what, status, title_color=INK, body_color=INK, status_color=QUIET):
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.adjustments[0] = 0.07
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    shape.line.fill.background()
    shape.shadow.inherit = False
    text(slide, x + 0.24, y + 0.2, w - 0.48, 0.45, where, 20, title_color, True)
    text(slide, x + 0.24, y + 0.72, w - 0.48, 0.9, what, 15, body_color)
    text(slide, x + 0.24, y + h - 0.42, w - 0.48, 0.3, status, 11, status_color, True)


def build() -> Path:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    brown = RGBColor.from_string("6B5A12")
    assets = Path(__file__).resolve().parent / "assets"

    # 1. the problem
    s = base(prs, "TrailProof", 1)
    text(s, 0.7, 0.85, 12, 1.5, "AI assistants will soon act for you everywhere. Nobody can tell yours from a bot.", 34, INK, True)
    card(s, 0.7, 2.6, 3.8, 3.15, WHITE, "Today: you, in a browser", "A small file called a cookie lets a website recognise you when you come back. That is why you stay signed in, known and trusted.", size=18)
    card(s, 4.75, 2.6, 3.8, 3.15, INK, "Tomorrow: your AI assistant", "It has no cookie. Everywhere it goes it is a stranger: blocked as a bot, asked for a deposit, sent to the back of the queue.", SOFT, PAPER, 18)
    card(s, 8.8, 2.6, 3.85, 3.15, MARK, "TrailProof", ["A cookie rebuilt for AI assistants: proof of who the assistant acts for and how it has behaved.", [("You hold it. It cannot be faked. It cannot be used to track you.", True)]], brown, size=18)
    text(s, 0.7, 6.25, 11.9, 0.8, "An AI agent is software that acts for a person: an assistant that books, orders and signs up for things on your behalf. Our users are these agents, on both sides of every interaction.", 12, QUIET)

    # 2. how it works
    s = base(prs, "TrailProof    how it works", 2)
    text(s, 0.7, 0.85, 12, 0.9, "Three steps. Each one can be checked by anyone.", 30, INK, True)
    step(s, 1.95, 1, "You decide what your assistant may do.", "\"Book tables and buy tickets, up to 100 euros a month.\" It can use this permission but can never change it.")
    step(s, 3.25, 2, "Your assistant builds a track record.", "A neutral record keeper seals every booking or order it completes, and signs a summary on request: \"6 bookings kept, no problems.\" Numbers only.")
    step(s, 4.65, 3, "Anyone it meets can check the proof.", "The proof is addressed to them alone. Copy it or change one letter and it stops working.")
    shot = assets / "trusted_guest.png"
    if shot.exists():
        s.shapes.add_picture(str(shot), Inches(7.0), Inches(1.95), width=Inches(5.65))
    text(s, 7.0, 5.2, 5.65, 0.6, "From our demo: a restaurant drops its deposit for an assistant whose record it can verify.", 11, QUIET)
    text(s, 0.7, 6.2, 11.9, 0.9, [[("What we built: ", True), ("three pieces of software that AI assistants plug into directly, using MCP, the open standard for giving tools to AI: a wallet for your assistant, a plug-in for the other side's assistant, and the neutral record keeper between them. No website, no human in the loop.", False)]], 12, QUIET)

    # 3. use cases
    s = base(prs, "TrailProof    where it helps", 3)
    text(s, 0.7, 0.85, 12, 0.9, "One proof, many doors.", 30, INK, True)
    live = "RUNNING IN OUR DEMO"
    nxt = "SAME PROOF, NOT BUILT YET"
    w_, h_, gx, gy = 3.85, 2.05, 0.2, 0.2
    tiles = [("Restaurants and hotels", "No deposit for guests who show up.", live, INK, PAPER, PAPER, MARK),
             ("Tickets", "Real fans get in before scalper bots.", live, INK, PAPER, PAPER, MARK),
             ("Shops", "Your loyalty status, through any assistant.", live, INK, PAPER, PAPER, MARK),
             ("Rentals and sharing", "No hold on your card for a renter with a clean record.", nxt, WHITE, INK, INK, QUIET),
             ("Customer support", "Skip the bot queue: a real customer is asking.", nxt, WHITE, INK, INK, QUIET),
             ("Sign-ups and applications", "A real person stands behind this request.", nxt, WHITE, INK, INK, QUIET)]
    for i, (where, what, status, fill, tc, bc, sc) in enumerate(tiles):
        tile(s, 0.7 + (i % 3) * (w_ + gx), 1.85 + (i // 3) * (h_ + gy), w_, h_, fill, where, what, status, tc, bc, sc)
    text(s, 0.7, 6.4, 11.9, 0.7, "The pattern is always the same: one AI agent asks, another has to decide whether to trust it. Today that decision is a guess. With a proof it is a check.", 13, QUIET)

    # 4. evidence, video, next
    s = base(prs, "TrailProof    does it work?", 4)
    box = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.7), Inches(1.0), Inches(7.5), Inches(5.75))
    box.adjustments[0] = 0.03
    box.fill.solid()
    box.fill.fore_color.rgb = INK
    box.line.fill.background()
    tb = text(s, 1.0, 3.1, 6.9, 1.8, ["Place the YouTube video here (Insert > Video)", "a trusted guest, a verified fan, a scalper bot turned away"], 15, SOFT)
    for para in tb.text_frame.paragraphs:
        para.alignment = 2
    card(s, 8.5, 1.0, 4.15, 2.0, WHITE, "What we tested", ["2 of 2 AI models we had never set up used it correctly from the manual alone.", "A forged proof was rejected every time."], size=14)
    card(s, 8.5, 3.13, 4.15, 1.8, INK, "What broke", ["A fresh AI exposed one real bug in how a permission was tracked. Fixed, with tests.", "Every test so far was run by us."], SOFT, PAPER, 14)
    card(s, 8.5, 5.06, 4.15, 1.69, MARK, "Next", ["One real partner instead of simulated ones", "Another team's assistant using it"], brown, size=14)
    text(s, 0.7, 6.95, 8, 0.3, "https://github.com/Andreyivvi/TrailProof", 12, QUIET)

    out = OUT
    try:
        prs.save(out)
    except PermissionError:   # the previous file is open in PowerPoint
        out = OUT.with_name(OUT.stem + "_new.pptx")
        prs.save(out)
    return out


if __name__ == "__main__":
    print(f"wrote {build()}")
