"""Data pipeline for the USDT-M perpetual futures panic-short backtest.

Stages:
  1. parse_daily   : read all downloaded 1d monthly zips -> per symbol-month
                     liquidity table (median daily quote volume, day count).
  2. select        : pick the tradeable universe (liquidity-ranked) and emit
                     the 1m download URL list.
  3. parse_minute  : read 1m zips -> one float32 .npy bundle per symbol on a
                     global minute grid (minutes since GRID_START, UTC).

All heavy data lives in SCRATCH; only small summary CSVs go into the repo.
"""

import io
import os
import sys
import zipfile
import numpy as np
import pandas as pd

SCRATCH = os.environ.get(
    "BT_SCRATCH",
    "/tmp/claude-0/-home-user-3d-photo-inpainting/adaf3827-6404-529f-9624-cce6c1e5b3cf/scratchpad",
)
REPO_RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

GRID_START = pd.Timestamp("2024-01-01 00:00:00", tz="UTC")
GRID_END = pd.Timestamp("2026-07-04 00:00:00", tz="UTC")  # exclusive
N_MIN = int((GRID_END - GRID_START).total_seconds() // 60)

KLINE_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "count", "taker_buy_volume",
    "taker_buy_quote_volume", "ignore",
]


def read_kline_zip(path):
    """Read one Binance kline zip -> DataFrame. Handles optional header row."""
    with zipfile.ZipFile(path) as z:
        name = z.namelist()[0]
        raw = z.read(name)
    first = raw.split(b"\n", 1)[0]
    skip = 1 if first.startswith(b"open_time") else 0
    df = pd.read_csv(io.BytesIO(raw), header=None, skiprows=skip, names=KLINE_COLS)
    return df


def parse_daily():
    d1dir = os.path.join(SCRATCH, "d1zips")
    rows = []
    files = sorted(os.listdir(d1dir))
    for i, f in enumerate(files):
        if not f.endswith(".zip"):
            continue
        # SYMBOL-1d-YYYY-MM.zip
        sym = f.split("-1d-")[0]
        month = f.split("-1d-")[1].replace(".zip", "")
        try:
            df = read_kline_zip(os.path.join(d1dir, f))
        except Exception as e:
            print("BAD ZIP", f, e)
            continue
        rows.append({
            "symbol": sym,
            "month": month,
            "days": len(df),
            "median_daily_qvol": float(df["quote_volume"].median()),
            "mean_daily_qvol": float(df["quote_volume"].mean()),
        })
        if i % 2000 == 0:
            print(f"{i}/{len(files)}", flush=True)
    liq = pd.DataFrame(rows)
    liq.to_csv(os.path.join(SCRATCH, "liquidity_by_month.csv"), index=False)
    liq.to_csv(os.path.join(REPO_RESULTS, "liquidity_by_month.csv"), index=False)
    print("symbol-months:", len(liq), "symbols:", liq.symbol.nunique())


def select(top_n=90):
    liq = pd.read_csv(os.path.join(SCRATCH, "liquidity_by_month.csv"))
    per_sym = liq.groupby("symbol").agg(
        months=("month", "nunique"),
        overall_median_qvol=("median_daily_qvol", "median"),
    ).reset_index()
    # exclude the trigger assets (downloaded separately) and stable/odd pairs
    excl = {"BTCUSDT", "ETHUSDT", "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "BTCSTUSDT",
            "BTCDOMUSDT", "DEFIUSDT"}
    cand = per_sym[~per_sym.symbol.isin(excl)]
    # require at least 6 months of life and never a sub-$1M median overall
    cand = cand[(cand.months >= 6) & (cand.overall_median_qvol >= 1e6)]
    universe = cand.sort_values("overall_median_qvol", ascending=False).head(top_n)
    syms = sorted(universe.symbol.tolist())
    with open(os.path.join(SCRATCH, "universe.txt"), "w") as f:
        f.write("\n".join(syms))
    universe.to_csv(os.path.join(REPO_RESULTS, "universe_selected.csv"), index=False)
    # 1m URL list: universe + trigger assets, monthly 2024-01..2026-06 + daily July 2026
    months = [f"{y}-{m:02d}" for y in (2024, 2025, 2026) for m in range(1, 13)
              if not (y == 2026 and m > 6)]
    days = ["2026-07-01", "2026-07-02", "2026-07-03"]
    dl = syms + ["BTCUSDT", "ETHUSDT"]
    base = "https://data.binance.vision/data/futures/um"
    with open(os.path.join(SCRATCH, "m1_urls.txt"), "w") as f:
        for s in dl:
            for mo in months:
                f.write(f"{base}/monthly/klines/{s}/1m/{s}-1m-{mo}.zip\n")
            for d in days:
                f.write(f"{base}/daily/klines/{s}/1m/{s}-1m-{d}.zip\n")
    print("universe:", len(syms), "symbols; url list written")


def parse_minute():
    m1dir = os.path.join(SCRATCH, "m1zips")
    outdir = os.path.join(SCRATCH, "m1npy")
    os.makedirs(outdir, exist_ok=True)
    syms = open(os.path.join(SCRATCH, "universe.txt")).read().split()
    syms += ["BTCUSDT", "ETHUSDT"]
    files = os.listdir(m1dir)
    by_sym = {}
    for f in files:
        if f.endswith(".zip"):
            by_sym.setdefault(f.split("-1m-")[0], []).append(f)
    for si, s in enumerate(syms):
        out = os.path.join(outdir, f"{s}.npz")
        if os.path.exists(out):
            continue
        high = np.full(N_MIN, np.nan, dtype=np.float32)
        low = np.full(N_MIN, np.nan, dtype=np.float32)
        close = np.full(N_MIN, np.nan, dtype=np.float32)
        for f in sorted(by_sym.get(s, [])):
            try:
                df = read_kline_zip(os.path.join(m1dir, f))
            except Exception as e:
                print("BAD ZIP", f, e)
                continue
            # open_time in ms; some 2025+ futures dumps use microseconds
            ts = df["open_time"].to_numpy(np.int64)
            ts = np.where(ts > 100_000_000_000_000, ts // 1000, ts)  # us -> ms
            idx = (ts - int(GRID_START.value // 1_000_000)) // 60_000
            ok = (idx >= 0) & (idx < N_MIN)
            idx = idx[ok].astype(np.int64)
            high[idx] = df["high"].to_numpy(np.float32)[ok]
            low[idx] = df["low"].to_numpy(np.float32)[ok]
            close[idx] = df["close"].to_numpy(np.float32)[ok]
        np.savez_compressed(out, high=high, low=low, close=close)
        print(f"{si+1}/{len(syms)} {s} bars={np.isfinite(close).sum()}", flush=True)


if __name__ == "__main__":
    {"parse_daily": parse_daily,
     "select": select,
     "parse_minute": parse_minute}[sys.argv[1]]()
