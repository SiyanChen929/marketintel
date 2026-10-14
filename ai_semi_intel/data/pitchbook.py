"""PitchBook adapter.

PitchBook data is licensed; there is no public API key in this project. Two ways in:

1. **Exports (recommended, works today):** export from the PitchBook platform
   (Companies & Deals -> Export) and drop CSV/XLSX files into ``data_cache/pitchbook/``.
   Recognised file types (matched on filename, case-insensitive):
     - ``*company*``   : private-company comps (name, ticker, last valuation, revenue, growth)
     - ``*deal*``      : VC/PE/M&A deals in AI infrastructure & semis (deal size, date, stage)
     - ``*comps*``     : public comps sheets with PitchBook estimates
2. **API:** PitchBook offers a licensed Data Feed / API to enterprise customers. Set
   ``PITCHBOOK_API_KEY`` and ``PITCHBOOK_API_BASE`` and implement ``PitchBookAPI.fetch``
   against your entitlement (endpoint schemas vary by contract, so it is left as a
   thin, documented hook rather than guessed).

Everything downstream treats PitchBook as *optional enrichment*: the pipeline runs
without it and the deck/dashboard state clearly whether it was available.
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from ..config.settings import DATA_CACHE
from ..utils.io import log

PB_DIR = DATA_CACHE / "pitchbook"
PB_DIR.mkdir(parents=True, exist_ok=True)


def _read_any(p: Path) -> pd.DataFrame:
    if p.suffix.lower() in (".xlsx", ".xls"):
        return pd.read_excel(p)
    return pd.read_csv(p)


def load_exports() -> dict[str, pd.DataFrame]:
    """Load whatever PitchBook exports exist. Returns {kind: DataFrame}."""
    out: dict[str, pd.DataFrame] = {}
    for p in sorted(PB_DIR.glob("*")):
        if p.suffix.lower() not in (".csv", ".xlsx", ".xls"):
            continue
        name = p.name.lower()
        kind = "companies" if "compan" in name else "deals" if "deal" in name else "comps" if "comp" in name else "other"
        try:
            df = _read_any(p)
            df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
            out[kind] = pd.concat([out[kind], df]) if kind in out else df
            log.info("PitchBook export loaded: %s (%d rows) as %s", p.name, len(df), kind)
        except Exception as e:                    # pragma: no cover
            log.warning("PitchBook file %s unreadable: %s", p.name, e)
    return out


class PitchBookAPI:
    """Hook for the licensed PitchBook API. Fill in ``fetch`` per your contract."""

    def __init__(self) -> None:
        self.key = os.environ.get("PITCHBOOK_API_KEY", "")
        self.base = os.environ.get("PITCHBOOK_API_BASE", "https://api.pitchbook.com")

    @property
    def configured(self) -> bool:
        return bool(self.key)

    def fetch(self, endpoint: str, params: dict | None = None) -> pd.DataFrame:  # pragma: no cover
        if not self.configured:
            raise RuntimeError("PITCHBOOK_API_KEY not set")
        import requests
        r = requests.get(f"{self.base}/{endpoint.lstrip('/')}", params=params,
                         headers={"Authorization": f"PB-Token {self.key}"}, timeout=60)
        r.raise_for_status()
        js = r.json()
        return pd.json_normalize(js if isinstance(js, list) else js.get("items", js))


def pitchbook_context(universe_tickers: list[str]) -> dict:
    """Summarise private-market context for the deck/dashboard.

    Returns a dict with ``available`` flag, a status string and any per-ticker enrichment
    (e.g. PitchBook revenue estimates or M&A activity) keyed by ticker.
    """
    exports = load_exports()
    api = PitchBookAPI()
    ctx = {"available": bool(exports) or api.configured, "api_configured": api.configured,
           "exports": {k: len(v) for k, v in exports.items()}, "per_ticker": {}, "deals": None,
           "status": ""}
    if not ctx["available"]:
        ctx["status"] = ("PitchBook not connected: drop platform exports into data_cache/pitchbook/ "
                         "or set PITCHBOOK_API_KEY. Analysis runs on SEC EDGAR + Yahoo + FRED.")
        return ctx
    ctx["status"] = f"PitchBook exports loaded: {ctx['exports']}"
    comps = exports.get("comps", exports.get("companies"))
    if comps is not None:
        tcol = next((c for c in comps.columns if c in ("ticker", "symbol", "primary_ticker")), None)
        if tcol:
            comps[tcol] = comps[tcol].astype(str).str.upper().str.replace(r"^(NAS|NYS|NASDAQ|NYSE):", "", regex=True)
            sub = comps[comps[tcol].isin(universe_tickers)]
            ctx["per_ticker"] = sub.set_index(tcol).to_dict("index")
    deals = exports.get("deals")
    if deals is not None:
        ctx["deals"] = deals.head(500).to_dict("records")
    return ctx
