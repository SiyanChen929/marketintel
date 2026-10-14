"""Regenerate the LaTeX table inputs for the paper from the latest pipeline outputs.
Writes outputs/paper/strategic_table.tex and outputs/paper/strategic_stats.tex."""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd, numpy as np
from ai_semi_intel.analysis import strategic as S

LATEST = Path("outputs/latest"); OUT = Path("outputs/paper")
t = pd.read_csv(LATEST / "strategic_table.csv", index_col=0)
top = pd.read_csv(LATEST / "top15.csv", index_col=0)
ev = t[t["strategic_source"] == "evidence_assessment"]
def esc(s): return str(s).replace("&", "\\&").replace("%", "\\%").replace("_", "\\_")
rows = []
for tk, r in ev.sort_values("scarcity_strength", ascending=False).head(12).iterrows():
    gate = "capped" if bool(r.get("risk_capped")) else ("excluded" if r.get("risk_eligible") is False else "ok")
    rows.append(f"{tk} & {esc(r['segment'])[:26]} & {r['scarcity_strength']:.2f} & {r['llm_risk']:.2f} & {r['financial_risk']:.2f} & {r['risk_score']:.2f} & {r['strategic_pillar']:.2f} & {r['news_direction']:+.2f} & {r['strategic_confidence']:.2f} & {gate}\\\\")
gated = t[t.get("risk_gated_out", pd.Series(False, index=t.index)).fillna(False).astype(bool)]
for tk, r in gated.iterrows():
    rows.append(f"{tk} & {esc(r['segment'])[:26]} & {r['scarcity_strength']:.2f} & {r['llm_risk']:.2f} & {r['financial_risk']:.2f} & {r['risk_score']:.2f} & {r['strategic_pillar']:.2f} & {r['news_direction']:+.2f} & {r['strategic_confidence']:.2f} & excluded\\\\")
(OUT / "strategic_table.tex").write_text("\n".join(rows) + "\n\\bottomrule%")
stats = {"n_evidence": int((t["strategic_source"] == "evidence_assessment").sum()), "n_prior": int((t["strategic_source"] == "reference_prior").sum()),
         "n_missing": int((t["strategic_source"] == "missing").sum()), "scar_mean": float(ev["scarcity_strength"].mean()), "scar_sd": float(ev["scarcity_strength"].std()),
         "risk_mean": float(ev["risk_score"].mean()), "conf_mean": float(ev["strategic_confidence"].mean()), "n_gated": int(len(gated)), "n_capped": int(t["risk_capped"].fillna(False).astype(bool).sum()),
         "topfifteen": ", ".join(top.sort_values("rank").index), "corr_scar_alpha": float(ev["scarcity_strength"].corr(t.loc[ev.index, "composite"])) if len(ev) > 5 else float("nan")}
macros = "\n".join(f"\\newcommand{{\\strat{k.replace('_', '')}}}{{{(f'{v:.2f}' if isinstance(v, float) else esc(v))}}}" for k, v in stats.items())
(OUT / "strategic_stats.tex").write_text(macros + "\n")
print(json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in stats.items()}, indent=0)[:600])
