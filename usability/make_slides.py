"""Build the four final-project slides as a .pptx, for importing into the shared Google Slides deck.

    uv run --with python-pptx python usability/make_slides.py     # writes usability/TrailProof_final_slides_v2.pptx

In Google Slides: File > Import slides > Upload, pick this file, select all
four, import. Then put the YouTube video on slide 4 (Insert > Video), over
the dark box, and set Format options > Video playback > Play (automatically).
The same four slides are the last pages of the HW9 PDF (make_report.py).
"""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches, Pt

PAPER, INK, WHITE, MARK, QUIET, SOFT = (RGBColor.from_string(c) for c in ("E6EEEA", "14213D", "FFFFFF", "FFD23F", "5C6B7A", "9FB0C3"))
OUT = Path(__file__).resolve().parent / "TrailProof_final_slides_v2.pptx"


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


def build() -> Path:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    brown = RGBColor.from_string("6B5A12")

    s = base(prs, "TrailProof", 1)
    text(s, 0.7, 0.85, 12, 1.5, "Soon an AI assistant will do your shopping. Shops cannot tell it from a bot.", 35, INK, True)
    card(s, 0.7, 2.6, 3.8, 3.15, WHITE, "Today: you, in a browser", "A small file called a cookie lets a shop recognise you when you return. That is how you get your basket, your history and your loyalty price.", size=15)
    card(s, 4.75, 2.6, 3.8, 3.15, INK, "Tomorrow: your AI assistant", "It has no cookie. To the shop it is a stranger, the same as a thousand price-scraping bots. No loyalty price. And if the order goes wrong, it is the shop's records against yours.", SOFT, PAPER, 15)
    card(s, 8.8, 2.6, 3.85, 3.15, MARK, "TrailProof", ["A cookie rebuilt for AI assistants: proof of who the assistant shops for and how it has behaved.", [("You hold it. It cannot be faked. It cannot be used to track you.", True)]], brown, size=15)
    text(s, 0.7, 6.25, 11.9, 0.8, "An AI agent is software that acts for a person: here, an assistant that finds, orders and pays for things on your behalf. Our users are these agents, on both sides of a sale.", 12, QUIET)

    s = base(prs, "TrailProof    how it works", 2)
    text(s, 0.7, 0.85, 12, 0.9, "Three steps. Each one can be checked by anyone.", 30, INK, True)
    step(s, 1.95, 1, "You set a spending permission.", "\"Pet food, up to 80 euros, this month.\" Your assistant can use it but can never change it.")
    step(s, 3.25, 2, "Your assistant proves its track record.", "\"2 completed orders here, no disputes lost.\" Signed by a neutral record keeper. Numbers only: never what you bought or where.")
    step(s, 4.65, 3, "The shop recognises you, and every step is sealed.", "You get your loyalty price. Each side locks a fingerprint of what it promised, so a wrong delivery is settled from proof and refunded automatically.")
    shot = Path(__file__).resolve().parent / "assets" / "recognised.png"
    if shot.exists():
        s.shapes.add_picture(str(shot), Inches(7.0), Inches(1.95), width=Inches(5.65))
    text(s, 7.0, 5.2, 5.65, 0.6, "From our demo. Left: the assistant at the shop's door. Middle: the sealed record. Right: the price it earns.", 11, QUIET)
    text(s, 0.7, 6.2, 11.9, 0.9, [[("What we built: ", True), ("three pieces of software that AI assistants plug into directly, using the open MCP standard for giving tools to AI: a wallet for the customer's assistant, a plug-in for the shop's assistant, and a neutral record keeper between them. No website, no human in the loop.", False)]], 12, QUIET)

    s = base(prs, "TrailProof    does it work?", 3)
    text(s, 0.7, 0.85, 12, 0.9, "What we tested, and what broke.", 30, INK, True)
    cards = [("Can a new AI use it?", "2 of 2", "AI models we had never set up read only our manual and bought correctly on the first try, at the right price.", INK, SOFT, PAPER),
             ("Does it judge fairly?", "0 vs 46", "honest parties wrongly blamed in 240 simulated disputes where one side lied. Ours: 0. Comparing each side's own records: 46.", WHITE, QUIET, INK),
             ("Can it be cheated?", "Caught", "A seller that shipped the wrong product and then edited its records was exposed automatically, in a shared test environment run by the course.", WHITE, QUIET, INK),
             ("What failed", "1 real bug", "A fresh AI showed us that a refund did not give the spending allowance back. Found by testing, then fixed.", MARK, brown, INK)]
    for i, (title, big, body, fill, tc, bc) in enumerate(cards):
        card(s, 0.7 + i * 3.02, 1.9, 2.85, 2.85, fill, title, body, tc, bc, 12.5, big)
    text(s, 0.7, 5.05, 5.8, 1.9, [[("What worked. ", True), ("Assistants picked the right proof on their own, refused to buy outside the permission and told the user how to allow it, and took a wrong delivery all the way to a refund.", False)]], 14)
    text(s, 6.85, 5.05, 5.8, 1.9, [[("What still does not. ", True), ("After the fix, the assistant got the refund but asked before buying again, so the customer still had no dog food. And every test so far was run by us, with AI models from one company.", False)]], 14)

    s = base(prs, "TrailProof    see it, and what comes next", 4)
    box = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.7), Inches(1.0), Inches(7.5), Inches(5.75))
    box.adjustments[0] = 0.03
    box.fill.solid()
    box.fill.fore_color.rgb = INK
    box.line.fill.background()
    tb = text(s, 1.0, 3.1, 6.9, 1.8, ["Place the YouTube video here (Insert > Video)", "a returning customer is recognised, an impostor is caught, a wrong delivery is refunded"], 15, SOFT)
    for para in tb.text_frame.paragraphs:
        para.alignment = 2
    card(s, 8.5, 1.0, 4.15, 1.85, WHITE, "Real today", ["Working, open-source software", "89 automated tests", "A live AI assistant buying through it"], size=12.5)
    card(s, 8.5, 2.98, 4.15, 1.65, INK, "Not yet", ["Shops and payments are simulated", "Your key sits in a file, not a passkey"], SOFT, PAPER, 12.5)
    card(s, 8.5, 4.76, 4.15, 1.99, MARK, "Next", ["A plug-in for a real shop", "Another team's assistant using it", "Loyalty points on top"], brown, size=12.5)
    text(s, 0.7, 6.95, 8, 0.3, "https://github.com/Andreyivvi/TrailProof", 12, QUIET)

    out = OUT
    try:
        prs.save(out)
    except PermissionError:   # the previous file is open in PowerPoint
        out = OUT.with_name("TrailProof_final_slides_v3.pptx")
        prs.save(out)
    return out


if __name__ == "__main__":
    print(f"wrote {build()}")
