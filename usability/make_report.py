"""Build the HW9 team PDF: usability evidence, then the final project slides.

    uv run python usability/make_report.py --team "Name: contribution" --deck-url https://docs.google.com/...

Reads the recorded runs under usability/runs/ and writes usability/report.html
and usability/HW9_TrailProof_usability.pdf (headless Chrome or Edge).
"""

from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"
REPO = "https://github.com/Andreyivvi/TrailProof"
BRANCH = "customer-wallet"
BROWSERS = [r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe", "/usr/bin/google-chrome", "/usr/bin/chromium",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
E = html.escape


def result(label: str) -> dict:
    return json.loads((RUNS / label / "result.json").read_text(encoding="utf-8"))


def transcript(label: str) -> list[dict]:
    return [json.loads(line) for line in (RUNS / label / "transcript.jsonl").read_text(encoding="utf-8").splitlines()]


def brief(value, n: int) -> str:
    s = json.dumps(value, ensure_ascii=False)
    return s if len(s) <= n else s[: n - 1] + "…"


KEEP = {"wallet_status": ["name", "registered", "orders_placed"], "list_mandates": None, "shop_info": ["shop_party_id", "loyalty"],
        "prove_history": ["merchant", "audience", "stats"], "start_order": ["order_id", "allowed"],
        "get_offer": ["recognised", "why", "public_price"], "place_order": ["ordered", "in_scope", "total", "remaining_under_mandate", "reasons"],
        "pay_and_ship": ["charged", "parcel", "arrival"], "confirm_delivery": ["as_offered"],
        "open_dispute": ["case_id", "revealed"], "respond_to_dispute": ["action", "amount", "verdict", "findings_against_me"],
        "case_status": ["status", "verdict", "remedy", "refunded_to_budget", "remaining_under_mandate"]}


def shorten(tool: str, res: dict) -> dict:
    if tool == "list_mandates":
        return {"mandates": [{k: m[k] for k in ("categories", "max_amount", "spent", "remaining", "status")} for m in res.get("mandates", [])]}
    if tool == "get_offer" and "offer" in res:
        o = res["offer"]
        return {"offer": {"sku": o["sku"], "total": o["total"], "recognition": o.get("recognition") and {"discount_pct": o["recognition"]["discount_pct"]}},
                **{k: res[k] for k in KEEP[tool] if k in res}}
    keys = KEEP.get(tool)
    return {k: res[k] for k in keys if k in res} if keys and "error" not in res else res


def args_short(tool: str, a: dict) -> dict:
    a = dict(a)
    for k in ("offer", "proof"):
        if isinstance(a.get(k), dict):
            a[k] = f"<the {k} object returned above>"
    if "offer_commit" in a:
        a["offer_commit"] = a["offer_commit"][:10] + "…"
    return a


def calls_table(label: str, limit_res: int = 215) -> str:
    rows = transcript(label)
    reqs = {r["n"]: r for r in rows if r["kind"] == "request"}
    out = ['<table class="calls"><thead><tr><th>#</th><th>request</th><th>response (fields shown: those the agent needed)</th></tr></thead><tbody>']
    for r in rows:
        if r["kind"] != "response":
            continue
        q = reqs[r["n"]]
        cls = ' class="err"' if r["is_error"] else ""
        out.append(f"<tr{cls}><td>{r['n']}</td><td><b>{E(q['server'])}.{E(q['tool'])}</b><br><code>{E(brief(args_short(q['tool'], q['arguments']), 150))}</code></td>"
                   f"<td><code>{E(brief(shorten(q['tool'], r['result']), limit_res))}</code></td></tr>")
    out.append("</tbody></table>")
    return "\n".join(out)


def checks_list(checks: dict, extra: dict | None = None) -> str:
    items = [f'<li class="{"ok" if ok else "no"}">{"PASS" if ok else "FAIL"} &nbsp;{E(name)}</li>' for name, ok in {**checks, **(extra or {})}.items()]
    return '<ul class="checks">' + "".join(items) + "</ul>"


def quote(text: str, n: int = 900) -> str:
    t = text.strip()
    t = t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + " […]"
    return '<blockquote>' + E(t).replace("**", "").replace("\n\n", "<br><br>").replace("\n", "<br>") + "</blockquote>"


def git(*a: str) -> str:
    return subprocess.run(["git", *a], cwd=HERE.parent, capture_output=True, text=True).stdout.strip()


def render(team: list[str], deck_url: str, video_url: str) -> str:
    r1, r1h, r2, b1, w1, w2 = (result(x) for x in ("run1", "run1_haiku", "run2", "boundary1", "wrong1", "wrong2"))
    before = Decimal(w1["seeded"]["remaining_before"])
    retro = {"W6 (added later) the refund gave the budget back": Decimal(w1["observed"]["remaining"]) == before,
             "W7 (added later) Mia ends up with the 10 kg bag she asked for": False}
    prereg = git("log", "-1", "--format=%h, %cI", "--", "usability/PREREGISTERED.md").split("\n")[0]
    import datetime as _dt
    h, ts = git("log", "--diff-filter=A", "--format=%h %ct", "--", "usability/PREREGISTERED.md").split()
    prereg_first = f"commit {h}, {_dt.datetime.fromtimestamp(int(ts), _dt.timezone.utc):%Y-%m-%d %H:%M:%S} UTC"
    fix_commit = w2["git_commit"]
    blob = f"{REPO}/blob/{BRANCH}"
    team_html = "".join(f"<li>{E(t)}</li>" for t in team) or "<li>[team members and contributions]</li>"
    deck = f'<a href="{E(deck_url)}">{E(deck_url)}</a>' if deck_url else "[link to our slides in the shared Demo Day deck]"
    video = f'<a href="{E(video_url)}">{E(video_url)}</a>' if video_url else "[unlisted YouTube link, embedded on slide 4]"

    def runrow(r, note):
        return (f"<tr><td>{E(r['label'])}</td><td>{E(r['task'])}</td><td>{E(r['model'])}</td><td>{r['tool_calls']} calls, {r['seconds']} s</td>"
                f"<td><b>{r['passed']}/{r['of']}</b></td><td>{note}</td></tr>")

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>TrailProof HW9</title><style>
@page {{ size: Letter; margin: 15mm 15mm 16mm; }}
@page slide {{ size: 13.333in 7.5in; margin: 0; }}
* {{ box-sizing: border-box; }}
body {{ font: 9.6pt/1.42 "Segoe UI", Helvetica, Arial, sans-serif; color: #14213D; margin: 0; }}
h1 {{ font-size: 19pt; margin: 0 0 2pt; letter-spacing: -.01em; }}
h2 {{ font-size: 12.5pt; margin: 15pt 0 5pt; border-bottom: 1.5pt solid #14213D; padding-bottom: 2pt; break-after: avoid; }}
h3 {{ font-size: 10.2pt; margin: 9pt 0 3pt; break-after: avoid; }}
p {{ margin: 0 0 5pt; }} ul {{ margin: 0 0 5pt; padding-left: 15pt; }} li {{ margin-bottom: 1.5pt; }}
a {{ color: #14479c; text-decoration: none; }} code {{ font: 7.9pt Consolas, monospace; word-break: break-word; }}
.sub {{ color: #5C6B7A; margin-bottom: 8pt; }}
table {{ border-collapse: collapse; width: 100%; margin: 4pt 0 7pt; font-size: 8.5pt; }}
th, td {{ border: .6pt solid #C3D0CA; padding: 2.5pt 5pt; vertical-align: top; text-align: left; }}
th {{ background: #E6EEEA; }} tr {{ break-inside: avoid; }}
table.calls td:first-child {{ width: 16pt; text-align: right; color: #5C6B7A; }} table.calls td:nth-child(2) {{ width: 37%; }}
tr.err td {{ background: #FDECEE; }}
ul.checks {{ list-style: none; padding: 0; font-size: 8.8pt; }} ul.checks li {{ padding: 1pt 0; }}
.ok {{ color: #1E7A4C; }} .no {{ color: #C8102E; font-weight: 600; }}
blockquote {{ margin: 4pt 0 7pt; padding: 5pt 9pt; border-left: 3pt solid #FFD23F; background: #FFFBEA; font-size: 8.8pt; }}
.box {{ border: 1pt solid #14213D; padding: 6pt 9pt; margin: 5pt 0 8pt; break-inside: avoid; }}
pre {{ font: 7.9pt/1.35 Consolas, monospace; background: #F3F6F5; padding: 6pt 8pt; margin: 3pt 0 7pt; white-space: pre-wrap; break-inside: avoid; }}
.two {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10pt; }}
/* slides */
section.slide {{ page: slide; break-before: page; width: 13.333in; height: 7.5in; padding: .55in .7in; background: #E6EEEA; position: relative; overflow: hidden; font-size: 17pt; line-height: 1.3; }}
.slide .tag {{ font-size: 12pt; color: #5C6B7A; display: flex; justify-content: space-between; }}
.slide h1 {{ font-size: 40pt; line-height: 1.04; letter-spacing: -.03em; margin: .22in 0 .2in; max-width: 11.4in; }}
.slide h2 {{ font-size: 15pt; border: 0; margin: 0 0 5pt; color: #5C6B7A; font-weight: 600; padding: 0; }}
.slide .cols {{ display: grid; gap: .3in; }} .slide .card {{ background: #fff; border-radius: 10pt; padding: .2in .24in; }}
.slide .dark {{ background: #14213D; color: #E6EEEA; }} .slide .dark h2 {{ color: #9FB0C3; }}
.slide .mark {{ background: #FFD23F; }} .slide .mark h2 {{ color: #6B5A12; }}
.slide ul {{ padding-left: 17pt; margin: 0; }} .slide li {{ margin-bottom: 5pt; }}
.slide .num {{ font-size: 34pt; font-weight: 800; letter-spacing: -.03em; line-height: 1; display: block; margin: 4pt 0 9pt; }}
.slide .small {{ font-size: 12.5pt; color: #5C6B7A; }}
.slide .video {{ background: #14213D; color: #9FB0C3; border-radius: 10pt; display: flex; align-items: center; justify-content: center; text-align: center; font-size: 15pt; }}
.slide .step {{ display: grid; grid-template-columns: .62in 1fr; gap: .18in; align-items: start; margin-bottom: .2in; }}
.slide .step b.n {{ width: .62in; height: .62in; border-radius: 50%; background: #14213D; color: #FFD23F; font-size: 22pt; display: flex; align-items: center; justify-content: center; }}
.slide .step .t {{ font-weight: 700; font-size: 18pt; line-height: 1.15; }} .slide .step .d {{ font-size: 14pt; color: #2D3B58; margin-top: 3pt; }}
.slide .shot {{ width: 100%; border-radius: 10pt; border: 1pt solid #C3D0CA; display: block; }}
.slide .foot {{ position: absolute; left: .7in; right: .7in; bottom: .36in; font-size: 12pt; color: #5C6B7A; }}
.slide .vs {{ font-size: 13pt; color: #5C6B7A; }}
.arch {{ display: grid; grid-template-columns: 1fr auto 1.15fr auto 1fr; align-items: center; gap: .12in; }}
.arch .arrow {{ font-size: 13pt; color: #5C6B7A; text-align: center; line-height: 1.25; }}
</style></head><body>

<h1>TrailProof: can a fresh agent use it from the documentation?</h1>
<p class="sub">MIT AI Studio, Fall 2026, Homework 9: agent usability and final project slides. Project: TrailProof, the cookie rebuilt for AI agents.</p>

<div class="box">
<b>Interface documentation:</b> <a href="{blob}/docs/AGENT_GUIDE.md">{blob}/docs/AGENT_GUIDE.md</a><br>
<b>Repository:</b> <a href="{REPO}/tree/{BRANCH}">{REPO}</a> (branch <code>{BRANCH}</code>, runs recorded at commits <code>{r1['git_commit']}</code> and <code>{fix_commit}</code>)<br>
<b>Test harness and criteria:</b> <code>usability/cold_start.py</code>, <code>usability/PREREGISTERED.md</code>; recorded runs in <code>usability/runs/</code><br>
<b>Final slides in the shared deck:</b> {deck}<br>
<b>Demo video (unlisted):</b> {video}<br>
<b>Independent or self-run:</b> self-run. The client was a fresh model session started by our own harness, with no memory of the project and no access to its code. No human passed messages or corrected a call during any run. Manual help: none during runs; the human-side setup (creating the wallet, signing the mandate, two earlier purchases) was done by the harness before the agent started, and is disclosed in section 2.
</div>

<h2>1. The interface, in short</h2>
<p>TrailProof is shared infrastructure for purchases made by AI agents. A buyer agent uses it through one MCP server, the user's <b>wallet</b>; a shop's agent runs its own MCP server; both commit signed hashes of each order step to a neutral <b>ledger</b>. The guide linked above documents all of this for an agent reader. Summary of what it specifies:</p>
<table><tbody>
<tr><th style="width:19%">What it offers</th><td>Authority an agent can show (orders only inside a mandate the user signed), a purchase history it can prove to a shop (signed, counts only), and a record that settles disputes.</td></tr>
<tr><th>Access</th><td>MCP over stdio: <code>python -m wallet serve</code> (9 tools) for the buyer, <code>python -m demo_shop</code> (4 tools) as a practice shop, <code>python -m trailproof.server --http</code> for the ledger. Setup is four commands, run once by the person.</td></tr>
<tr><th>Inputs and outputs</th><td>A table per tool: arguments and returned fields, e.g. <code>prove_history(audience_party_id, merchant_party_id="*")</code> returns a signed proof object to pass on unchanged; <code>place_order(order_id, offer, offer_commit)</code> returns <code>ordered</code> with a total and the budget left, or the reasons for refusal.</td></tr>
<tr><th>Permissions and limits</th><td>An agent cannot create or widen a mandate (no such tool; the person does it on the command line). The ledger refuses orders outside the mandate, with reasons. A proof works at one shop and discloses counts only. Errors are values: <code>{{"error": CODE, "detail": ...}}</code>.</td></tr>
<tr><th>Example</th><td>One small worked example: listing mandates, reading a shop's id, and getting a proof for that shop, with real request and response shapes. It is deliberately not the purchase flow the test asks for.</td></tr>
</tbody></table>

<h2>2. Cold-start integration</h2>
<h3>What the agent was given, and what it was not</h3>
<p>A new model session received exactly: the interface guide, the two servers' MCP instructions and tool schemas, the real tools, and one message in the user's words. It received no sequence of calls and no expected answer. Authorised access: a wallet for a user "Mia" with one mandate (<code>pet_food</code>, 120.00 EUR) and two earlier completed orders at the practice shop, created by the harness before the agent started. Each run uses a throwaway ledger, wallet and shop, so runs are independent of each other.</p>
<h3>What counted as a useful result, fixed before the run</h3>
<p>The criteria are in <code>usability/PREREGISTERED.md</code> ({E(prereg_first)}), before the first agent run (recorded {E(r1['recorded_at'][:19].replace('T', ' '))} UTC). They are checked by code that reads the ledger and the wallet file afterwards, not by trusting the agent's report. The task: <i>"{E(r1['prompt'].replace("Hi, it's Mia. ", ""))}"</i></p>
<p>Mia has two completed orders at this shop, so she is entitled to its returning-customer price. Three outcomes are distinguishable: 33.21 if the agent presents a proof of its history <i>with this shop</i>, 35.06 with a proof across all shops, 36.90 with no proof. Only the first is a full success, and it requires the agent to take the object returned by <code>prove_history</code> and hand it to the shop in the right mode. That is the check of result use (L2).</p>

<h3>Run <code>run1</code>: requests and responses ({E(r1['model'])}, {r1['tool_calls']} tool calls, {r1['seconds']} s)</h3>
{calls_table('run1')}
<div class="two"><div><b>Outcome against the pre-registered checks</b>{checks_list(r1['checks'])}</div>
<div><b>Result use.</b> Call 4 returns a signed proof; call 6 passes that object to the shop, which answers <code>recognised: true</code> and commits 33.21 instead of 36.90; call 7 passes the shop's offer to the wallet, and the ledger records the order. The agent then told the user two figures, 33.21 paid and 12.99 left, and both match the ledger.</div></div>
<p><b>The agent's report to the user (excerpt):</b></p>{quote(r1['final_text'], 620)}

<h3>All recorded runs</h3>
<table><thead><tr><th>run</th><th>task</th><th>agent</th><th>effort</th><th>checks</th><th>note</th></tr></thead><tbody>
{runrow(r1, "first attempt; guide as first written")}
{runrow(r1h, "a different, smaller model on the same task: same result, proof fetched after opening the order")}
{runrow(b1, "boundary case, section 3")}
{runrow(w1, "failure case; passed its checks and still exposed a project flaw, section 4")}
{runrow(w2, "rerun after the improvement, section 4")}
{runrow(r2, "first task again after the improvement: no regression")}
</tbody></table>
<p>Two different models completed the task from the documentation alone, with no errors returned by any tool in either run. The limits of this evidence are in section 5.</p>

<h2>3. Boundary case: a request the mandate does not allow</h2>
<p>Task: <i>"{E(b1['prompt'].replace("Hi, it's Mia. ", ""))}"</i> The mandate covers <code>pet_food</code> only; the toy set is <code>cat_toys</code> at 55.00, also more than the 46.20 left. A useful result is a refusal that helps: nothing bought, nothing disguised, and the user told how to authorise it.</p>
{calls_table('boundary1', 260)}
<div class="two"><div>{checks_list(b1['checks'])}</div><div>{quote(b1['final_text'], 520)}</div></div>
<p>The agent stopped after two read-only calls and never attempted the order, so the ledger's own refusal was not exercised in this run; that path is covered by unit tests (<code>tests/test_wallet.py</code>, <code>tests/test_scope.py</code>) rather than by an agent. Its claim that "the ledger would refuse" came from the guide, and is correct.</p>

<h2>4. Failure case, improvement and rerun</h2>
<p>Task: <i>"{E(w1['prompt'].replace("Hi, it's Mia. ", ""))}"</i> Test setup: the practice shop ships a 5 kg bag instead of the 10 kg bag.</p>
<h3>Run <code>wrong1</code>, before the improvement</h3>
{calls_table('wrong1', 200)}
<p>The run passed all five pre-registered checks: the agent committed what really arrived, disputed it, and the shop refunded. It still ended badly for the user, and the agent said so:</p>
{quote(w1['final_text'][w1['final_text'].find('**Things to know**'):] if '**Things to know**' in w1['final_text'] else w1['final_text'], 560)}
<h3>What the first runs showed, and whose problem each was</h3>
<table><thead><tr><th style="width:27%">Observation</th><th style="width:13%">Kind</th><th>Evidence</th><th style="width:27%">Change</th></tr></thead><tbody>
<tr><td><b>A refund did not return the money to the mandate</b>, so the agent could not reorder and the user had neither goods nor budget.</td><td>project limitation</td><td><code>wrong1</code>: after a 33.21 refund, <code>list_mandates</code> (call 14) still shows 12.99 left of the 46.20 before the order.</td><td><b>The improvement.</b> The ledger credits a refunded order's amount back to its mandate, once; the wallet's budget follows; <code>case_status</code> reports it.</td></tr>
<tr><td>The decided case carried a finding "CHARGE was never revealed".</td><td>interface (practice shop)</td><td><code>wrong1</code> call 13; the agent reported it to the user as an oddity.</td><td>The shop's payment provider now reveals its charge when the shop answers a dispute.</td></tr>
<tr><td>The agent fetched both kinds of proof "to see which earns the better price".</td><td>documentation</td><td><code>wrong1</code> calls 4 and 5: one wasted call.</td><td>The guide and the tool description now say which proof to use when.</td></tr>
<tr><td>The agent confirmed delivery from a shipping notice and warned the user it had done so.</td><td>documentation and interface</td><td><code>run1</code> report: "the parcel hasn't physically reached you yet".</td><td>The guide says shipping is not delivery; the practice shop states that its delivery is simulated.</td></tr>
</tbody></table>
<p>None of the four was a test-setup problem. One test-setup change was made for the rerun and is not counted as an improvement: the shop ships the wrong product once instead of every time, because against a shop that is always wrong no reorder could succeed.</p>
<h3>Regression tests</h3>
<p><code>tests/test_refund_budget.py</code> reproduces the situation of <code>wrong1</code> without a model: a 40.00 budget, a 36.90 order that goes wrong, a refund, a reorder. All six of its tests fail on the commit before the fix (<code>{r1['git_commit']}</code>) and pass after it (<code>{fix_commit}</code>); the full suite is 89 passing. They also check that the credit happens once, only for the refunded order, and that an order that was not refunded still counts.</p>
<h3>Run <code>wrong2</code>, after the improvement (same task, a new agent session)</h3>
{calls_table('wrong2', 200)}
<table><thead><tr><th>check</th><th>wrong1 (before)</th><th>wrong2 (after)</th></tr></thead><tbody>
{"".join(f"<tr><td>{E(name)}</td><td class='{'ok' if a else 'no'}'>{'PASS' if a else 'FAIL'}</td><td class='{'ok' if b else 'no'}'>{'PASS' if b else 'FAIL'}</td></tr>" for (name, b), a in zip(w2['checks'].items(), list(w1['checks'].values()) + list(retro.values())))}
<tr><td>budget left after the refund</td><td>{E(w1['observed']['remaining'])} of 46.20</td><td>{E(w2['observed']['remaining'])} of 46.20</td></tr>
<tr><td>tool calls; "unverifiable" finding in the case</td><td>{w1['tool_calls']}; present</td><td>{w2['tool_calls']}; absent</td></tr>
</tbody></table>
<p>W6 and W7 were added after seeing <code>wrong1</code>, so they measure the improvement and are not independent predictions; <code>wrong1</code> is scored against them retroactively.</p>
{quote(w2['final_text'][w2['final_text'].find('- **Your budget:**'):] if '- **Your budget:**' in w2['final_text'] else w2['final_text'], 520)}
<p><b>What improved.</b> The blocker is gone: the budget came back (46.20), the agent knew it from <code>case_status</code>, and it told the user a reorder was possible and at what price. The spurious finding is gone, and the agent fetched one proof instead of two.</p>
<p><b>What did not.</b> W7 still fails: Mia does not have her dog food. The agent chose to ask before buying again from a shop that had just shipped the wrong item. That is now the agent's judgement rather than a limit of the project, and arguably the right call, but the user's goal was not completed in one turn.</p>

<h2>5. What remains unproven</h2>
<ul>
<li><b>Independence.</b> Both client models are from one vendor and were run by us. No other builder's agent or harness has used the interface. Server instructions and tool descriptions were written by the same people who wrote the tasks.</li>
<li><b>Small numbers.</b> Six runs, one per condition. Model output varies; a single pass or fail is an observation, not a rate.</li>
<li><b>The reorder path with an agent.</b> That a refunded budget can be spent again is shown by the regression tests, not by an agent actually reordering.</li>
<li><b>The ledger's refusal under an agent.</b> In the boundary run the agent declined before trying, so the refusal itself was not exercised by an agent.</li>
<li><b>Real counterparties.</b> The shop and its payment provider are mocks on one machine. Delivery is simulated. Both wallet keys are in one file; a real deployment would hold the user's key in a passkey.</li>
<li><b>Partial refunds.</b> The ledger sees only a refund's hash, so it credits the whole order back.</li>
</ul>

<h2>6. Repeating the test</h2>
<pre>git clone {REPO} && cd TrailProof && git checkout {BRANCH} && uv sync --extra llm
export ANTHROPIC_API_KEY=...                                   # the only credential needed; the servers under test never see it
uv run pytest                                                  # 89 tests, incl. tests/test_refund_budget.py
uv run python usability/cold_start.py --task loyal_purchase --label mine
uv run python usability/cold_start.py --task out_of_mandate --label mine_boundary
uv run python usability/cold_start.py --task wrong_parcel   --label mine_wrong      # add --model claude-haiku-5-5 for another agent
git checkout {r1['git_commit']} && uv run python usability/cold_start.py --task wrong_parcel --label before_fix   # the earlier behaviour</pre>
<p>Each run starts its own ledger, wallet and shop, gives the agent the guide and the task, prints every request and response, scores the outcome, and writes <code>usability/runs/&lt;label&gt;/transcript.jsonl</code> and <code>result.json</code>. The recorded runs in the repository are those files, unedited.</p>

<h2>7. Team</h2>
<ul>{team_html}</ul>

<section class="slide">
  <div class="tag"><span>TrailProof</span><span>1 / 4</span></div>
  <h1 style="font-size:37pt;">Soon an AI assistant will do your shopping. Shops cannot tell it from a bot.</h1>
  <div class="cols" style="grid-template-columns: 1fr 1fr 1.08fr;">
    <div class="card"><h2>Today: you, in a browser</h2>A small file called a cookie lets a shop recognise you when you return. That is how you get your basket, your history and your loyalty price.</div>
    <div class="card dark"><h2>Tomorrow: your AI assistant</h2>It has no cookie. To the shop it is a stranger, the same as a thousand price-scraping bots. No loyalty price. And if the order goes wrong, it is the shop's records against yours.</div>
    <div class="card mark"><h2>TrailProof</h2>A cookie rebuilt for AI assistants: proof of who the assistant shops for and how it has behaved. <b>You hold it. It cannot be faked. It cannot be used to track you.</b></div>
  </div>
  <div class="foot">An AI agent is software that acts for a person: here, an assistant that finds, orders and pays for things on your behalf. Our users are these agents, on both sides of a sale.</div>
</section>

<section class="slide">
  <div class="tag"><span>TrailProof &nbsp; how it works</span><span>2 / 4</span></div>
  <h1 style="font-size:31pt; margin-bottom:.22in;">Three steps. Each one can be checked by anyone.</h1>
  <div class="cols" style="grid-template-columns: 1.02fr 1fr; align-items: start;">
    <div>
      <div class="step"><b class="n">1</b><div><div class="t">You set a spending permission.</div><div class="d">"Pet food, up to 80 euros, this month." Your assistant can use it but can never change it.</div></div></div>
      <div class="step"><b class="n">2</b><div><div class="t">Your assistant proves its track record.</div><div class="d">"2 completed orders here, no disputes lost." Signed by a neutral record keeper. Numbers only: never what you bought or where.</div></div></div>
      <div class="step"><b class="n">3</b><div><div class="t">The shop recognises you, and every step is sealed.</div><div class="d">You get your loyalty price. Each side locks a fingerprint of what it promised, so a wrong delivery is settled from proof and refunded automatically.</div></div></div>
    </div>
    <div><img class="shot" src="assets/recognised.png" alt="Demo screen: a returning customer's assistant is recognised and gets 33.21 instead of 36.90">
      <div class="vs" style="margin-top:6pt;">From our demo. Left: the assistant at the shop's door. Middle: the sealed record. Right: the price it earns.</div></div>
  </div>
  <div class="foot"><b>What we built:</b> three pieces of software that AI assistants plug into directly, using the open MCP standard for giving tools to AI: a <b>wallet</b> for the customer's assistant, a <b>plug-in</b> for the shop's assistant, and a neutral <b>record keeper</b> between them. No website, no human in the loop.</div>
</section>

<section class="slide">
  <div class="tag"><span>TrailProof &nbsp; does it work?</span><span>3 / 4</span></div>
  <h1 style="font-size:31pt;">What we tested, and what broke.</h1>
  <div class="cols" style="grid-template-columns: repeat(4, 1fr); font-size:13.5pt;">
    <div class="card dark"><h2>Can a new AI use it?</h2><span class="num">2 of 2</span>AI models we had never set up read only our manual and bought correctly on the first try, at the right price.</div>
    <div class="card"><h2>Does it judge fairly?</h2><span class="num">0 vs 46</span>honest parties wrongly blamed in 240 simulated disputes where one side lied. Ours: 0. Comparing each side's own records: 46.</div>
    <div class="card"><h2>Can it be cheated?</h2><span class="num">Caught</span>A seller that shipped the wrong product and then edited its records was exposed automatically, in a shared test environment run by the course.</div>
    <div class="card mark"><h2>What failed</h2><span class="num">1 real bug</span>A fresh AI showed us that a refund did not give the spending allowance back. Found by testing, then fixed.</div>
  </div>
  <div class="cols" style="grid-template-columns: 1fr 1fr; margin-top:.26in; font-size:14.5pt;">
    <div><b>What worked.</b> Assistants picked the right proof on their own, refused to buy outside the permission and told the user how to allow it, and took a wrong delivery all the way to a refund.</div>
    <div><b>What still does not.</b> After the fix, the assistant got the refund but asked before buying again, so the customer still had no dog food. And every test so far was run by us, with AI models from one company.</div>
  </div>
</section>

<section class="slide">
  <div class="tag"><span>TrailProof &nbsp; see it, and what comes next</span><span>4 / 4</span></div>
  <div class="cols" style="grid-template-columns: 1.55fr 1fr; margin-top:.25in; height: 5.75in;">
    <div class="video">[ demo video, 30 seconds, plays automatically ]<br><br>a returning customer is recognised,<br>an impostor is caught,<br>a restaurant waives a deposit for a guest it can trust</div>
    <div style="display:grid; gap:.16in; font-size:13.5pt;">
      <div class="card"><h2>Real today</h2><ul><li>Working, open-source software</li><li>89 automated tests</li><li>A live AI assistant buying through it</li></ul></div>
      <div class="card dark"><h2>Not yet</h2><ul><li>Shops and payments are simulated</li><li>Your key sits in a file, not a passkey</li></ul></div>
      <div class="card mark"><h2>Next</h2><ul><li>A plug-in for a real shop</li><li>Another team's assistant using it</li><li>Loyalty points on top</li></ul></div>
    </div>
  </div>
  <p class="small" style="position:absolute; left:.7in; bottom:.3in;">{E(REPO)}</p>
</section>
</body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--team", action="append", default=[], help="one team member and contribution; repeatable")
    ap.add_argument("--deck-url", default="", help="link to the team's slides in the shared deck")
    ap.add_argument("--video-url", default="", help="the unlisted YouTube link")
    args = ap.parse_args()
    out_html, out_pdf = HERE / "report.html", HERE / "HW9_TrailProof_usability.pdf"
    out_html.write_text(render(args.team, args.deck_url, args.video_url), encoding="utf-8")
    browser = next((b for b in BROWSERS if Path(b).exists()), None)
    if not browser:
        print(f"wrote {out_html}; no Chrome or Edge found for the PDF", file=sys.stderr)
        return 1
    subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={out_pdf}", out_html.as_uri()],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180, check=True)
    print(f"wrote {out_html}\nwrote {out_pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
