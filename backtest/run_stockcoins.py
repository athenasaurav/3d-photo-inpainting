"""Replication attempt: the frozen panic-short strategy on Binance's REAL
stock-tokens (spot), for the entire life of those instruments.

Reality check performed first (2026-07-04): Binance spot lists exactly 14
stock-tokens; the earliest (TSLAB/NVDAB/CRCLB/SNDKB) first traded
2026-06-11 18:00 UTC. A 5-month backtest of "104 Binance stock-coins"
cannot be reproduced from Binance's public data because the instruments
did not exist before that date.

This run: 14 tokens, 2026-06-11 .. 2026-07-03, ETH and BTC spot triggers,
frozen stock-coin rules (0.7%/5m alarm, sleeper 0.2%, 0.75/0.25/0.75
ladder), fees per the original description: maker 0.00%, taker 0.04% +
0.01% slippage padding on panic exits.
"""

import io
import os
import sys
import json
import zipfile
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine import simulate_coin, make_trigger, X_STOP
import data_pipeline as dp

SCRATCH = dp.SCRATCH
RESULTS = dp.REPO_RESULTS
ZDIR = os.path.join(SCRATCH, "stockzips")
GRID_START = dp.GRID_START
N_MIN = dp.N_MIN

TOKENS = ["AMDB", "CRCLB", "EWYB", "INTCB", "LITEB", "METAB", "MSFTB",
          "MSTRB", "NVDAB", "PLTRB", "QQQB", "SNDKB", "SPCXB", "TSLAB"]

MAKER_FEE = 0.0000   # zero-fee patient orders on stock-coins
TAKER_FEE = 0.0004   # "4 per 10,000" panic-exit toll from the description
SLIP = 0.0001

CFG = dict(sleeper=0.002, window=5, drop=0.007, entry_ttl=60,
           tp=0.0075, warn=0.0025, sl=0.0075, max_hold=1440, cooldown=30)


def load_symbol(sym):
    high = np.full(N_MIN, np.nan, dtype=np.float32)
    low = np.full(N_MIN, np.nan, dtype=np.float32)
    close = np.full(N_MIN, np.nan, dtype=np.float32)
    qvol = 0.0
    files = sorted(f for f in os.listdir(ZDIR) if f.startswith(sym + "-1m-"))
    for f in files:
        df = dp.read_kline_zip(os.path.join(ZDIR, f))
        ts = df["open_time"].to_numpy(np.int64)
        ts = np.where(ts > 100_000_000_000_000, ts // 1000, ts)  # us -> ms
        idx = (ts - int(GRID_START.value // 1_000_000)) // 60_000
        ok = (idx >= 0) & (idx < N_MIN)
        idx = idx[ok]
        high[idx] = df["high"].to_numpy(np.float32)[ok]
        low[idx] = df["low"].to_numpy(np.float32)[ok]
        close[idx] = df["close"].to_numpy(np.float32)[ok]
        qvol += float(df["quote_volume"].sum())
    return high, low, close, qvol


def main():
    t0 = int((pd.Timestamp("2026-06-11", tz="UTC") - GRID_START).total_seconds() // 60)
    t1 = int((pd.Timestamp("2026-07-04", tz="UTC") - GRID_START).total_seconds() // 60)
    days = (t1 - t0) / 1440.0

    out = {}
    pertok_all = {}
    for trig_sym in ["ETHUSDT", "BTCUSDT"]:
        th, tl, tc, _ = load_symbol(trig_sym)
        trig = make_trigger(tc, CFG["window"], CFG["drop"])
        trig[:t0] = False
        trig[t1:] = False
        rows = []
        pertok = {}
        for tok in TOKENS:
            high, low, close, qvol = load_symbol(tok + "USDT")
            tradeable = np.isfinite(close)
            te, tx, pnl, code = simulate_coin(
                high, low, close, trig, tradeable,
                CFG["sleeper"], CFG["window"], CFG["entry_ttl"],
                CFG["tp"], CFG["warn"], CFG["sl"],
                CFG["max_hold"], CFG["cooldown"],
                MAKER_FEE, TAKER_FEE, SLIP)
            for i in range(len(pnl)):
                rows.append((tok, int(te[i]), int(tx[i]), float(pnl[i]), int(code[i])))
            pertok[tok] = dict(n=len(pnl), pnl=round(float(pnl.sum()), 2),
                               avg_daily_qvol_musd=round(qvol / days / 1e6, 2))
        tr = pd.DataFrame(rows, columns=["symbol", "t_entry", "t_exit", "pnl", "code"])
        s = dict(
            n=len(tr),
            total=round(float(tr.pnl.sum()), 2) if len(tr) else 0.0,
            per_trade=round(float(tr.pnl.mean()), 3) if len(tr) else 0.0,
            winrate=round(float((tr.pnl > 0).mean()), 4) if len(tr) else 0.0,
            stop_rate=round(float((tr.code == X_STOP).mean()), 4) if len(tr) else 0.0,
            per_day=round(float(tr.pnl.sum()) / days, 2) if len(tr) else 0.0,
        )
        out[trig_sym] = s
        pertok_all[trig_sym] = pertok
        tr.to_parquet(os.path.join(SCRATCH, f"trades_stockcoins_{trig_sym[:3]}.parquet"))
        print(trig_sym, "engine:", json.dumps(s))

    json.dump(dict(window=f"2026-06-11..2026-07-03 ({days:.0f} days)",
                   engines=out, per_token=pertok_all),
              open(os.path.join(RESULTS, "stockcoin_replication.json"), "w"), indent=2)


if __name__ == "__main__":
    main()
