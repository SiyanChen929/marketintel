"""Repeatable acceptance checks against the running app (default http://127.0.0.1:8787).

    python scripts/verify_acceptance.py [--base URL]

Checks (P0): one run_id across status / summary / portfolio / universe / ticker / macro; executable weights + cash
reconcile to 100%; target-list wording (no "held"); evidence banner and run chip on every page; hindsight label and
standard Sharpe on the portfolio page; story badges and contributions in the feed; lineage on LLM-classified events.
Exit code 1 on any failure. Read-only: GET requests only.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request


def get(base: str, path: str, raw: bool = False):
    with urllib.request.urlopen(base + path, timeout=30) as r:
        body = r.read()
    return body.decode() if raw else json.loads(body)


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--base", default="http://127.0.0.1:8787"); a = ap.parse_args()
    fails: list[str] = []
    ok = lambda cond, msg: fails.append(msg) if not cond else None
    st = get(a.base, "/api/status"); s = get(a.base, "/api/research/summary"); p = get(a.base, "/api/research/portfolio")
    u = get(a.base, "/api/research/universe"); t = get(a.base, "/api/research/ticker/MU"); m = get(a.base, "/api/research/macro")
    ids = {x["run"]["run_id"] for x in (st, s, p, u, t, m)}
    ok(len(ids) == 1 and None not in ids, f"run_id differs across APIs: {ids}")
    inv = sum(h["weight"] for h in p["holdings"]); cash = p["recommended_cash"]
    ok(abs(inv + cash - 1) < 1e-6 and p["reconciles"], f"targets + cash != 100%: invested {inv:.6f} cash {cash:.6f}")
    ok(all(h["weight"] <= h["weight_model"] + 1e-12 for h in p["holdings"]), "executable weight above model weight")
    held_u = {r["ticker"]: r["weight"] for r in u["rows"] if r["is_holding"]}
    ok(all(abs(held_u[h["ticker"]] - h["weight"]) < 1e-9 for h in p["holdings"]), "universe weights differ from portfolio weights")
    ok(t["holding"] and abs(t["holding"]["weight"] - held_u.get("MU", -1)) < 1e-9, "ticker MU weight differs")
    ok(st["run"]["validation_status"].startswith("NOT VALIDATED"), "validation status missing")
    for path in ("/", "/portfolio", "/universe", "/ticker/MU", "/macro", "/research"):
        html = get(a.base, path, raw=True)
        ok('id="banner"' in html and "statusBanner(" in html and "runChip(" in html, f"{path}: banner/run chip missing")
        ok("held " not in html.lower().replace("held names", "") or path == "/", f"{path}: 'held' wording present")
    port = get(a.base, "/portfolio", raw=True)
    ok("Invalid as track record" in port and "Sharpe (daily excess)" in port and "sharpeStd" in port, "portfolio: hindsight label / standard Sharpe missing")
    bts = p["backtest"]["stats"]["Portfolio"]
    ok("sharpe" in bts, "backtest stats missing sharpe")
    ev = get(a.base, "/api/events?limit=50&collapse=true")
    ok(ev.get("collapsed") is True and all("impacts" in e and "readthrough" in e for e in ev["events"]), "events API lacks collapse/contributions")
    llm_ev = [e for e in ev["events"] if e.get("llm") == 1]
    ok(all(e.get("classifier") in ("llm", "human") for e in llm_ev), "LLM-classified events without classifier lineage")
    stories = [e for e in ev["events"] if e.get("story") and e["story"]["n_events"] > 1]
    ok(len(stories) >= 1, "no multi-copy stories in the feed (clustering inactive?)")
    # P1 backend: gzip, cache headers, action feed, cases, risk workbench, dossier
    req = urllib.request.Request(a.base + "/api/events?limit=50", headers={"Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=30) as r:
        ok(r.headers.get("Content-Encoding") == "gzip", "events API not gzip-compressed")
    with urllib.request.urlopen(a.base + "/static/base.js", timeout=30) as r:
        ok("max-age" in (r.headers.get("Cache-Control") or ""), "static files without Cache-Control")
    act = get(a.base, "/api/actions?hours=48")
    ok(act["run"]["run_id"] in ids and {"action_required", "portfolio_impact", "catalysts", "new_evidence", "system_health", "counts"} <= set(act), "action feed shape")
    ok(all(x["ticker"] in held_u for x in act["action_required"]), "action feed lists non-target names")
    risk = get(a.base, "/api/research/risk")
    ok(risk["weights"]["reconciles"] and abs(risk["weights"]["cash"] - cash) < 1e-6, "risk workbench does not reconcile to the run")
    ok(risk["var"].get("var_95") is not None and risk["compliance"], "risk workbench missing VaR or compliance")
    ok(risk["current_vs_target"] and all(x.get("current_live_weight") is None for x in (risk["current_vs_target"] if isinstance(risk["current_vs_target"], list) else risk["current_vs_target"].get("rows", []))), "current_vs_target claims live weights")
    cases = get(a.base, "/api/cases"); ok("summary" in cases, "cases API shape")
    d = get(a.base, "/api/research/dossier/MU")
    ok(d["run"]["run_id"] in ids and d["pt_bridge"] and d["strategic"] is not None and "expected_return" in d, "dossier shape")
    ok(d["scenarios"] is None or {"base", "bull", "bear"} <= set(d["scenarios"]), "dossier scenarios shape")
    js = get(a.base, "/static/base.js", raw=True)
    home = get(a.base, "/", raw=True)
    order = [home.find(k) for k in ("Action required", "Portfolio impact", "Upcoming catalysts", "New evidence", "System", "Live feed")]
    ok(all(i >= 0 for i in order) and order == sorted(order), f"home panel order wrong: {order}")
    ok("gnews_" not in home and "(FTS)" not in home and "/api/actions" in home, "home still exposes internal ids or lacks the action feed")
    cases_html = get(a.base, "/cases", raw=True)
    ok("/api/cases" in cases_html and "no-trade" in cases_html.lower() and "sign" in cases_html.lower(), "/cases page wiring")
    ok("/risk" in js and "/cases" in js and "Cases" in js, "nav lacks Risk / Cases")
    risk_html = get(a.base, "/risk", raw=True)
    ok("no live position ledger" in risk_html and "/api/research/risk" in risk_html and "held" not in risk_html.lower().replace("held names", ""), "/risk page wording or wiring")
    tick_html = get(a.base, "/ticker/MU", raw=True)
    ok('id="dossier"' in tick_html and "/api/research/dossier/" in tick_html and "kill criteria" in tick_html.lower(), "ticker dossier section missing")
    js = get(a.base, "/static/base.js", raw=True)
    ok("Claude-classified" not in get(a.base, "/", raw=True) and "hindsight illustration" in js, "vendor hardcode or banner text missing")
    print(f"run {next(iter(ids))} · invested {inv:.4%} + cash {cash:.4%} · {len(p['holdings'])} names · feed {ev['count']} stories, {len(stories)} multi-copy · "
          f"actions {len(act['action_required'])} · VaR95 {risk['var'].get('var_95')} · cases open {cases['summary'].get('open')}")
    if fails:
        print("FAIL"); [print(" -", f) for f in fails]; return 1
    print("PASS: all acceptance checks"); return 0


if __name__ == "__main__":
    sys.exit(main())
