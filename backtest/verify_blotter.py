"""Row-by-row audit of the uploaded trade blotter against Binance's public
1m futures archive.

Checks per trade (short, $10k notional, ladder 0.75/0.25/0.75):
  A. candles exist at signal, fill and exit minutes
  B. chronology: signal < fill <= exit
  C. maker sell fill: fill-minute high must reach entry price (strict >)
  D. exit price plausibility by reason:
       TARGET       exit == entry*(1-0.0075) or entry*(1-0.00375) (halved)
                    or entry (breakeven); exit-minute low must reach it
       REAL_STOP    exit == entry*(1+0.0075); exit-minute high must reach it
       WARN_3RD     exit == entry*(1+0.0025); exit-minute high must reach it
       TIME_CAP     exit within exit-minute [low, high]
  E. PnL arithmetic: gross = 10000*(entry-exit)/entry, maker fee 0,
     taker fee $4 on REAL_STOP / WARN_3RD / TIME_CAP
  F. ETH fire alarm: ETHUSDT perp fell >= 0.7% over the 5 minutes ending at
     the signal minute
  G. sleeper: the coin itself fell < 0.2% over the same 5 minutes
"""

import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import data_pipeline as dp

SCRATCH = dp.SCRATCH
ZDIR = os.path.join(SCRATCH, "verifzips")
GRID_START = dp.GRID_START
N_MIN = dp.N_MIN

BLOTTER = "/root/.claude/uploads/adaf3827-6404-529f-9624-cce6c1e5b3cf/421afe71-blotter_eth12_tp75.csv"
RTOL = 2e-4      # price-level match tolerance (blotter has 5-6 sig figs)
EPS = 1e-9


def load_sym(sym, zdir=ZDIR):
    high = np.full(N_MIN, np.nan, np.float64)
    low = np.full(N_MIN, np.nan, np.float64)
    close = np.full(N_MIN, np.nan, np.float64)
    for f in sorted(x for x in os.listdir(zdir) if x.startswith(sym + "-1m-")):
        df = dp.read_kline_zip(os.path.join(zdir, f))
        ts = df["open_time"].to_numpy(np.int64)
        ts = np.where(ts > 100_000_000_000_000, ts // 1000, ts)
        idx = (ts - int(GRID_START.value // 1_000_000)) // 60_000
        ok = (idx >= 0) & (idx < N_MIN)
        idx = idx[ok]
        high[idx] = df["high"].to_numpy(np.float64)[ok]
        low[idx] = df["low"].to_numpy(np.float64)[ok]
        close[idx] = df["close"].to_numpy(np.float64)[ok]
    return high, low, close


def midx(s):
    return int((pd.Timestamp(s, tz="UTC") - GRID_START).total_seconds() // 60)


def close_to(a, b):
    return abs(a - b) <= RTOL * max(abs(a), abs(b))


def main():
    b = pd.read_csv(BLOTTER)
    syms = sorted(b.symbol.unique())
    data = {s: load_sym(s) for s in syms}
    ez = np.load(os.path.join(SCRATCH, "m1npy", "ETHUSDT.npz"))
    eth_close = ez["close"].astype(np.float64)

    fail_counts = {}
    rows = []
    for r in b.itertuples():
        high, low, close = data[r.symbol]
        t_sig = midx(r.signal_time_utc)
        t_fill = midx(r.fill_time_utc)
        t_exit = midx(r.exit_time_utc)
        errs = []

        # A/B
        if not (t_sig < t_fill <= t_exit):
            errs.append("B:chronology")
        for name, t in (("signal", t_sig), ("fill", t_fill), ("exit", t_exit)):
            if not np.isfinite(close[t]):
                errs.append(f"A:no-candle-{name}")

        entry, exitp = float(r.entry_price), float(r.exit_price)
        if np.isfinite(high[t_fill]):
            # C: maker sell fill needs the market to trade through the level
            if not (high[t_fill] > entry * (1 - EPS)):
                errs.append("C:fill-impossible")

        # D
        reason = r.exit_reason
        lvl_ok, touch_ok = True, True
        if reason == "TARGET":
            lvls = [entry * (1 - 0.0075), entry * (1 - 0.00375), entry,
                    entry * (1 - 0.0004)]  # breakeven escape posted 4bp below
            lvl_ok = any(close_to(exitp, L) for L in lvls)
            touch_ok = np.isfinite(low[t_exit]) and low[t_exit] <= exitp * (1 + RTOL)
        elif reason == "REAL_STOP":
            lvl_ok = close_to(exitp, entry * (1 + 0.0075))
            touch_ok = np.isfinite(high[t_exit]) and high[t_exit] >= exitp * (1 - RTOL)
        elif reason == "WARN_3RD_TOUCH":
            lvl_ok = close_to(exitp, entry * (1 + 0.0025))
            touch_ok = np.isfinite(high[t_exit]) and high[t_exit] >= exitp * (1 - RTOL)
        elif reason == "TIME_CAP":
            touch_ok = (np.isfinite(low[t_exit]) and
                        low[t_exit] * (1 - RTOL) <= exitp <= high[t_exit] * (1 + RTOL))
        if not lvl_ok:
            errs.append("D:exit-level-wrong")
        if not touch_ok:
            errs.append("D:exit-never-touched")

        # E
        gross = 10000.0 * (entry - exitp) / entry
        fee = 0.0 if reason == "TARGET" else 4.0
        if abs(gross - fee - float(r.pnl_usd)) > 0.75:
            errs.append(f"E:pnl-off-by-{gross - fee - float(r.pnl_usd):+.2f}")

        # F: ETH alarm
        e_now, e_then = eth_close[t_sig], eth_close[t_sig - 5]
        if np.isfinite(e_now) and np.isfinite(e_then):
            eth_ret = e_now / e_then - 1.0
            if not (eth_ret <= -0.007 + 1e-6):
                errs.append(f"F:no-eth-alarm({eth_ret*100:+.2f}%)")
        else:
            errs.append("F:eth-data-missing")

        # G: sleeper
        c_now, c_then = close[t_sig], close[t_sig - 5]
        if np.isfinite(c_now) and np.isfinite(c_then):
            ret = c_now / c_then - 1.0
            if not (ret > -0.002 - 1e-6):
                errs.append(f"G:not-a-sleeper({ret*100:+.2f}%)")

        for e in errs:
            fail_counts[e.split("(")[0]] = fail_counts.get(e.split("(")[0], 0) + 1
        rows.append({"i": r.Index, "symbol": r.symbol, "signal": r.signal_time_utc,
                     "n_errors": len(errs), "errors": ";".join(errs)})

    res = pd.DataFrame(rows)
    res.to_csv(os.path.join(dp.REPO_RESULTS, "blotter_audit.csv"), index=False)
    n_bad = (res.n_errors > 0).sum()
    print(f"rows audited: {len(res)}")
    print(f"rows fully clean: {len(res) - n_bad}  |  rows with failures: {n_bad} ({n_bad/len(res)*100:.1f}%)")
    print("\nfailure counts by check:")
    for k, v in sorted(fail_counts.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")
    print("\nfirst 12 failing rows:")
    print(res[res.n_errors > 0].head(12).to_string(index=False))


if __name__ == "__main__":
    main()
