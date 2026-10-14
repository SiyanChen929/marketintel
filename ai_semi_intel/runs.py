"""Run identity, data manifest and the append-only run history.

A *run* is one execution of the daily pipeline. Every run receives an immutable ``run_id``, a manifest
(``run.json``) recording what was used (data cut-off, config / code / data hashes, data health) and what was
produced (the canonical target weights, cash, alternatives), and its own directory under
``outputs/history/runs/<run_id>/``. Runs are never overwritten: a rerun inside the same session *supersedes*
the earlier run. Turnover is measured against the *approved prior run*, the last approved run of an earlier
session, never against a same-day rerun.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import fcntl
import tempfile
from io import StringIO
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .config.settings import CONFIG, DATA_CACHE, HISTORY, LATEST, OUTPUTS, PROJECT_ROOT, FRED_SERIES
from .utils.io import DataUnavailable, read_json, write_json

PORTFOLIO_ID = "ai_semi_top15_erc_vt25"
VALIDATION_STATUS = "NOT VALIDATED · exploratory"
RUN_FILES = ("run.json", "snapshot.json", "summary.json", "top15.csv", "portfolio_weights.csv", "portfolio_stats.csv", "kpi_table.csv",
             "universe_scores.csv", "backtest_stats.csv", "construction_backtest_cumulative.csv", "construction_backtest_drawdown.csv",
             "construction_backtest_stats.csv", "strategic_table.csv", "strategic_refresh.json", "strategic_queue.json",
             "macro_snapshot.csv", "construction_targets.json", "risk_contributions.csv", "asset_stats.csv",
             "correlation.csv", "backtest_cumulative.csv", "backtest_drawdown.csv", "frontier.csv", "segment_table.csv",
             "macro_panel.json", "prices_top15.csv", "data_manifest.json", "live_validation.json")
# freshness thresholds by series frequency: (max hours since fetch, max days since the last observation)
FRESHNESS = {"daily": (36.0, 6), "monthly": (36.0, 80), "quarterly": (36.0, 160)}   # observation dates lag releases by weeks (monthly) / months (quarterly)
FRED_FREQ = {"DGS10": "daily", "DGS2": "daily", "T10Y2Y": "daily", "VIXCLS": "daily", "BAMLH0A0HYM2": "daily", "DTWEXBGS": "daily",
             "FEDFUNDS": "monthly", "CPIAUCSL": "monthly", "PCEPILFE": "monthly", "UNRATE": "monthly", "INDPRO": "monthly", "IPG3344S": "monthly",
             "PCU33443344": "monthly", "AMTMNO": "monthly", "NEWORDER": "monthly", "GDPC1": "quarterly"}
FRED_UNIT = {"DGS10": "% p.a.", "DGS2": "% p.a.", "T10Y2Y": "pp", "FEDFUNDS": "% p.a.", "CPIAUCSL": "index", "PCEPILFE": "index", "UNRATE": "%",
             "GDPC1": "bn 2017 USD", "INDPRO": "index", "IPG3344S": "index", "PCU33443344": "index", "VIXCLS": "points", "BAMLH0A0HYM2": "pp",
             "DTWEXBGS": "index", "AMTMNO": "USD m", "NEWORDER": "USD m"}


# ----------------------------------------------------------------------------- hashes / ids
def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def code_hash(root: Path = PROJECT_ROOT) -> str:
    """Hash of every .py file of the two packages (path + content), so a run can be tied to the code that produced it."""
    h = hashlib.sha256()
    for pkg in ("ai_semi_intel", "market_intel"):
        for p in sorted((root / pkg).rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            h.update(str(p.relative_to(root)).encode()); h.update(_sha256_file(p).encode())
    return h.hexdigest()[:16]


def config_hash(cfg=CONFIG) -> str:
    return sha256_text(json.dumps(asdict(cfg), sort_keys=True, default=str))[:16]


def new_run_id(asof, generated_at: datetime) -> str:
    asof = pd.Timestamp(asof)
    tag = sha256_text(f"{generated_at.isoformat()}|{os.getpid()}")[:8]
    return f"{asof:%Y%m%d}T{generated_at:%H%M%S}-{tag}"


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None


def _mtime(p: Path) -> datetime | None:
    return datetime.fromtimestamp(p.stat().st_mtime).astimezone() if p.exists() else None


# ----------------------------------------------------------------------------- data manifest
def _status(fetched_at: datetime | None, effective_at: pd.Timestamp | None, freq: str, now: datetime) -> tuple[str, str | None]:
    if fetched_at is None:
        return "unavailable", "no cached data"
    max_fetch_h, max_obs_d = FRESHNESS.get(freq, FRESHNESS["daily"])
    age_h = (now - fetched_at.astimezone(timezone.utc)).total_seconds() / 3600
    if age_h > max_fetch_h:
        return "stale", f"fetched {age_h / 24:.1f} days ago"
    if effective_at is not None and (now.date() - effective_at.date()).days > max_obs_d:
        return "stale", f"last observation {effective_at.date()} is {(now.date() - effective_at.date()).days} days old"
    return "fresh", None


def data_manifest(px: pd.DataFrame | None, tickers: list[str], panel: dict | None = None, now: datetime | None = None,
                  data_cache: Path = DATA_CACHE) -> dict:
    """Field-level provenance for the inputs of a run: vendor, effective / fetched timestamps, unit, hash, status."""
    now = now or datetime.now(timezone.utc)
    entries: dict[str, dict] = {}
    gaps: dict[str, list[str]] = {}
    ppath = data_cache / "prices" / "prices_universe.parquet"
    if px is not None and not px.empty:
        fetched = _mtime(ppath)
        last = pd.Timestamp(px.index[-1])
        st, why = _status(fetched, last, "daily", now)
        recent = px.index[-10:]
        for t in tickers:
            if t in px.columns:
                miss = [str(d.date()) for d in recent if pd.isna(px.loc[d, t])]
                if miss:
                    gaps[t] = miss
        if gaps:
            st, why = "stale", f"missing recent prices for {', '.join(sorted(gaps))}"
        entries["prices"] = {"vendor": "Yahoo Finance", "field": "adjusted close", "path": str(ppath), "sha256": _sha256_file(ppath)[:16] if ppath.exists() else None,
                             "effective_at": str(last.date()), "released_at": str(last.date()), "fetched_at": _iso(fetched), "currency": "USD", "unit": "price",
                             "adjustment": "split & dividend adjusted", "finality": "final", "n_tickers": int(px.shape[1]), "status": st, "stale_reason": why}
    fred_dir = data_cache / "fred"
    fred_status = read_json(fred_dir / "_status.json", {}) or {}
    for sid in (panel or {}):
        if sid not in FRED_SERIES:
            continue                                   # derived YoY series carry their parent's provenance
        fp = fred_dir / f"{sid}.csv"
        s = panel[sid].dropna() if panel.get(sid) is not None else pd.Series(dtype=float)
        eff = pd.Timestamp(s.index[-1]) if len(s) else None
        fetched = _mtime(fp)
        freq = FRED_FREQ.get(sid, "monthly")
        st, why = _status(fetched, eff, freq, now)
        attrs = getattr(panel.get(sid), "attrs", {}) or {}
        fs = fred_status.get(sid) or {}
        recent_fail = fs and not fs.get("ok", True) and fs.get("at") and (now - datetime.fromisoformat(fs["at"]).astimezone(timezone.utc)).total_seconds() < 86400
        if attrs.get("status") in ("stale", "unavailable"):
            st, why = attrs["status"], attrs.get("stale_reason")
        elif recent_fail:
            st, why = ("stale" if fp.exists() else "unavailable"), f"last fetch failed: {fs.get('error')}"
        entries[f"fred:{sid}"] = {"vendor": "FRED", "field": FRED_SERIES[sid], "path": str(fp), "sha256": _sha256_file(fp)[:16] if fp.exists() else None,
                                  "effective_at": str(eff.date()) if eff is not None else None, "released_at": None, "fetched_at": _iso(fetched), "currency": None,
                                  "unit": FRED_UNIT.get(sid), "adjustment": "as published (may be revised)", "finality": "provisional" if freq != "daily" else "final",
                                  "frequency": freq, "status": st, "stale_reason": why}
    fxp = data_cache / "prices" / "fx.json"
    fx = read_json(fxp, {}) if fxp.exists() else {}
    fx_status = read_json(data_cache / "prices" / "fx_status.json", {}) or {}
    failed = sorted(k for k, v in fx_status.items() if not v.get("ok", True) and v.get("at") and (now - datetime.fromisoformat(v["at"]).astimezone(timezone.utc)).total_seconds() < 86400)
    fx_st = "fresh" if fx and _status(_mtime(fxp), None, "daily", now)[0] == "fresh" else ("unavailable" if not fx else "stale")
    if failed:
        fx_st = "unavailable"
    entries["fx"] = {"vendor": "Yahoo Finance", "field": "spot FX vs USD", "path": str(fxp), "fetched_at": _iso(_mtime(fxp)), "pairs": sorted(fx), "failed_pairs": failed,
                     "unit": "local per USD", "finality": "final", "status": fx_st,
                     "stale_reason": ("FX fetch failed for " + ", ".join(failed) + " (names marked fx_missing and excluded)") if failed else (None if fx else "no FX cache")}
    for name, sub in (("fundamentals", "fundamentals"), ("edgar", "edgar")):
        d = data_cache / sub
        files = list(d.glob("*")) if d.exists() else []
        newest = max((f.stat().st_mtime for f in files), default=None)
        fetched = datetime.fromtimestamp(newest).astimezone() if newest else None
        st, why = _status(fetched, None, "daily", now) if fetched else ("unavailable", "no cache")
        entries[name] = {"vendor": "Yahoo Finance" if name == "fundamentals" else "SEC EDGAR", "field": "company snapshot" if name == "fundamentals" else "XBRL companyfacts + submissions",
                         "path": str(d), "n_files": len(files), "fetched_at": _iso(fetched), "finality": "as filed", "status": st, "stale_reason": why}
    statuses = {v["status"] for v in entries.values()}
    overall = "unavailable" if "unavailable" in statuses else "stale" if "stale" in statuses else "fresh"
    body = {"generated_at": _iso(now), "entries": entries, "price_gaps": gaps, "status": overall,
            "stale": sorted(k for k, v in entries.items() if v["status"] != "fresh")}
    body["hash"] = sha256_text(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "fetched_at"} for k, v in entries.items()}, sort_keys=True, default=str))[:16]
    return body


def bind_price_finality(manifest: dict, finality: dict) -> dict:
    """Keep acquisition-time session evidence bound to every saved manifest."""
    manifest["price_finality"] = finality
    manifest["entries"]["prices"]["session_finality"] = dict(finality)
    if not finality["fresh_complete_session"]:
        manifest["entries"]["prices"].update(
            status="stale", stale_reason="latest completed exchange session is missing: " + finality["required_through"])
        manifest["status"] = "unavailable" if manifest["status"] == "unavailable" else "stale"
        manifest["stale"] = sorted(set(manifest["stale"]) | {"prices"})
    manifest["hash"] = sha256_text(json.dumps(
        {k: {kk: vv for kk, vv in v.items() if kk != "fetched_at"} for k, v in manifest["entries"].items()},
        sort_keys=True, default=str))[:16]
    return manifest


BLOCKING_INPUTS = ("prices", "fred:DGS10")          # without these there is no defensible output at all


def blocking_reasons(manifest: dict) -> list[str]:
    """Inputs whose absence blocks the run outright (stale inputs only make the run not actionable)."""
    out = []
    for k in BLOCKING_INPUTS:
        e = manifest.get("entries", {}).get(k)
        if e is None or e.get("status") == "unavailable":
            out.append(f"{k} unavailable: {(e or {}).get('stale_reason') or 'no data'}")
    return out


def write_blocked(reasons: list[str], stage: str, latest: Path = LATEST) -> Path:
    p = latest / "run_blocked.json"
    write_json({"at": datetime.now().astimezone().isoformat(timespec="seconds"), "stage": stage, "reasons": reasons,
                "message": "run stopped; the last completed run stays on display and is marked BLOCKED"}, p)
    return p


def clear_blocked(latest: Path = LATEST) -> None:
    p = latest / "run_blocked.json"
    if p.exists():
        p.unlink()


def validation_summary(outputs: Path = OUTPUTS, *, live: dict | None = None) -> dict:
    """The evidence state shown next to every recommendation: PIT IC, walk-forward, live forward archive."""
    v = read_json(outputs / "validation" / "summary.json", {}) or {}
    live = live if live is not None else (read_json(outputs / "latest" / "live_validation.json", {}) or {})
    ic = {(r.get("horizon"), r.get("signal")): r for r in v.get("ic", [])}
    def g(h, k):
        r = ic.get((h, "composite")) or {}
        return r.get(k)
    wf = v.get("walk_forward", {}) or {}
    return {"status": VALIDATION_STATUS, "pit_ic_3m": g("3m", "mean_ic"), "pit_t_3m": g("3m", "t_hac"), "pit_ic_12m": g("12m", "mean_ic"), "pit_t_12m": g("12m", "t_hac"),
            "wf_ic_3m": wf.get("oos_mean_ic"), "wf_t_3m": wf.get("oos_t_hac"), "permutation_p": (v.get("permutation") or {}).get("p_value"),
            "pit_generated_at": v.get("generated_at"), "live_snapshots": live.get("n_snapshots", 0), "live_forward": live.get("snapshots_with_forward", {}),
            "live_status": live.get("status"), "gates": {"pit_t_3m_ge_2": (g("3m", "t_hac") or 0) >= 2.0, "live_forward_12m_ge_12": (live.get("snapshots_with_forward", {}) or {}).get("12m", 0) >= 12,
                                                         "wf_t_3m_ge_2": (wf.get("oos_t_hac") or 0) >= 2.0}}


# ----------------------------------------------------------------------------- run manifest
def build_run(snapshot: dict, opt: dict, manifest: dict, validation: dict, changes: dict, config_h: str | None = None, code_h: str | None = None,
              data_cutoff: dict | None = None, returns_info: dict | None = None) -> dict:
    rec = opt["recommended"]
    base = rec.get("base", "risk_parity")
    positions = []
    for t in snapshot["top"]:
        positions.append({"ticker": t, "rank": int(snapshot["rank"][t]), "composite": float(snapshot["composite"][t]),
                          "target_model_weight": float(opt[base]["weights"].get(t, 0.0)), "target_executable_weight": float(rec["weights"].get(t, 0.0)),
                          "current_live_weight": None, "max_sharpe_weight": float(opt["max_sharpe"]["weights"].get(t, 0.0))})
    cash = float(rec.get("cash", 0.0))
    invested = float(sum(p["target_executable_weight"] for p in positions))
    reasons = []
    if manifest.get("status") == "unavailable":
        reasons.append("required input unavailable: " + ", ".join(manifest.get("stale", [])))
    if manifest.get("price_gaps"):
        reasons.append("recent price gaps for " + ", ".join(sorted(manifest["price_gaps"])))
    for key, label in (("prices", "prices"), ("fred:DGS10", "risk-free rate (DGS10)")):
        e = manifest.get("entries", {}).get(key) or {}
        if e.get("status") == "stale" and not (key == "prices" and manifest.get("price_gaps")):
            reasons.append(f"stale {label}: {e.get('stale_reason')}")
    daily = snapshot.get("rebalance_policy")
    if daily is not None and daily.get("ready") is not True:
        reasons.append("daily execution policy: " + str(daily.get("reason") or "not ready"))
    return {"run_id": snapshot["run_id"], "portfolio_id": snapshot.get("portfolio_id", PORTFOLIO_ID), "asof_session": snapshot["date"],
            "generated_at": snapshot["generated_at"], "supersedes": snapshot.get("supersedes"), "approved_prior_run_id": changes.get("prev_run_id"),
            "status": "pending", "data_cutoff": data_cutoff or {}, "config_hash": config_h or config_hash(), "code_hash": code_h or code_hash(),
            "data_manifest_hash": manifest.get("hash"), "validation_status": VALIDATION_STATUS, "validation": validation,
            "actionable": not reasons, "not_actionable_reasons": reasons,
            "rebalance_policy": daily, "price_finality": snapshot.get("price_finality"),
            "daily_attempt_id": snapshot.get("daily_attempt_id"),
            "data_health": {"status": manifest.get("status"), "stale": manifest.get("stale", []), "price_gaps": manifest.get("price_gaps", {}),
                            "entries": {k: {kk: v.get(kk) for kk in ("status", "effective_at", "fetched_at", "stale_reason", "unit", "vendor", "frequency", "finality")}
                                        for k, v in manifest.get("entries", {}).items()}},
            "portfolio": {"construction": base, "vol_target": float(CONFIG.portfolio.vol_target), "scale": float(rec.get("scale", 1.0)), "cash": cash, "invested": invested,
                          "reconciles": abs(invested + cash - 1.0) < 1e-6, "positions": positions, "wording": "target weights; no live position ledger"},
            "alternatives": {k: {t: float(v) for t, v in opt[k]["weights"].items()} for k in ("max_sharpe", "min_vol", "equal_weight") if k in opt},
            "returns_window": {k: returns_info.get(k) for k in ("start", "end", "common_observed_days", "dropped_missing_rows")} if returns_info else {},
            "changes": {k: changes.get(k) for k in ("prev_run_id", "basis", "turnover", "entries", "exits", "supersedes", "rerun_turnover")}}


# ----------------------------------------------------------------------------- append-only history
def runs_dir(history: Path = HISTORY) -> Path:
    return history / "runs"


def load_index(history: Path = HISTORY) -> list[dict]:
    return read_json(runs_dir(history) / "index.json", []) or []


def save_index(index: list[dict], history: Path = HISTORY) -> None:
    folder = runs_dir(history)
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=folder, prefix=".index-", delete=False) as stream:
        pending = Path(stream.name)
    try:
        write_json(sorted(index, key=lambda r: (r["asof_session"], r["generated_at"])), pending)
        with pending.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(pending, folder / "index.json")
    finally:
        pending.unlink(missing_ok=True)


def latest_same_session(index: list[dict], asof_session: str) -> dict | None:
    same = [r for r in index if r["asof_session"] == asof_session and r.get("status") != "superseded"]
    return max(same, key=lambda r: r["generated_at"]) if same else None


def approved_prior(index: list[dict], asof_session: str) -> dict | None:
    """Latest approved run of an earlier session. Pending runs of earlier sessions are auto-approved (last run of that session)."""
    earlier = [r for r in index if r["asof_session"] < asof_session and r.get("status") in ("approved", "pending")]
    if not earlier:
        return None
    return max(earlier, key=lambda r: (r["asof_session"], r["generated_at"]))


def load_run_snapshot(run_id: str, history: Path = HISTORY) -> dict | None:
    return read_json(runs_dir(history) / run_id / "snapshot.json")


def register(run: dict, latest: Path = LATEST, history: Path = HISTORY, extra_files: dict[str, Path] | None = None) -> Path:
    """Publish a complete immutable directory, then atomically replace its index.

    A failed copy remains unpublished. A crash after publication is recognizable
    by archive_manifest.json and can finish indexing without repeating research.
    """
    rid = run["run_id"]
    if not isinstance(rid, str) or not rid or Path(rid).name != rid or rid in (".", ".."):
        raise DataUnavailable("invalid run archive identity")
    folder = runs_dir(history)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ".register.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _register_locked(run, latest, history, extra_files)


def _register_locked(run: dict, latest: Path, history: Path, extra_files: dict[str, Path] | None) -> Path:
    rdir = runs_dir(history) / run["run_id"]
    if rdir.exists():
        raise DataUnavailable("immutable run archive already exists: " + run["run_id"])
    try:
        source_run = read_json(latest / "run.json")
    except (OSError, ValueError) as exc:
        raise DataUnavailable("source run manifest is unreadable") from exc
    if source_run != run:
        raise DataUnavailable("source run manifest does not match the registered run")
    pending = Path(tempfile.mkdtemp(dir=runs_dir(history), prefix=".pending-" + run["run_id"] + "-"))
    try:
        for f in RUN_FILES:
            p = latest / f
            if p.exists():
                shutil.copy(p, pending / f)
        for name, p in (extra_files or {}).items():
            if Path(name).name != name or name in (".", "..", "archive_manifest.json"):
                raise DataUnavailable("invalid additional run artifact name")
            if p.exists():
                shutil.copy(p, pending / name)
        if read_json(pending / "run.json") != run:
            raise DataUnavailable("run manifest changed while archiving")
        snapshot = read_json(pending / "snapshot.json")
        if snapshot is not None and (not isinstance(snapshot, dict) or any(snapshot.get(k) != run.get(k) for k in ("run_id", "generated_at", "asof_session"))):
            raise DataUnavailable("snapshot identity does not match its run")
        manifest = {"schema": "stock_select.run_archive.v1", "run_id": run["run_id"],
                    "complete": True, "files": {p.name: _sha256_file(p) for p in sorted(pending.iterdir()) if p.is_file()}}
        write_json(manifest, pending / "archive_manifest.json")
        for path in pending.iterdir():
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
        os.rename(pending, rdir)
    except BaseException:
        if pending.exists():
            shutil.rmtree(pending)
        raise
    _index_registered_run(run, history)
    return rdir


def _index_registered_run(run: dict, history: Path):
    index = load_index(history)
    if any(r["run_id"] == run["run_id"] for r in index):
        return
    session = run["asof_session"]
    newer = [r for r in index if r["asof_session"] == session and r["generated_at"] > run["generated_at"]]
    superseded_by = max(newer, key=lambda r: r["generated_at"])["run_id"] if newer else None
    for r in index:
        if not superseded_by and r["asof_session"] == session and r.get("status") in ("pending", "approved"):
            r["status"], r["superseded_by"] = "superseded", run["run_id"]
        elif r["asof_session"] < session and r.get("status") == "pending":
            last = max((x for x in index if x["asof_session"] == r["asof_session"] and x.get("status") != "superseded"), key=lambda x: x["generated_at"])
            r["status"] = "approved" if last is r else "superseded"
    index.append({"run_id": run["run_id"], "asof_session": session, "generated_at": run["generated_at"],
                  "daily_attempt_id": run.get("daily_attempt_id"),
                  "status": "superseded" if superseded_by else "pending", "superseded_by": superseded_by, "supersedes": run.get("supersedes"),
                  "approved_prior_run_id": run.get("approved_prior_run_id"), "actionable": run.get("actionable"), "turnover": (run.get("changes") or {}).get("turnover"),
                  "code_hash": run.get("code_hash"), "config_hash": run.get("config_hash"), "data_manifest_hash": run.get("data_manifest_hash"), "note": run.get("note")})
    save_index(index, history)


def inspect_completed_archive(folder: Path) -> dict | None:
    """Read a newly committed directory only when every manifest byte matches."""
    manifest = read_json(folder / "archive_manifest.json")
    if not isinstance(manifest, dict) or manifest.get("schema") != "stock_select.run_archive.v1" or manifest.get("complete") is not True:
        return None
    if manifest.get("run_id") != folder.name or not isinstance(manifest.get("files"), dict) or "run.json" not in manifest["files"]:
        raise DataUnavailable("completed archive identity is inconsistent")
    for name, checksum in manifest["files"].items():
        if Path(name).name != name or not (folder / name).is_file() or _sha256_file(folder / name) != checksum:
            raise DataUnavailable("completed archive file integrity mismatch")
    run = read_json(folder / "run.json")
    if not isinstance(run, dict) or run.get("run_id") != folder.name:
        raise DataUnavailable("completed archive run identity mismatch")
    return run


def recover_registration(run_id: str, history: Path = HISTORY) -> dict:
    """Complete only the index commit of a verified, fully published directory."""
    if Path(run_id).name != run_id or run_id in (".", ".."):
        raise DataUnavailable("invalid recovery run identity")
    folder = runs_dir(history)
    with (folder / ".register.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        run = inspect_completed_archive(folder / run_id)
        if run is None:
            raise DataUnavailable("run has no completed archive manifest")
        _index_registered_run(run, history)
        return run


def approve(run_id: str, history: Path = HISTORY, note: str | None = None) -> dict:
    index = load_index(history)
    hit = [r for r in index if r["run_id"] == run_id]
    if not hit:
        raise KeyError(run_id)
    for r in index:                                   # the approved run is the session's canonical run: every other run of that session is superseded
        if r["asof_session"] == hit[0]["asof_session"] and r["run_id"] != run_id and r.get("status") in ("approved", "pending"):
            r["status"] = "superseded"; r["superseded_by"] = run_id
    hit[0]["status"] = "approved"; hit[0]["approved_at"] = _iso(datetime.now().astimezone())
    if note:
        hit[0]["note"] = note
    save_index(index, history)
    return hit[0]


def append_rows(path: Path, rows: pd.DataFrame, run_id: str) -> None:
    """Append an immutable run batch; identical recovery writes are idempotent."""
    rows = rows.copy()
    rows.insert(0, "run_id", run_id) if "run_id" not in rows.columns else None
    if not rows["run_id"].eq(run_id).all():
        raise DataUnavailable("row batch contains a different run identity")
    if path.exists():
        old = pd.read_csv(path)
        if "run_id" not in old.columns:
            old.insert(0, "run_id", "legacy")
        existing = old.loc[old["run_id"] == run_id]
        if not existing.empty:
            # Match the on-disk CSV representation, including harmless numeric
            # dtype promotion when later runs add nullable columns.
            proposed = pd.read_csv(StringIO(rows.to_csv(index=False)))
            extra = set(existing.columns) - set(proposed.columns)
            if (not set(proposed.columns).issubset(existing.columns)
                    or any(existing[name].notna().any() for name in extra)):
                raise DataUnavailable("immutable row batch conflict: " + run_id)
            try:
                pd.testing.assert_frame_equal(existing[list(proposed.columns)].reset_index(drop=True),
                                              proposed.reset_index(drop=True), check_dtype=False, check_exact=True)
            except AssertionError as exc:
                raise DataUnavailable("immutable row batch conflict: " + run_id) from exc
            return
        rows = pd.concat([old, rows], ignore_index=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".rows-", delete=False) as stream:
        pending = Path(stream.name)
    try:
        rows.to_csv(pending, index=False)
        with pending.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def latest_per_date(df: pd.DataFrame, index: list[dict] | None = None) -> pd.DataFrame:
    """One run per date for time-series consumers: the approved run when known, otherwise the last run_id of the date."""
    if df.empty or "date" not in df.columns:
        return df
    if "run_id" not in df.columns:
        return df
    status = {r["run_id"]: r for r in (index or [])}
    keep = pd.Series(False, index=df.index)
    for d, g in df.groupby("date"):
        ids = list(dict.fromkeys(g["run_id"]))
        approved = [i for i in ids if status.get(i, {}).get("status") == "approved"]
        chosen = approved[-1] if approved else ids[-1]
        # A migrated legacy run_id can occur on several dates. Selecting it
        # for one date must not pull its superseded rows into another date.
        keep |= (df["date"] == d) & (df["run_id"] == chosen)
    return df[keep]


# ----------------------------------------------------------------------------- CLI
def _cli(argv=None) -> int:
    """python -m ai_semi_intel.runs list | approve <run_id> [--note TEXT] | show <run_id>"""
    import argparse
    ap = argparse.ArgumentParser(description="Run registry: list, approve or show runs")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list registered runs")
    a = sub.add_parser("approve", help="mark a run as the approved run of its session (supersedes the previously approved one)")
    a.add_argument("run_id"); a.add_argument("--note", default=None)
    s = sub.add_parser("show", help="print a run manifest"); s.add_argument("run_id")
    args = ap.parse_args(argv)
    hist = HISTORY                                   # resolved at call time (tests redirect it)
    if args.cmd == "list":
        for r in load_index(hist):
            print(f"{r['run_id']:34s} {r['asof_session']} {r.get('status', '?'):10s} prior={r.get('approved_prior_run_id') or '-':34s} turnover={r.get('turnover')} actionable={r.get('actionable')}")
        return 0
    if args.cmd == "approve":
        r = approve(args.run_id, hist, note=args.note)
        print(f"approved {r['run_id']} (session {r['asof_session']})")
        return 0
    if args.cmd == "show":
        print(json.dumps(read_json(runs_dir(hist) / args.run_id / "run.json"), indent=1, default=str))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
