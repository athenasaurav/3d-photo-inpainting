"""Run the panic-short backtest: baseline and/or grid, practice and/or exam.

Usage:
  python3 run_grid.py practice          # baseline + full grid on 2024-2025
  python3 run_grid.py exam              # baseline + selected config on 2026 (ONCE)
  python3 run_grid.py exam --config K   # exam with explicitly named grid config

Periods (UTC), minutes since 2024-01-01 00:00:
  practice : 2024-01-01 .. 2025-12-31 23:59
  exam     : 2026-01-01 .. 2026-07-03 23:59
"""

import json
import os
import sys
import itertools
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine import simulate_coin, make_trigger, X_TP, X_WARN3, X_STOP, X_TIME, X_BREAKEVEN
import data_pipeline as dp

SCRATCH = dp.SCRATCH
RESULTS = dp.REPO_RESULTS
GRID_START = dp.GRID_START

MIN_PRACTICE_END = int((pd.Timestamp("2026-01-01", tz="UTC") - GRID_START).total_seconds() // 60)
N_MIN = dp.N_MIN

MAKER_FEE = 0.0002   # 2.0 bps  (USDT-M VIP0 maker)
TAKER_FEE = 0.0005   # 5.0 bps  (USDT-M VIP0 taker)
SLIP = 0.0001        # 1.0 bp   extra padding on taker exits

BASELINE = dict(trigger="ETHUSDT", drop=0.007, window=5, sleeper=0.002,
                tp=0.0075, warn=0.0025, sl=0.0075,
                entry_ttl=60, max_hold=1440, cooldown=30)

GRID = dict(
    trigger=["BTCUSDT", "ETHUSDT"],
    drop=[0.005, 0.007, 0.010],
    window=[5, 10, 15],
    sleeper=[0.002, 0.003],
    scale=[0.005, 0.0075, 0.0125],   # tp = sl = scale, warn = scale/3
)


def load_universe():
    syms = open(os.path.join(SCRATCH, "universe.txt")).read().split()
    data = {}
    for s in syms + ["BTCUSDT", "ETHUSDT"]:
        z = np.load(os.path.join(SCRATCH, "m1npy", f"{s}.npz"))
        data[s] = (z["high"], z["low"], z["close"])
    # monthly tradeability mask from liquidity table (prior-month pond rule:
    # tradeable in month M if median daily quote volume in month M-1 >= $1M)
    liq = pd.read_csv(os.path.join(SCRATCH, "liquidity_by_month.csv"))
    months = pd.period_range("2024-01", "2026-07", freq="M")
    liq_map = {(r.symbol, r.month): r.median_daily_qvol for r in liq.itertuples()}
    masks = {}
    month_start_min = {}
    for m in months:
        ts = pd.Timestamp(m.start_time, tz="UTC")
        month_start_min[str(m)] = max(0, int((ts - GRID_START).total_seconds() // 60))
    for s in syms:
        mask = np.zeros(N_MIN, dtype=np.bool_)
        for i, m in enumerate(months):
            prev = str(m - 1)
            qv = liq_map.get((s, prev), 0.0)
            if qv >= 1e6:  # pond or lake last month -> tradeable this month
                a = month_start_min[str(m)]
                b = month_start_min[str(months[i + 1])] if i + 1 < len(months) else N_MIN
                mask[a:b] = True
        masks[s] = mask
    return syms, data, masks


def run_config(syms, data, masks, cfg, t0, t1):
    """Run one config over [t0, t1) minutes. Returns trades DataFrame."""
    trig_close = data[cfg["trigger"]][2]
    trig = make_trigger(trig_close, cfg["window"], cfg["drop"])
    trig[:t0] = False
    trig[t1:] = False
    rows = []
    for s in syms:
        if s == cfg["trigger"]:
            continue
        high, low, close = data[s]
        te, tx, pnl, code = simulate_coin(
            high, low, close, trig, masks[s],
            cfg["sleeper"], cfg["window"], cfg["entry_ttl"],
            cfg["tp"], cfg["warn"], cfg["sl"],
            cfg["max_hold"], cfg["cooldown"],
            MAKER_FEE, TAKER_FEE, SLIP)
        for i in range(len(pnl)):
            rows.append((s, int(te[i]), int(tx[i]), float(pnl[i]), int(code[i])))
    return pd.DataFrame(rows, columns=["symbol", "t_entry", "t_exit", "pnl", "code"])


def summarize(trades):
    if len(trades) == 0:
        return dict(n=0, total=0.0, per_trade=0.0, winrate=0.0, stop_rate=0.0)
    return dict(
        n=len(trades),
        total=round(float(trades.pnl.sum()), 2),
        per_trade=round(float(trades.pnl.mean()), 3),
        winrate=round(float((trades.pnl > 0).mean()), 4),
        stop_rate=round(float((trades.code == X_STOP).mean()), 4),
    )


def grid_configs():
    out = []
    for tr, dr, w, sle, sc in itertools.product(
            GRID["trigger"], GRID["drop"], GRID["window"],
            GRID["sleeper"], GRID["scale"]):
        out.append(dict(trigger=tr, drop=dr, window=w, sleeper=sle,
                        tp=sc, warn=sc / 3.0, sl=sc,
                        entry_ttl=60, max_hold=1440, cooldown=30,
                        name=f"{tr[:3]}_d{dr*1000:.0f}_w{w}_s{sle*1000:.0f}_L{sc*10000:.0f}"))
    return out


def main():
    mode = sys.argv[1]
    syms, data, masks = load_universe()
    print(f"universe loaded: {len(syms)} coins")

    if mode == "practice":
        t0, t1 = 0, MIN_PRACTICE_END
        results = []
        trades_b = run_config(syms, data, masks, {**BASELINE, "name": "BASELINE"}, t0, t1)
        sb = summarize(trades_b)
        results.append({**{k: BASELINE[k] for k in ("trigger", "drop", "window", "sleeper", "tp")},
                        "name": "BASELINE", **sb})
        trades_b.to_parquet(os.path.join(SCRATCH, "trades_practice_BASELINE.parquet"))
        print("BASELINE practice:", sb, flush=True)
        for cfg in grid_configs():
            tr = run_config(syms, data, masks, cfg, t0, t1)
            s = summarize(tr)
            results.append({**{k: cfg[k] for k in ("trigger", "drop", "window", "sleeper", "tp")},
                            "name": cfg["name"], **s})
            print(cfg["name"], s, flush=True)
        df = pd.DataFrame(results)
        df.to_csv(os.path.join(RESULTS, "practice_grid.csv"), index=False)
        # pre-registered selection rule: among grid configs with n >= 800,
        # highest per-trade PnL; tie-break by total.
        elig = df[(df.name != "BASELINE") & (df.n >= 800) & (df.per_trade > 0)]
        if len(elig):
            best = elig.sort_values(["per_trade", "total"], ascending=False).iloc[0]
            json.dump(dict(best), open(os.path.join(RESULTS, "selected_config.json"), "w"),
                      default=str, indent=2)
            print("SELECTED:", best["name"])
        else:
            print("SELECTED: none eligible")

    elif mode == "exam":
        t0 = MIN_PRACTICE_END
        t1 = int((pd.Timestamp("2026-07-04", tz="UTC") - GRID_START).total_seconds() // 60)
        out = {}
        trades_b = run_config(syms, data, masks, {**BASELINE, "name": "BASELINE"}, t0, t1)
        out["BASELINE"] = summarize(trades_b)
        trades_b.to_parquet(os.path.join(SCRATCH, "trades_exam_BASELINE.parquet"))
        sel_path = os.path.join(RESULTS, "selected_config.json")
        if os.path.exists(sel_path):
            sel = json.load(open(sel_path))
            cfg = next(c for c in grid_configs() if c["name"] == sel["name"])
            trades_s = run_config(syms, data, masks, cfg, t0, t1)
            out[sel["name"]] = summarize(trades_s)
            trades_s.to_parquet(os.path.join(SCRATCH, "trades_exam_SELECTED.parquet"))
        json.dump(out, open(os.path.join(RESULTS, "exam_results.json"), "w"), indent=2)
        print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
