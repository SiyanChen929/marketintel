"""Quality control for strategic assessments (LLM output is data, so it is checked like data).
Checks: schema validity, score ranges, weighted-score consistency (recomputed), evidence dates (parseable,
not in the future, news_asof within 90 days), evidence count, thesis length, rationale presence, and
per-segment calibration statistics (mean / spread of scarcity strength) to catch agent-level bias.
Usage: python scripts/qc_assessments.py [--fix]   (--fix rewrites recomputed strategic_score and news_asof)"""
import sys, json, glob, argparse
from pathlib import Path
from datetime import date
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd
from ai_semi_intel.analysis import strategic as S
from ai_semi_intel.config.universe import universe_frame

ap = argparse.ArgumentParser(); ap.add_argument("--fix", action="store_true"); a = ap.parse_args()
uni = universe_frame(); today = pd.Timestamp(date.today())
rows, problems = [], []
for f in sorted(glob.glob(str(S.ASSESS_DIR / "*.json"))):
    t = Path(f).stem
    try:
        d = json.load(open(f))
    except Exception as e:
        problems.append((t, "invalid JSON", str(e)[:80])); continue
    try:
        S.Assessment.model_validate({**d, "version": d.get("version", 1), "triggers_since_last": d.get("triggers_since_last", []), "previous_version_score": d.get("previous_version_score")})
    except Exception as e:
        problems.append((t, "schema", str(e).splitlines()[0][:100]))
    dims = d.get("dimensions", {})
    missing = [k for k in S.DIMENSION_WEIGHTS if k not in dims]
    if missing:
        problems.append((t, "missing dimensions", ",".join(missing)))
    for k, v in dims.items():
        sc = v.get("score") if isinstance(v, dict) else None
        if sc is None or not (0 <= float(sc) <= 1):
            problems.append((t, "score out of range", f"{k}={sc}"))
        if isinstance(v, dict) and len(str(v.get("rationale", ""))) < 20:
            problems.append((t, "thin rationale", k))
    ws = S.weighted_score(dims)
    if abs(ws - float(d.get("strategic_score", -1))) > 0.02:
        problems.append((t, "weighted score mismatch", f"file {d.get('strategic_score')} vs recomputed {ws:.3f}"))
    ev = d.get("evidence", [])
    if not (3 <= len(ev) <= 8):
        problems.append((t, "evidence count", str(len(ev))))
    latest = None
    for e in ev:
        ts = S._safe_ts(e.get("date"))
        if ts is None:
            problems.append((t, "unparseable evidence date", str(e.get("date"))))
        elif ts > today + pd.Timedelta(days=1):
            problems.append((t, "future evidence date", str(e.get("date"))))
        else:
            latest = ts if latest is None or ts > latest else latest
        if e.get("dimension") not in list(S.DIMENSION_WEIGHTS) + ["customer_concentration"]:
            problems.append((t, "evidence dimension key", str(e.get("dimension"))))
    na = S._safe_ts(d.get("news_asof"))
    if na is None or (latest is not None and abs((na - latest).days) > 45):
        problems.append((t, "news_asof inconsistent with evidence", f"{d.get('news_asof')} vs latest evidence {latest.date() if latest is not None else None}"))
    if len(str(d.get("thesis", "")).split()) > 75:
        problems.append((t, "thesis too long", str(len(d['thesis'].split())) + " words"))
    if not (0 <= float(d.get("confidence", -1)) <= 1) or not (-1 <= float(d.get("news_direction", -2)) <= 1):
        problems.append((t, "confidence/news range", ""))
    if a.fix:
        d["strategic_score"] = round(ws, 4)
        if latest is not None and (na is None or abs((na - latest).days) > 45):
            d["news_asof"] = str(latest.date())
        json.dump(d, open(f, "w"), indent=1)
    rows.append({"ticker": t, "segment": uni.loc[t, "segment"] if t in uni.index else "?", "scarcity_strength": S.scarcity_strength(dims), "llm_risk": S.llm_risk(dims),
                 "strategic_score": ws, "confidence": d.get("confidence"), "news": d.get("news_direction"), "n_evidence": len(ev), "assessor": d.get("assessor")})
df = pd.DataFrame(rows).set_index("ticker")
print(f"assessments: {len(df)} | problems: {len(problems)}")
for p in problems[:40]:
    print("  -", p)
print("\nper-segment calibration (scarcity strength):")
print(df.groupby("segment")["scarcity_strength"].agg(["count", "mean", "std", "min", "max"]).round(2).to_string())
print("\nrisk (LLM) by segment:"); print(df.groupby("segment")["llm_risk"].agg(["mean", "min", "max"]).round(2).to_string())
print("\nuniverse-wide: scarcity mean %.2f sd %.2f | risk mean %.2f | confidence mean %.2f | news mean %+.2f" % (df.scarcity_strength.mean(), df.scarcity_strength.std(), df.llm_risk.mean(), df.confidence.mean(), df.news.mean()))
df.round(3).to_csv(S.STRAT_DIR / "qc_summary.csv")
