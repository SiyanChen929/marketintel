"""Offline construction demonstration, with fictional assets and covariance."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from ai_semi_intel.portfolio.constructions import Ctx, build
from ai_semi_intel.config.settings import PortfolioConfig

rng = np.random.default_rng(7)
names = [f"ZZZ{i:02d}" for i in range(10)]
x = rng.normal(size=(400,10)) * np.linspace(.01,.025,10)
returns = pd.DataFrame(x, columns=names)
cov = returns.cov() * 252
ctx = Ctx(cov=cov, cfg=PortfolioConfig(min_weight=0, max_weight=.3, max_segment_weight=1))
print("SYNTHETIC SOFTWARE DEMO — NOT EMPIRICAL PERFORMANCE")
for method in ("equal_weight", "inverse_vol", "min_variance", "risk_parity"):
    weights = build(method, ctx)
    print(method, weights.round(5).to_dict(), "sum=", float(weights.sum()))
