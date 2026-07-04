"""Panic-short laggard backtest engine (USDT-M perpetual futures).

Strategy, rebuilt from the frozen stock-coin description:
  * Fire alarm : trigger asset (BTC or ETH) falls >= `drop` over trailing
    `window` minutes (close-to-close).
  * Sleeper    : a universe coin whose own return over the same window is
    better than -`sleeper_thresh` (the wave hasn't reached it).
  * Entry      : post a patient SELL limit at the sleeper's current close P.
    Conservative fill rule: filled only on a later bar whose high goes
    STRICTLY through P. Cancel after `entry_ttl` minutes unfilled.
  * Ladder (fractions of P, defaults mirror $75/$25/$75 per $10k):
      - take-profit  : buy limit at P*(1-tp)  (maker), fill only if low < level
      - warning line : P*(1+warn). Upward crossings count as touches:
          1st touch -> tp halves; 2nd touch -> target becomes breakeven (P);
          3rd touch -> exit immediately at the warning line (taker).
        If price trades back below entry (low < P) touches reset and the
        original tp is restored.
      - emergency stop: high >= P*(1+sl) -> exit at P*(1+sl) (taker). Checked
        FIRST within every bar (pessimistic).
      - time limit   : `max_hold` minutes -> exit at close (taker).
  * Within-bar pessimism: stop -> warning -> take-profit -> re-arm.
  * One live order/position per coin; `reentry_cooldown` minutes after an
    order is placed before that coin may take a new signal.

Fees on $10k notional: maker entries/exits maker_fee, panic exits taker_fee
plus `slip` slippage padding.
"""

import numpy as np
from numba import njit

NOTIONAL = 10_000.0

# exit codes
X_TP, X_WARN3, X_STOP, X_TIME, X_BREAKEVEN = 0, 1, 2, 3, 4


@njit(cache=True)
def simulate_coin(high, low, close, trig, tradeable,
                  sleeper_thresh, window, entry_ttl, tp0, warn, sl,
                  max_hold, reentry_cooldown,
                  maker_fee, taker_fee, slip):
    """Simulate one coin against the trigger series. Returns trade arrays."""
    n = close.shape[0]
    max_trades = 200_000
    t_entry = np.empty(max_trades, dtype=np.int64)
    t_exit = np.empty(max_trades, dtype=np.int64)
    pnl = np.empty(max_trades, dtype=np.float64)
    code = np.empty(max_trades, dtype=np.int64)
    ntr = 0
    next_ok = 0  # earliest minute a new order may be placed

    t = window
    while t < n - 1:
        if t < next_ok or not trig[t] or not tradeable[t]:
            t += 1
            continue
        c_now = close[t]
        c_then = close[t - window]
        if not (c_now == c_now and c_then == c_then and c_then > 0.0):
            t += 1
            continue
        ret = c_now / c_then - 1.0
        if ret <= -sleeper_thresh:  # wave already reached it
            t += 1
            continue

        # --- post patient sell limit at P = c_now ---
        P = c_now
        next_ok = t + reentry_cooldown
        fill_t = -1
        end_wait = min(t + entry_ttl, n - 1)
        for u in range(t + 1, end_wait + 1):
            if high[u] == high[u] and high[u] > P:
                fill_t = u
                break
        if fill_t < 0:
            t += 1
            continue

        # --- manage the position ---
        tp = tp0
        touches = 0
        target = P * (1.0 - tp)     # current buy-back level (maker)
        warnP = P * (1.0 + warn)
        stopP = P * (1.0 + sl)
        above_warn = high[fill_t] == high[fill_t] and high[fill_t] >= warnP
        exit_t = -1
        exit_px = 0.0
        exit_code = -1
        end_hold = min(fill_t + max_hold, n - 1)

        for u in range(fill_t + 1, end_hold + 1):
            h = high[u]
            l = low[u]
            if not (h == h and l == l):
                continue
            # 1) emergency stop
            if h >= stopP:
                exit_t = u
                exit_px = stopP
                exit_code = X_STOP
                break
            # 2) warning-line upward crossing
            if h >= warnP:
                if not above_warn:
                    touches += 1
                    above_warn = True
                    if touches == 1:
                        tp = tp0 * 0.5
                        target = P * (1.0 - tp)
                    elif touches == 2:
                        target = P  # breakeven escape
                    elif touches >= 3:
                        exit_t = u
                        exit_px = warnP
                        exit_code = X_WARN3
                        break
            else:
                above_warn = False
            # 3) take-profit / breakeven fill (maker buy limit, strict)
            if l < target:
                exit_t = u
                exit_px = target
                exit_code = X_BREAKEVEN if target >= P else X_TP
                break
            # 4) re-arm if price back in our favor past entry
            if touches > 0 and l < P:
                touches = 0
                tp = tp0
                target = P * (1.0 - tp)

        if exit_t < 0:  # time limit
            exit_t = end_hold
            exit_px = close[end_hold]
            if not (exit_px == exit_px):
                # find last finite close
                v = end_hold
                while v > fill_t and not (close[v] == close[v]):
                    v -= 1
                exit_px = close[v]
            exit_code = X_TIME

        gross = NOTIONAL * (P - exit_px) / P
        fee_in = NOTIONAL * maker_fee
        if exit_code == X_TP or exit_code == X_BREAKEVEN:
            fee_out = NOTIONAL * maker_fee
        else:
            fee_out = NOTIONAL * (taker_fee + slip)
        t_entry[ntr] = fill_t
        t_exit[ntr] = exit_t
        pnl[ntr] = gross - fee_in - fee_out
        code[ntr] = exit_code
        ntr += 1
        if ntr >= max_trades:
            break
        next_ok = max(next_ok, exit_t + 1)
        t = max(t + 1, fill_t)

    return t_entry[:ntr], t_exit[:ntr], pnl[:ntr], code[:ntr]


def make_trigger(close_trig, window, drop):
    """Boolean array: trigger asset fell >= drop over trailing window minutes."""
    n = close_trig.shape[0]
    trig = np.zeros(n, dtype=np.bool_)
    c = close_trig.astype(np.float64)
    r = np.full(n, np.nan)
    r[window:] = c[window:] / c[:-window] - 1.0
    trig[window:] = r[window:] <= -drop
    trig[~np.isfinite(np.nan_to_num(r, nan=np.nan))] = False
    trig = np.where(np.isfinite(r), trig, False)
    return trig
