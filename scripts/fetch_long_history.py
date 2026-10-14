"""Long price history (max available) + splits for the point-in-time backtest. Cached in data_cache/prices/."""
import sys, warnings, time, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")
import pandas as pd, yfinance as yf
from ai_semi_intel.config.universe import tickers
from ai_semi_intel.config.settings import DATA_CACHE
out = DATA_CACHE / "prices"
tk = tickers() + ["SPY", "QQQ", "SOXX"]
frames = []
for i in range(0, len(tk), 30):
    chunk = tk[i:i+30]
    raw = yf.download(chunk, period="max", auto_adjust=False, progress=False, threads=True, group_by="column")
    for col in ("Close", "Adj Close", "Volume"):
        sub = raw[col] if isinstance(raw.columns, pd.MultiIndex) else raw[[col]].rename(columns={col: chunk[0]})
        sub.columns = pd.MultiIndex.from_product([[col], sub.columns])
        frames.append(sub)
    time.sleep(1)
df = pd.concat(frames, axis=1).sort_index(axis=1)
df.index = pd.to_datetime(df.index).tz_localize(None)
df = df.loc[df.index >= "2005-01-01"]
for col in ("Close", "Adj Close", "Volume"):
    d = df[col]; d = d.loc[:, ~d.columns.duplicated()]
    d.to_parquet(out / f"long_{col.replace(' ', '_').lower()}.parquet")
    print(col, d.shape, d.index[0].date(), d.index[-1].date())
splits = {}
for t in tk:
    try:
        s = yf.Ticker(t).splits
        splits[t] = {str(k.date()): float(v) for k, v in s.items()} if s is not None and len(s) else {}
    except Exception as e:
        splits[t] = {}
    time.sleep(0.2)
json.dump(splits, open(out / "splits.json", "w"), indent=1)
print("splits saved:", sum(1 for v in splits.values() if v), "tickers with splits")
