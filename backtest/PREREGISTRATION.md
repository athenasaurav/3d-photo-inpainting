# Pre-registration — Panic-short laggard strategy on USDT-M perpetual futures

Written and committed BEFORE any backtest was run on the exam period.
Date: 2026-07-04. Author: Claude (rebuild-from-scratch session; the original
stock-coin engine was not available in any accessible repository, so this is
a re-implementation from its written description, NOT the original code).

## Question

Does the stock-coin panic-short edge (BTC/ETH dumps -> short the laggard)
exist on ordinary Binance USDT-M perpetual futures, where shorting is native
but maker fees are 2 bps instead of zero?

## Data

- Source: data.binance.vision official public dumps (1m klines, futures/um).
- Period: 2024-01-01 .. 2026-07-03 UTC.
- Universe: top 90 USDT perps by overall median daily quote volume, requiring
  >= 6 months of history and >= $1M overall median daily quote volume.
  BTCUSDT/ETHUSDT are trigger assets only, never traded.
  Delisted symbols are INCLUDED if they meet the bar (no survivorship pruning
  beyond the liquidity rule). Known caveat: ranking by whole-period volume is
  mild lookahead; the causal control is the in-backtest pond rule below.
- Pond rule (causal): a coin is tradeable in month M only if its median daily
  quote volume in month M-1 was >= $1M (puddles banned, as in the stock-coin
  tests).

## Split

- Practice half: 2024-01-01 .. 2025-12-31 (all building/tuning happens here).
- Exam half:     2026-01-01 .. 2026-07-03 (opened ONCE, after the grid run
  on practice is complete and the selection is frozen).

## Frozen baseline (transplanted stock-coin ETH engine)

ETH drops >= 0.7% over 5 minutes; sleeper = coin fell < 0.2% over the same
window; patient sell limit at current price, strict-penetration fill (high
must trade strictly through the level), 60-min TTL; ladder 0.75% TP / 0.25%
warning / 0.75% emergency stop with halve-then-breakeven-then-exit touch
logic and re-arm below entry; 24h time limit; 30-min per-coin re-entry
cooldown; $10,000 notional per trade; unlimited concurrency (Gap-1 style,
concurrency distribution to be reported).

Fees: maker 0.02% on patient entries and TP/breakeven exits; taker 0.05%
plus 0.01% slippage padding on warning-3rd-touch, emergency-stop and
time-limit exits.

## Grid (run on practice half ONLY)

trigger {BTC, ETH} x drop {0.5, 0.7, 1.0}% x window {5, 10, 15}m x
sleeper {0.2, 0.3}% x ladder scale {0.5, 0.75, 1.25}% (tp = sl = scale,
warn = scale/3). 108 configs. Everything else identical to baseline.

## Selection rule (decided now)

Among grid configs with >= 800 practice trades and positive practice
per-trade PnL: pick the single highest per-trade PnL (tie-break: total PnL).
Exactly ONE selected config graduates to the exam alongside the baseline.

## Exam protocol

The exam period is run exactly twice: once for the frozen baseline, once for
the selected config. No re-runs, no parameter changes after seeing exam
results. Full grid results on the exam will NOT be produced (that would be
the fishing expedition).

## Pass / fail criteria (decided now)

PASS requires, for baseline OR selected config, on the exam half:
1. >= 100 exam trades, AND
2. positive total exam PnL, AND
3. positive per-trade exam PnL.

Anything else is a FAIL for that engine. If both fail: the verdict is that
the edge does not transfer to ordinary perps at these fee levels, and the
honest recommendation is to stay with the stock-coin engines.

## Known differences vs the original stock-coin study

- Different venue (perps vs spot stock-coins), different fee model (2 bps
  maker vs zero), funding payments NOT modeled (positions are held <= 24h;
  8-hourly funding averages near +/-1 bp per 8h and shorts often RECEIVE
  funding in panics, so omitting it is roughly neutral-to-conservative here —
  but it is a modeled gap and is listed as such).
- Re-implemented engine from a prose description; parameter semantics may
  differ in detail from the original code.
