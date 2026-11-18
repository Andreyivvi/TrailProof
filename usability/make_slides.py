"""Build the four final-project slides as a .pptx, for importing into the shared Google Slides deck.

    uv run --with python-pptx python usability/make_slides.py     # writes usability/TrailProof_final_slides.pptx

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
OUT = Path(__file__).resolve().parent / "TrailProof_final_slides.pptx"


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


def build() -> Path:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)

    s = base(prs, "TrailProof    MIT AI Studio, Fall 2026", 1)
    text(s, 0.7, 0.85, 12, 1.0, "The cookie, rebuilt for AI agents.", 40, INK, True)
    card(s, 0.7, 2.0, 3.95, 3.1, INK, "The problem", "On the web, a cookie lets a shop recognise a returning customer. An AI agent has none. To a merchant every agent is a stranger: a loyal customer, or one of a thousand bots?", SOFT, PAPER, 16)
    card(s, 4.95, 2.0, 3.7, 3.1, WHITE, "Why it is agentic", "The users are agents. A buyer agent carries proof of its history and its user's authority; a merchant agent checks it. No human is in the loop, and there is no web page.", size=16)
    card(s, 8.95, 2.0, 3.7, 3.1, MARK, "Who uses it", ["Buyer agents: prove history, buy inside a mandate", "Merchant agents: recognise real customers", "Payment agents: commit charges and refunds"], RGBColor.from_string("6B5A12"), size=16)
    text(s, 0.7, 6.55, 12, 0.4, "Unlike a cookie: the customer holds it, it cannot be faked, and it cannot be used to track them.", 13, QUIET)

    s = base(prs, "TrailProof    what we built", 2)
    text(s, 0.7, 0.85, 12, 1.3, "Three MCP servers around a ledger that keeps fingerprints, never documents.", 30, INK, True)
    card(s, 0.7, 2.45, 3.0, 2.3, INK, "Wallet (buyer's agent)", "9 tools. Holds keys, mandates, plaintext. Cannot grant itself a mandate.", SOFT, PAPER)
    text(s, 3.8, 3.0, 1.35, 1.2, ["signed hashes  >", "<  proofs, receipts"], 11, QUIET)
    card(s, 5.15, 2.25, 3.3, 2.7, WHITE, "Ledger (neutral)", "Per-order hash chain: mandate, offer, order, charge, delivery. Checks every order against the mandate. Deterministic dispute rules, no LLM.")
    text(s, 8.55, 3.0, 1.35, 1.2, ["<  signed hashes", "verdicts  >"], 11, QUIET)
    card(s, 9.75, 2.45, 2.9, 2.3, MARK, "Shop (merchant's agent)", "4 tools. Commits offers, recognises customers from their proof, refunds by policy.", RGBColor.from_string("6B5A12"))
    for i, (head, body) in enumerate([("Proof of history. ", "Ledger-signed counts, addressed to one shop. No products, no shop names."),
                                       ("Mandate. ", "Signed by the person on the command line. No mandate, no purchase."),
                                       ("Disputes. ", "Both sides reveal; the ledger re-hashes against what was committed earlier.")]):
        text(s, 0.7 + i * 4.05, 5.35, 3.8, 1.4, [[(head, True), (body, False)]], 14)

    s = base(prs, "TrailProof    evidence from HW6 to HW9", 3)
    text(s, 0.7, 0.85, 12, 0.9, "What we measured, and what broke.", 30, INK, True)
    cards = [("HW6   working core", "6", "scenarios end to end over MCP, with a live model as buyer; evidence re-verified offline.", WHITE, QUIET, INK),
             ("HW7   benchmark", "0 vs 46", "honest parties blamed in 240 disputes with a dishonest counterparty: TrailProof vs a logs-only baseline.", WHITE, QUIET, INK),
             ("HW8   Nanda Town", "8 / 119", "cases and checks in the course sandbox, including a seller caught doctoring evidence.", WHITE, QUIET, INK),
             ("HW9   cold start", "7 / 7", "checks by two fresh models given only the docs: right proof, right price, truthful report.", INK, SOFT, PAPER)]
    for i, (t, big, body, fill, tc, bc) in enumerate(cards):
        card(s, 0.7 + i * 3.02, 1.9, 2.85, 2.65, fill, t, body, tc, bc, 13, big)
    text(s, 0.7, 4.9, 5.8, 1.9, [[("Worked. ", True), ("Agents chose the right kind of proof unprompted, refused an out-of-mandate request and told the user how to authorise it, and disputed a wrong parcel through to a refund.", False)]], 14)
    text(s, 6.85, 4.9, 5.8, 1.9, [[("Failed, then fixed. ", True), ("A refund did not return the budget, so the agent could not reorder. Found by the cold-start run, fixed, six regression tests. After the fix the agent still asked before reordering.", False)]], 14)

    s = base(prs, "TrailProof    demo, limits, next", 4)
    box = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.7), Inches(1.0), Inches(7.5), Inches(5.75))
    box.adjustments[0] = 0.03
    box.fill.solid()
    box.fill.fore_color.rgb = INK
    box.line.fill.background()
    t = text(s, 1.0, 3.2, 6.9, 1.6, ["Place the YouTube video here (Insert > Video)", "six visitors at a shop door: recognised, welcomed, public price, forged, refused, refunded"], 15, SOFT)
    for p in t.text_frame.paragraphs:
        p.alignment = 2
    card(s, 8.5, 1.0, 4.15, 2.75, WHITE, "Limits today", ["Shop and payment provider are mocks", "User key in a file, not a passkey", "Clients tested: two models, one vendor, run by us"], size=14)
    card(s, 8.5, 4.0, 4.15, 2.75, MARK, "Next", ["Another team's agent on the interface", "Hosted ledger, real merchant plugin", "Loyalty points that vest on undisputed orders"], RGBColor.from_string("6B5A12"), size=14)
    text(s, 0.7, 6.95, 8, 0.3, "https://github.com/Andreyivvi/TrailProof", 12, QUIET)

    prs.save(OUT)
    return OUT


if __name__ == "__main__":
    print(f"wrote {build()}")
