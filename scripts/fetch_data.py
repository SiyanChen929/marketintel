"""Standalone data refresh (also called by the pipeline). Usage: python scripts/fetch_data.py [--force]"""
import sys, warnings, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")
from ai_semi_intel.utils.io import setup_logging, log
from ai_semi_intel.config.universe import tickers, BENCHMARKS
from ai_semi_intel.data import yahoo, edgar, fred

setup_logging()
force = "--force" in sys.argv
t0 = time.time()
tk = tickers()
px = yahoo.fetch_prices(tk + list(BENCHMARKS), tag="universe", force=force)
log.info("prices: %s .. %s, %d cols", px.index[0].date(), px.index[-1].date(), px.shape[1])
f = yahoo.fetch_all_fundamentals(tk, workers=4, force=force)
log.info("fundamentals ok: %d / %d", sum(1 for s in f.values() if s.get("ok")), len(tk))
e = edgar.fetch_all_edgar(tk)
log.info("edgar in: %d / %d", sum(1 for s in e.values() if s.get("in_edgar")), len(tk))
p = fred.macro_panel()
log.info("fred series: %d | done in %.0fs", len(p), time.time() - t0)
