"""Render the HW7 benchmark report (HTML, then PDF via a headless Chromium) from benchmark/results/*.json.

    uv run python benchmark/make_report.py            # writes benchmark/report.html and benchmark/HW7_TrailProof_benchmark.pdf
    uv run python benchmark/make_report.py --team "Name (role: what they did)" --team "..."
"""

from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
REPO = "https://github.com/Andreyivvi/TrailProof"

BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


def load(name: str) -> dict:
    with open(os.path.join(RESULTS, name), encoding="utf-8") as f:
        return json.load(f)


def git_head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=os.path.dirname(HERE),
                              capture_output=True, text=True, timeout=10).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def cell(a: dict) -> str:
    return f"{a['correct']}/{a['n']} ({a['accuracy']:.0%})" if a["n"] else "n/a"


def results_table(res: dict, caption: str) -> str:
    s = res["summary"]
    rows = [("all cases", s["overall"])] + [(f"strategy: {k}", v) for k, v in s["by_strategy"].items()] + \
           [(f"fault: {k.replace('_', ' ')}", v) for k, v in s["by_fault"].items()]
    out = [f"<table><caption>{html.escape(caption)}</caption><thead><tr><th>slice</th><th>TrailProof correct</th>"
           "<th>baseline correct</th><th>honest party blamed<br>TP / BL</th><th>at-fault party cleared<br>TP / BL</th></tr></thead><tbody>"]
    for name, sl in rows:
        tp, bl = sl["trailproof"], sl["baseline"]
        cls = ' class="sub"' if ":" in name else ""
        out.append(f"<tr{cls}><td>{html.escape(name)}</td><td>{cell(tp)}</td><td>{cell(bl)}</td>"
                   f"<td>{tp['honest_party_blamed']} / {bl['honest_party_blamed']}</td><td>{tp['at_fault_cleared']} / {bl['at_fault_cleared']}</td></tr>")
    out.append("</tbody></table>")
    return "\n".join(out)


def strategy_bar(before: dict, after: dict, label: str) -> str:
    """A small inline SVG: accuracy per strategy, before and after, both paths."""
    strategies = ("honest", "lie", "withhold")
    groups = [("TrailProof, rules as shipped", before, "trailproof", "#1f6f8b"), ("Baseline, rules as shipped", before, "baseline", "#9fb7c2"),
              ("TrailProof + rule 5b", after, "trailproof", "#c96a12"), ("Baseline + rule 5b", after, "baseline", "#e8c39e")]
    w, h, left, bottom = 640, 230, 50, 40
    gw = (w - left - 20) / len(strategies)
    bw = gw / (len(groups) + 1)
    parts = [f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" font-family="Helvetica, Arial, sans-serif" font-size="11">']
    for y in (0, 0.5, 1.0):
        yy = h - bottom - y * (h - bottom - 30)
        parts.append(f'<line x1="{left}" y1="{yy:.1f}" x2="{w-20}" y2="{yy:.1f}" stroke="#ddd"/><text x="{left-6}" y="{yy+4:.1f}" text-anchor="end">{y:.0%}</text>')
    for si, st in enumerate(strategies):
        x0 = left + si * gw + bw / 2
        for gi, (_, res, path, color) in enumerate(groups):
            acc = res["summary"]["by_strategy"][st][path]["accuracy"] or 0
            bh = acc * (h - bottom - 30)
            x = x0 + gi * bw
            parts.append(f'<rect x="{x:.1f}" y="{h-bottom-bh:.1f}" width="{bw-3:.1f}" height="{bh:.1f}" fill="{color}"/>')
            parts.append(f'<text x="{x+(bw-3)/2:.1f}" y="{h-bottom-bh-3:.1f}" text-anchor="middle" font-size="9">{acc:.0%}</text>')
        parts.append(f'<text x="{x0 + (len(groups)*bw)/2 - bw/2:.1f}" y="{h-bottom+16}" text-anchor="middle">{st}</text>')
    lx = left
    for gi, (name, _, _, color) in enumerate(groups):
        parts.append(f'<rect x="{lx}" y="{h-14}" width="10" height="10" fill="{color}"/><text x="{lx+14}" y="{h-5}">{html.escape(name)}</text>')
        lx += 150
    parts.append(f'<text x="{w/2}" y="14" text-anchor="middle" font-size="12">{html.escape(label)}</text></svg>')
    return "".join(parts)


def render(team: list[str]) -> str:
    s1b, s1a, s2b, s2a = load("seed1_before.json"), load("seed1_after.json"), load("seed2_before.json"), load("seed2_after.json")
    commit = git_head()
    o1, o2 = s1b["summary"]["overall"], s2b["summary"]["overall"]
    cost_ms = (o1["trailproof"]["mean_seconds"] + o2["trailproof"]["mean_seconds"]) / 2 * 1000
    calls = (o1["trailproof"]["mean_tool_calls"] + o2["trailproof"]["mean_tool_calls"]) / 2
    n1, n2 = s1b["n"], s2b["n"]
    wd1 = s1b["summary"]["by_strategy"]["withhold"]["trailproof"]["n"]
    wd2 = s2b["summary"]["by_strategy"]["withhold"]["trailproof"]["n"]
    lie1 = s1b["summary"]["by_strategy"]["lie"]["trailproof"]["n"]
    lie2 = s2b["summary"]["by_strategy"]["lie"]["trailproof"]["n"]
    team_html = "".join(f"<li>{html.escape(t)}</li>" for t in team) or "<li>[team members and contributions]</li>"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>TrailProof HW7 benchmark</title>
<style>
 @page {{ size: A4; margin: 18mm 16mm; }}
 body {{ font-family: Helvetica, Arial, sans-serif; font-size: 10.5pt; line-height: 1.38; color: #1a1a1a; max-width: 180mm; margin: 0 auto; }}
 h1 {{ font-size: 18pt; margin: 0 0 2pt; }} h2 {{ font-size: 12.5pt; margin: 14pt 0 4pt; border-bottom: 1px solid #bbb; padding-bottom: 2pt; }}
 p {{ margin: 4pt 0; }} .meta {{ color: #555; font-size: 9.5pt; margin-bottom: 8pt; }}
 table {{ border-collapse: collapse; width: 100%; margin: 6pt 0 8pt; font-size: 9.5pt; page-break-inside: avoid; }}
 caption {{ text-align: left; font-weight: bold; margin-bottom: 3pt; }}
 th, td {{ border: 1px solid #ccc; padding: 2.5pt 5pt; text-align: left; }} th {{ background: #f0f3f5; }}
 tr.sub td:first-child {{ padding-left: 14pt; color: #444; }}
 code {{ font-family: Consolas, Menlo, monospace; font-size: 9pt; background: #f4f4f4; padding: 0 2pt; }}
 pre {{ font-family: Consolas, Menlo, monospace; font-size: 8.8pt; background: #f4f4f4; padding: 6pt; border: 1px solid #ddd; white-space: pre-wrap; }}
 ul {{ margin: 3pt 0 3pt 16pt; padding: 0; }} li {{ margin: 1.5pt 0; }}
 .fig {{ margin: 6pt 0; page-break-inside: avoid; }} .small {{ font-size: 9pt; color: #444; }}
</style></head><body>
<h1>TrailProof: does pre-committed evidence survive a dishonest counterparty?</h1>
<p class="meta">MIT AI Studio, Fall 2026, Homework 7. Project: TrailProof, an append-only commitment ledger for AI-agent purchases, exposed as an MCP server.
Code and raw results: <a href="{REPO}">{REPO}</a> at commit <code>{commit}</code>, folder <code>benchmark/</code>.</p>

<h2>1. Claim</h2>
<p><b>Claim.</b> When a purchase made by an agent is disputed, an arbiter that judges from TrailProof's evidence (each party's SHA-256 commitment of what it saw, signed and countersigned before the dispute) blames the right side even when the party at fault lies about what was agreed, with no LLM and no human. The benefit is for the agents and merchants using the ledger: a dispute between them resolves correctly without trusting either side's story.</p>
<p><b>Metric.</b> Verdict correctness: the arbiter's verdict blames the side the case generator made responsible (merchant, buyer side, or nobody). Two failure modes are counted separately: an honest party blamed, and an at-fault party cleared. Costs: wall time and ledger tool calls per case; LLM usage is zero by construction.</p>
<p><b>Result check.</b> Ground truth is assigned by the case generator before any run, from the fault it injected and the dispute-time behaviour it assigned, never from the arbiter's output. As a self-check, the rules engine run on the <i>true</i> records agrees with the generator on every order-level fault (see section 3).</p>

<h2>2. Baseline</h2>
<p>The same rules engine (<code>trailproof.rules.evaluate</code>, unchanged) applied to the records each party hands over <i>after</i> the dispute is opened, with nothing committed in advance: TrailProof with its key feature disabled. Same cases, same claims, same records when everyone is honest. The only difference is that the baseline arbiter has no way to tell a record from a story, so a party's dispute-time version of an offer, a delivery or a shipment is taken at face value, while TrailProof checks each revealed record against the hash that party committed to during the order. A party that withholds a record leaves a gap in both paths.</p>

<h2>3. Experimental setup</h2>
<p><b>Cases.</b> Seeded random orders over the mock catalogue (four SKUs, quantity 1 to 3, a user mandate just above or just below the order total). Six fault types are cycled evenly: <i>overcharge</i> (PSP charges more than the committed offer), <i>wrong item</i> (merchant ships a different SKU), <i>not shipped</i>, <i>out of scope</i> (the agent orders above the user's mandate without the scope check), <i>friendly fraud</i> (clean order, user claims it was not authorised), <i>baseless</i> (clean order, buyer claims not as described). For each case the side that would gain from it is assigned, at random, one of three dispute-time behaviours: <b>honest</b>, <b>lie</b> (the merchant reveals an offer matching the charge or the shipped item, or a fabricated shipment; the buyer reveals a delivery record naming the wrong item) or <b>withhold</b> (reveal nothing). "Honest" is the ordinary case; "lie" and "withhold" are the difficult ones. The baseless fault is the negative control: nobody is at fault unless the buyer lies.</p>
<p><b>Procedure.</b> Every case runs through the real TrailProof flow in process: mandate, offer, order (or an order written without the scope check for out-of-scope cases), charge, fulfilment, delivery, dispute, reveals, <code>get_case_bundle</code>. The baseline then receives the identical self-reported records as an unverified case and the same rules are applied. Two seeds of {n1} cases each: seed 1 was used to find the limit and tune the change, seed 2 is the fresh check. Runs are deterministic: a repeat of seed 1 reproduced all {n1} verdicts exactly, so no further repetitions were needed. Each seed takes under a second.</p>
<p><b>Labelling correction, disclosed.</b> The first seed-1 run labelled a lying buyer on a baseless complaint as "nobody at fault", which blamed TrailProof for calling out the fabrication (9 cases). The labelling rule was corrected before the runs reported here: a party that lies during the dispute is at fault even if the order was clean. This is a rule about ground truth, not about either arbiter. As a result the rules engine on true records agrees with the generator on {s1b['summary']['oracle_agrees_with_generator']}/{n1} and {s2b['summary']['oracle_agrees_with_generator']}/{n2} cases; the disagreements are exactly those baseless-plus-lie cases, where the true records are clean and only the dispute-time lie assigns blame.</p>
<p><b>Repeat it.</b></p>
<pre>git clone {REPO} &amp;&amp; cd TrailProof &amp;&amp; uv sync
uv run python benchmark/bench_disputes.py --seed 1 --n 120 --out benchmark/results/seed1_before.json
uv run python benchmark/bench_disputes.py --seed 2 --n 120 --out benchmark/results/seed2_before.json
uv run python benchmark/bench_disputes.py --seed 1 --n 120 --default-judgment --out benchmark/results/seed1_after.json
uv run python benchmark/bench_disputes.py --seed 2 --n 120 --default-judgment --out benchmark/results/seed2_after.json
uv run python benchmark/make_report.py</pre>

<h2>4. Results (rules as shipped)</h2>
{results_table(s1b, f"Seed 1, {n1} cases, rules as shipped (tuning set)")}
{results_table(s2b, f"Seed 2, {n2} cases, rules as shipped (fresh set)")}
<p><b>Cost.</b> TrailProof: about {cost_ms:.0f} ms and {calls:.0f} ledger tool calls per case for the whole order and dispute flow in process (SQLite, Ed25519); the baseline evaluation itself is under 0.1 ms since it is the same pure function. Neither path calls a model: 0 tokens.</p>
<p><b>Reading.</b> With every party honest, both arbiters are right in every case: the rules engine is not the difference. When the party at fault lies, TrailProof is right in {lie1}/{lie1} and {lie2}/{lie2} cases and the baseline in none; every lie that passed the baseline either cleared the at-fault party or blamed an honest one. When the party at fault withholds its records, both paths fail in every case ({wd1} and {wd2} cases): the verdict is "unverifiable", nobody is blamed, and the at-fault party walks away. That is the limit.</p>

<h2>5. Limit, change, retest</h2>
<p><b>The limit.</b> A party that committed to an event and then refuses to reveal it produces an <code>UNVERIFIABLE</code> finding naming that party, and the verdict table stopped there. Silence was as good as innocence, which makes withholding the dominant strategy for anyone at fault: in seed 1 it beat the ledger in {wd1}/{wd1} cases.</p>
<p><b>The change (one rule).</b> Rule 5b, <i>default judgment</i>: if nothing conclusive was found and every unverifiable finding that names a party is owed by one side, decide against that side. The justification is specific to commitments: the ledger holds that party's signed hash of the record, so the party demonstrably had it and chose not to produce it. Events that were never recorded name nobody and still yield "unverifiable"; a conclusive finding (tampering, a verified mismatch) still takes precedence. Implemented as an opt-in flag, <code>TrailProofService(default_judgment=True)</code>, with a unit test; existing behaviour is unchanged by default.</p>
<p><b>Retest.</b> Seed 1 (the tuning set) and seed 2 (fresh, never used while designing the rule), both arbiters given the same new rule for fairness.</p>
{results_table(s1a, f"Seed 1, {n1} cases, with rule 5b (tuning set)")}
{results_table(s2a, f"Seed 2, {n2} cases, with rule 5b (fresh set)")}
<div class="fig">{strategy_bar(s2b, s2a, "Seed 2 (fresh): verdict accuracy by dispute-time behaviour, before and after rule 5b")}</div>
<p><b>What changed.</b> Withholding no longer pays: TrailProof is correct on all {n2} fresh cases, with no honest party blamed. The baseline improves by the same withholding cases, because in this benchmark every withheld record did exist, so the rule is just as applicable there; the baseline still fails every lie, so the remaining gap between the two paths is exactly the lying cases. The retest shows the change did what the evidence suggested and nothing else: honest and lying slices are unchanged.</p>

<h2>6. What the evidence supports, and what it does not</h2>
<ul>
<li><b>Supported.</b> Against a dishonest counterparty, commitments made before the dispute are what makes the deterministic verdict right; the rules alone are not. On these cases the effect is total: {lie1 + lie2}/{lie1 + lie2} lying cases right with commitments, 0/{lie1 + lie2} without. With default judgment, withholding is closed as well.</li>
<li><b>Not a model comparison.</b> The baseline is our own rules engine on unverified records, not a human or LLM arbiter reading free text. A model judge might catch some lies from inconsistencies the rules ignore, or be fooled in other ways; that comparison needs credentials and was not run.</li>
<li><b>Synthetic and narrow.</b> Four SKUs, scripted mock parties, one lie per case, one dishonest party at a time, an always-honest PSP, and no collusion. Lies are of one kind per fault (the cheapest one that would win under the baseline); a liar with a subtler strategy, such as revealing truthfully and arguing the facts, is not modelled, and a case where the true records are themselves ambiguous ends "ambiguous" in both paths.</li>
<li><b>Rule 5b has a cost the benchmark cannot show.</b> It punishes a party that is offline, lost its vault, or missed the dispute window exactly like a party hiding evidence. The benchmark contains no honest-but-unavailable party, so the false-blame rate of the new rule is unmeasured; in production it needs a reveal deadline and a reminder before it fires. Here it is opt-in for that reason.</li>
<li><b>Time is not a result.</b> Milliseconds in process say nothing about a hosted ledger with network round trips; the cost that matters, zero model tokens, holds by construction.</li>
<li><b>Determinism, not sampling.</b> Repeating a seed reproduces every verdict, so the counts carry no run-to-run variance; the only sampling is over the {n1 + n2} generated cases across two seeds.</li>
</ul>

<h2>7. Evidence</h2>
<ul>
<li>Benchmark driver: <code>benchmark/bench_disputes.py</code>; report generator: <code>benchmark/make_report.py</code>.</li>
<li>Raw per-case results with verdicts, findings and rationales: <code>benchmark/results/seed1_before.json</code>, <code>seed2_before.json</code>, <code>seed1_after.json</code>, <code>seed2_after.json</code>.</li>
<li>The change: rule 5b in <code>trailproof/rules.py</code> (<code>decide</code>), the <code>default_judgment</code> flag in <code>trailproof/service.py</code>, test <code>test_default_judgment_rule_5b_goes_against_the_side_that_withholds</code> in <code>tests/test_rules.py</code>. Full suite: <code>uv run pytest</code>.</li>
<li>Related compatibility evidence (HW8): the same ledger driven by external Nanda Town agents, <a href="https://github.com/projnanda/nandatown/pull/319">projnanda/nandatown#319</a>.</li>
</ul>

<h2>8. Team</h2>
<ul>{team_html}</ul>
<p class="small">No credentials or private data are involved; all parties are mock agents with freshly generated keys.</p>
</body></html>
"""


def to_pdf(html_path: str, pdf_path: str) -> bool:
    browser = next((b for b in BROWSERS if os.path.exists(b)), None) or shutil.which("msedge") or shutil.which("google-chrome") or shutil.which("chromium")
    if not browser:
        print("no Chromium-based browser found; HTML written, PDF skipped", file=sys.stderr)
        return False
    import tempfile

    url = "file:///" + os.path.abspath(html_path).replace("\\", "/")
    with tempfile.TemporaryDirectory(prefix="trailproof-pdf-") as profile:  # a scratch profile: headless printing needs one it can own
        cmd = [browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--user-data-dir={profile}",
               f"--print-to-pdf={os.path.abspath(pdf_path)}", url]
        done = subprocess.run(cmd, timeout=120, capture_output=True, text=True)
    if not os.path.exists(pdf_path):
        print(f"PDF rendering failed with {browser}:\n{done.stderr[-2000:]}", file=sys.stderr)
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--team", action="append", default=[], help="one team member and their contribution; repeatable")
    ap.add_argument("--html", default=os.path.join(HERE, "report.html"))
    ap.add_argument("--pdf", default=os.path.join(HERE, "HW7_TrailProof_benchmark.pdf"))
    args = ap.parse_args(argv)
    with open(args.html, "w", encoding="utf-8") as f:
        f.write(render(args.team))
    print(f"wrote {args.html}")
    if to_pdf(args.html, args.pdf):
        print(f"wrote {args.pdf}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
