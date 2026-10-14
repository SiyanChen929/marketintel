"""Nested Optuna/TPE research arm for portfolio-construction hyperparameters.

The tuner receives only net return observations strictly before an outer cutoff. It scores a sampled
configuration on chronological inner validation years, then the chosen configuration is replayed on the
outer year through :func:`portfolio_sweep.replay_policy`. The terminal holdout uses one configuration
chosen before the holdout and frozen for the entire holdout.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from . import portfolio_sweep as PS

PARAMETERS = ("method", "max_weight", "lookback", "n_hold", "hysteresis", "vol_target")


def _python_value(value: Any) -> str | int | float:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, np.integer)):
        return int(value)
    return float(value)


def parameter_levels(cfg: pd.DataFrame) -> dict[str, list[str | int | float]]:
    """Categorical levels in stable order; these exactly describe the precomputed finite grid."""
    out: dict[str, list[str | int | float]] = {}
    for name in PARAMETERS:
        vals = [_python_value(x) for x in cfg[name].drop_duplicates().tolist()]
        out[name] = sorted(vals)
    return out


def params_to_config(cfg: pd.DataFrame, params: dict[str, Any]) -> int:
    mask = pd.Series(True, index=cfg.index)
    for name in PARAMETERS:
        if name == "method":
            mask &= cfg[name].astype(str).eq(str(params[name]))
        else:
            mask &= np.isclose(pd.to_numeric(cfg[name]), float(params[name]))
    ids = list(cfg.index[mask])
    if len(ids) != 1:
        raise ValueError(f"TPE parameters map to {len(ids)} configs: {params}")
    return int(ids[0])


def config_params(cfg: pd.DataFrame, config_id: int) -> dict[str, str | int | float]:
    return {name: _python_value(cfg.loc[config_id, name]) for name in PARAMETERS}


def inner_validation_years(R: pd.DataFrame, cutoff: str | pd.Timestamp, first_year: int = 2016,
                           min_months: int = 12) -> list[int]:
    """Complete calendar validation years strictly before an outer cutoff."""
    cutoff = pd.Timestamp(cutoff)
    train = R[R.index < cutoff]
    counts = train.groupby(train.index.year).size()
    return [int(y) for y, n in counts.items() if y >= first_year and y < cutoff.year and n >= min_months]


def fold_score(R: pd.DataFrame, config_id: int, years: list[int]) -> tuple[float, list[float]]:
    """Mean chronological inner-fold net Sharpe; no outer observation enters this function."""
    values = [PS.stats(R.loc[R.index.year == y, config_id])["sharpe"] for y in years]
    finite = [float(x) for x in values if np.isfinite(x)]
    return (float(np.mean(finite)) if finite else -1e9), [float(x) for x in values]


def tune_fold(R: pd.DataFrame, cfg: pd.DataFrame, cutoff: str | pd.Timestamp, n_trials: int = 80,
              seed: int = 20260903, inner_first_year: int = 2016, min_inner_folds: int = 3,
              baseline_config: int | None = None) -> dict:
    """Run one seeded TPE study using only rows before ``cutoff``."""
    try:
        import optuna
    except ImportError as exc:  # pragma: no cover - exercised by the CLI environment check
        raise RuntimeError("Optuna is required: install requirements.txt") from exc

    cutoff = pd.Timestamp(cutoff)
    train = R[R.index < cutoff]
    years = inner_validation_years(train, cutoff, first_year=inner_first_year)
    if len(years) < min_inner_folds:
        raise ValueError(f"need {min_inner_folds} complete inner folds before {cutoff.date()}, found {years}")
    levels = parameter_levels(cfg)
    sampler = optuna.samplers.TPESampler(seed=seed, n_startup_trials=min(20, max(5, n_trials // 4)))
    study = optuna.create_study(direction="maximize", sampler=sampler)
    if baseline_config is not None:
        study.enqueue_trial(config_params(cfg, baseline_config))

    def objective(trial) -> float:
        params = {name: trial.suggest_categorical(name, levels[name]) for name in PARAMETERS}
        cid = params_to_config(cfg, params)
        score, scores = fold_score(train, cid, years)
        trial.set_user_attr("config_id", cid)
        trial.set_user_attr("inner_years", years)
        trial.set_user_attr("inner_fold_sharpes", scores)
        trial.set_user_attr("max_training_date", str(train.index.max().date()))
        return score

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study.optimize(objective, n_trials=n_trials, n_jobs=1, show_progress_bar=False)
    best = study.best_trial
    return {"study": study, "best_config": int(best.user_attrs["config_id"]),
            "best_score": float(best.value), "best_params": dict(best.params),
            "inner_years": years, "training_max_date": str(train.index.max().date()),
            "unique_configs": int(len({t.user_attrs.get("config_id") for t in study.trials}))}


def newey_west_mean_t(diff: pd.Series, max_lag: int = 3) -> float:
    """HAC t statistic for the paired monthly net-return difference."""
    x = pd.Series(diff).dropna().to_numpy(dtype=float)
    n = len(x)
    if n < 3:
        return np.nan
    u = x - x.mean()
    long_var = float(u @ u / n)
    for lag in range(1, min(max_lag, n - 1) + 1):
        gamma = float(u[lag:] @ u[:-lag] / n)
        long_var += 2 * (1 - lag / (max_lag + 1)) * gamma
    se = np.sqrt(max(long_var, 0.0) / n)
    return float(x.mean() / se) if se > 0 else np.nan


def block_bootstrap_sharpe_diff(a: pd.Series, b: pd.Series, n_boot: int = 2000,
                                block: int = 6, seed: int = 20260903) -> dict:
    """Paired circular moving-block bootstrap of the net-Sharpe difference."""
    both = pd.concat([a.rename("a"), b.rename("b")], axis=1).dropna()
    n = len(both)
    if n < 12:
        return {"sharpe_diff": np.nan, "ci_lo": np.nan, "ci_hi": np.nan, "p_gt_zero": np.nan,
                "n_months": n}
    arr = both.to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    draws = np.empty(n_boot)
    n_blocks = int(np.ceil(n / block))
    for i in range(n_boot):
        starts = rng.integers(0, n, n_blocks)
        idx = np.concatenate([(np.arange(s, s + block) % n) for s in starts])[:n]
        sample = arr[idx]
        draws[i] = PS.stats(pd.Series(sample[:, 0]))["sharpe"] - PS.stats(pd.Series(sample[:, 1]))["sharpe"]
    observed = PS.stats(both["a"])["sharpe"] - PS.stats(both["b"])["sharpe"]
    return {"sharpe_diff": float(observed), "ci_lo": float(np.nanpercentile(draws, 2.5)),
            "ci_hi": float(np.nanpercentile(draws, 97.5)), "p_gt_zero": float(np.nanmean(draws > 0)),
            "n_months": int(n)}


def replay_stats(replay: dict) -> dict:
    monthly = replay["monthly"]
    if monthly.empty:
        return {}
    net = PS.stats(monthly["net"])
    gross = PS.stats(monthly["gross"])
    return {**{f"net_{k}": v for k, v in net.items()},
            **{f"gross_{k}": v for k, v in gross.items()},
            "avg_turnover": float(monthly["turnover"].mean()),
            "total_cost_return": float(monthly["cost"].sum()),
            "n_switches": int((monthly["config"] != monthly["config"].shift()).sum() - 1)}


def compare_replays(tpe: dict, erc: dict, seed: int = 20260903) -> dict:
    a = tpe["monthly"]["net"]
    b = erc["monthly"]["net"]
    paired = pd.concat([a.rename("tpe"), b.rename("erc")], axis=1).dropna()
    boot = block_bootstrap_sharpe_diff(paired["tpe"], paired["erc"], seed=seed)
    return {"tpe": replay_stats(tpe), "fixed_erc": replay_stats(erc),
            "paired": {"mean_diff_annualized": float((paired["tpe"] - paired["erc"]).mean() * 12),
                       "newey_west_t": newey_west_mean_t(paired["tpe"] - paired["erc"]), **boot}}
