"""Statistical tests of predictive power for the point-in-time score."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import spearmanr, ttest_1samp

from .backtest import PILLARS_PIT, DEFAULT_WEIGHTS, PILLAR_SIGNALS, composite, score_cross_section, HORIZONS


def _ic_series(scored: pd.DataFrame, fr: pd.DataFrame, signal: str, horizon: str) -> pd.Series:
    frh = fr[fr["horizon"] == horizon].set_index("date")
    out = {}
    for d, cs in scored.groupby("date"):
        if d not in frh.index:
            continue
        cs = cs[cs["eligible"]].set_index("ticker")
        r = pd.to_numeric(frh.loc[d].reindex(cs.index), errors="coerce")
        x = cs[signal].astype(float)
        m = r.notna() & x.notna()
        if m.sum() >= 20:
            out[d] = spearmanr(x[m], r[m])[0]
    return pd.Series(out, name=signal)


def _hac_t(x: pd.Series, lags: int) -> float:
    """t-stat of the mean with Newey-West (HAC) standard errors for overlapping horizons."""
    x = x.dropna().values
    n = len(x)
    if n < 6:
        return np.nan
    e = x - x.mean()
    s2 = e @ e / n
    for L in range(1, min(lags, n - 1) + 1):
        w = 1 - L / (lags + 1)
        s2 += 2 * w * (e[L:] @ e[:-L]) / n
    se = np.sqrt(max(s2, 1e-12) / n)
    return float(x.mean() / se)


def ic_table(scored: pd.DataFrame, fr: pd.DataFrame, signals: list[str] | None = None) -> pd.DataFrame:
    signals = signals or PILLAR_SIGNALS
    rows = []
    for h, k in HORIZONS.items():
        for sig in signals:
            ic = _ic_series(scored, fr, sig, h)
            if ic.empty:
                continue
            rows.append({"horizon": h, "signal": sig, "mean_ic": ic.mean(), "median_ic": ic.median(), "std_ic": ic.std(),
                         "t_naive": ttest_1samp(ic, 0).statistic, "t_hac": _hac_t(ic, k - 1), "pct_positive": float((ic > 0).mean()),
                         "n_periods": int(len(ic)), "icir": ic.mean() / ic.std() if ic.std() else np.nan})
    return pd.DataFrame(rows)


def feature_ic(panel_scored: pd.DataFrame, panel_raw: pd.DataFrame, fr: pd.DataFrame, horizon: str = "3m") -> pd.DataFrame:
    """IC of every raw feature (signed as in the pillar definition)."""
    merged = panel_raw.merge(panel_scored[["date", "ticker", "eligible"]], on=["date", "ticker"])
    rows = []
    for pillar, feats in PILLARS_PIT.items():
        for f, sign in feats.items():
            if f not in merged.columns:
                continue
            tmp = merged[["date", "ticker", "eligible", f]].copy(); tmp[f] = tmp[f] * sign
            ic = _ic_series(tmp, fr, f, horizon)
            if len(ic):
                rows.append({"pillar": pillar, "feature": f, "mean_ic": ic.mean(), "t_hac": _hac_t(ic, HORIZONS[horizon] - 1), "n": int(len(ic))})
    return pd.DataFrame(rows).sort_values("mean_ic", ascending=False)


def ic_time_series(scored: pd.DataFrame, fr: pd.DataFrame, signal: str = "composite", horizon: str = "3m") -> pd.Series:
    return _ic_series(scored, fr, signal, horizon)


def permutation_test(scored: pd.DataFrame, fr: pd.DataFrame, horizon: str = "3m", n_perm: int = 300, seed: int = 0) -> dict:
    """Shuffle composite within each date; distribution of mean IC under no skill."""
    rng = np.random.default_rng(seed)
    actual = _ic_series(scored, fr, "composite", horizon).mean()
    frh = fr[fr["horizon"] == horizon].set_index("date")
    groups = []
    for d, cs in scored.groupby("date"):
        if d not in frh.index:
            continue
        cs = cs[cs["eligible"]].set_index("ticker")
        r = pd.to_numeric(frh.loc[d].reindex(cs.index), errors="coerce")
        m = r.notna() & cs["composite"].notna()
        if m.sum() >= 20:
            groups.append((cs.loc[m, "composite"].values, r[m].values))
    dist = []
    for _ in range(n_perm):
        ics = [spearmanr(rng.permutation(x), r)[0] for x, r in groups]
        dist.append(np.mean(ics))
    dist = np.array(dist)
    return {"actual_mean_ic": float(actual), "null_mean": float(dist.mean()), "null_sd": float(dist.std()),
            "p_value": float((dist >= actual).mean()), "n_perm": n_perm, "distribution": dist.tolist()}


def random_weights_test(scored: pd.DataFrame, fr: pd.DataFrame, horizon: str = "3m", n_draw: int = 300, seed: int = 1) -> dict:
    """Are the documented pillar weights better than random weightings of the same pillars?"""
    rng = np.random.default_rng(seed)
    pillars = list(DEFAULT_WEIGHTS)
    frh = fr[fr["horizon"] == horizon].set_index("date")
    groups = []
    for d, cs in scored.groupby("date"):
        if d not in frh.index:
            continue
        cs = cs[cs["eligible"]].set_index("ticker")
        r = pd.to_numeric(frh.loc[d].reindex(cs.index), errors="coerce")
        m = r.notna()
        if m.sum() >= 20:
            groups.append((cs.loc[m, [f"score_{p}" for p in pillars]].fillna(0).values, r[m].values))
    def mean_ic(w):
        return float(np.mean([spearmanr(X @ w, r)[0] for X, r in groups]))
    actual = mean_ic(np.array([DEFAULT_WEIGHTS[p] for p in pillars]))
    dist = np.array([mean_ic(rng.dirichlet(np.ones(len(pillars)))) for _ in range(n_draw)])
    single = {p: mean_ic(np.eye(len(pillars))[i]) for i, p in enumerate(pillars)}
    eq = mean_ic(np.full(len(pillars), 0.25))
    return {"actual_mean_ic": actual, "random_mean": float(dist.mean()), "random_sd": float(dist.std()),
            "percentile_of_actual": float((dist < actual).mean()), "equal_weight_ic": eq, "single_pillar_ic": single,
            "best_random": float(dist.max()), "n_draw": n_draw, "distribution": dist.tolist()}


def leave_one_out(scored: pd.DataFrame, fr: pd.DataFrame, horizon: str = "3m", tickers: list[str] | None = None) -> pd.DataFrame:
    base = _ic_series(scored, fr, "composite", horizon).mean()
    rows = []
    for t in tickers or []:
        ic = _ic_series(scored[scored["ticker"] != t], fr, "composite", horizon).mean()
        rows.append({"dropped": t, "mean_ic": ic, "delta_vs_base": ic - base})
    return pd.DataFrame(rows)


def regime_split(ic: pd.Series, regime: pd.Series) -> pd.DataFrame:
    """Mean IC by regime label (regime indexed by date)."""
    df = pd.concat([ic.rename("ic"), regime.reindex(ic.index).rename("regime")], axis=1).dropna()
    g = df.groupby("regime")["ic"]
    return pd.DataFrame({"mean_ic": g.mean(), "t_naive": g.apply(lambda s: ttest_1samp(s, 0).statistic if len(s) > 3 else np.nan), "n": g.size()})


def factor_regression(bt_monthly: pd.DataFrame, rf_monthly: float = 0.02 / 12) -> dict:
    """Alpha of top-15 minus universe vs SOXX and SPY excess returns (HAC t-stats)."""
    import statsmodels.api as sm
    y = (bt_monthly["top15"] - bt_monthly["universe_ew"]).dropna()
    out = {}
    for name, cols in (("vs_soxx", ["SOXX"]), ("vs_spy_soxx", ["SPY", "SOXX"])):
        X = bt_monthly.loc[y.index, cols] - rf_monthly
        X = sm.add_constant(X)
        m = sm.OLS(y, X, missing="drop").fit(cov_type="HAC", cov_kwds={"maxlags": 3})
        out[name] = {"alpha_monthly": float(m.params["const"]), "alpha_annual": float(m.params["const"] * 12),
                     "alpha_t": float(m.tvalues["const"]), "betas": {c: float(m.params[c]) for c in cols}, "r2": float(m.rsquared), "n": int(m.nobs)}
    # raw top-15 vs benchmarks
    y2 = (bt_monthly["top15"] - rf_monthly).dropna()
    X2 = sm.add_constant(bt_monthly.loc[y2.index, ["SPY", "SOXX"]] - rf_monthly)
    m2 = sm.OLS(y2, X2, missing="drop").fit(cov_type="HAC", cov_kwds={"maxlags": 3})
    out["top15_vs_spy_soxx"] = {"alpha_annual": float(m2.params["const"] * 12), "alpha_t": float(m2.tvalues["const"]),
                                "betas": {c: float(m2.params[c]) for c in ("SPY", "SOXX")}, "r2": float(m2.rsquared)}
    return out


def fama_french(bt_monthly: pd.DataFrame) -> dict | None:
    """Optional: alpha vs Fama-French 5 factors + momentum (downloads from Ken French's library)."""
    import io, zipfile, requests, statsmodels.api as sm
    base = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
    def load(name, skip):
        r = requests.get(base + name, timeout=30); r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content)); f = z.open(z.namelist()[0])
        txt = f.read().decode("latin1").splitlines()
        rows = [l for l in txt if l[:6].strip().isdigit() and len(l.split(",")[0].strip()) == 6]
        df = pd.read_csv(io.StringIO("\n".join(rows)), header=None)
        return df
    try:
        ff = load("F-F_Research_Data_5_Factors_2x3_CSV.zip", 3); ff.columns = ["ym", "MKT", "SMB", "HML", "RMW", "CMA", "RF"]
        mom = load("F-F_Momentum_Factor_CSV.zip", 13); mom.columns = ["ym", "MOM"]
    except Exception as e:
        return {"error": str(e)}
    ff["ym"] = ff["ym"].astype(str); mom["ym"] = mom["ym"].astype(str)
    fac = ff.merge(mom, on="ym"); fac = fac.set_index(pd.to_datetime(fac["ym"], format="%Y%m").dt.to_period("M")).drop(columns="ym") / 100
    # backtest rows are dated at portfolio formation; the return accrues over the *next* month
    y = bt_monthly["top15"].copy(); y.index = pd.DatetimeIndex(y.index).to_period("M") + 1
    df = pd.concat([y.rename("y"), fac], axis=1).dropna()
    if len(df) < 24:
        return {"error": "insufficient overlap"}
    X = sm.add_constant(df[["MKT", "SMB", "HML", "RMW", "CMA", "MOM"]])
    m = sm.OLS(df["y"] - df["RF"], X).fit(cov_type="HAC", cov_kwds={"maxlags": 3})
    return {"alpha_annual": float(m.params["const"] * 12), "alpha_t": float(m.tvalues["const"]),
            "betas": {c: round(float(m.params[c]), 3) for c in X.columns if c != "const"}, "r2": float(m.rsquared), "n": int(m.nobs)}
