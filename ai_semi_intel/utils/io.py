"""Small IO helpers: JSON/parquet caching with age checks, logging."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger("ai_semi_intel")


class DataUnavailable(RuntimeError):
    """A required input is missing and no defensible fallback exists: the run must stop, not guess."""


def record_status(path: Path, key: str, **fields: Any) -> None:
    """Append-in-place fetch outcome for one input (read by ai_semi_intel.runs.data_manifest)."""
    st = read_json(path, {}) or {}
    st[key] = {**fields, "at": datetime.now().astimezone().isoformat(timespec="seconds")}
    write_json(st, path)


def setup_logging(level: int = logging.INFO) -> None:
    if not log.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))
        log.addHandler(h)
    log.setLevel(level)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def file_age_hours(p: Path) -> float:
    if not p.exists():
        return float("inf")
    return (time.time() - p.stat().st_mtime) / 3600.0


class NpEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return None if np.isnan(o) else float(o)
        if isinstance(o, (np.bool_,)):
            return bool(o)
        if isinstance(o, (pd.Timestamp, datetime)):
            return o.isoformat()
        if isinstance(o, (np.ndarray,)):
            return o.tolist()
        if isinstance(o, pd.DataFrame):
            return o.to_dict(orient="records")
        if isinstance(o, pd.Series):
            return o.to_dict()
        if o is pd.NaT:
            return None
        return super().default(o)


def write_json(obj: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, cls=NpEncoder, indent=2, default=str)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with open(path) as f:
        return json.load(f)


def write_frame(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".parquet":
        df.to_parquet(path)
    else:
        df.to_csv(path)


def read_frame(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path, index_col=0, parse_dates=True)


def safe_div(a, b):
    try:
        if a is None or b is None or b == 0 or pd.isna(a) or pd.isna(b):
            return np.nan
        return a / b
    except Exception:
        return np.nan
