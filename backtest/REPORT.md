# Report — Does the stock-coin panic-short edge exist on ordinary Binance perps?

Test run 2026-07-04. Protocol: `PREREGISTRATION.md` (committed and pushed
before the exam period was touched). Engine re-implemented from the written
description of the frozen stock-coin ETH engine; the original code was not
available in any accessible repository.

## Verdict: FAIL — the edge does NOT transfer to ordinary futures coins.

The recommendation that follows from this test: **stay with the stock-coin
engines.** Do not deploy this strategy on regular Binance perpetuals.

## What was tested

- 90 most liquid USDT-M perpetual futures (out of 790 enumerated, delisted
  included), Jan 2024 – Jul 2026, 1m candles from Binance's official dumps.
- The frozen stock-coin recipe transplanted as-is (ETH −0.7%/5m alarm,
  sleeper < 0.2%, patient entry, 0.75/0.25/0.75 ladder), PLUS a
  pre-registered 108-config grid varying trigger asset (BTC/ETH), drop
  (0.5–1.0%), window (5–15m), sleeper (0.2–0.3%) and ladder scale
  (0.5–1.25%) — the grid ran on the practice half (2024–2025) only.
- Fees: 2 bps maker on patient entries/TP exits, 5 bps taker + 1 bp
  slippage on panic exits ($10k notional per trade).

## Results

Practice half (2024–2025), baseline: 11,222 trades, **−$8.86/trade**,
−$99,463 total, 43.9%→45.6% win rate, 45.5% emergency-stop rate.

Grid: **0 of 108 configurations positive.** Best config: −$4.09/trade.
Under the pre-registered selection rule, nothing was eligible for the exam.

Sealed exam (2026), baseline only, run once: 4,920 trades, **−$8.59/trade**,
−$42,263 total, 43.9% win rate, 42.5% stop rate. Practice and exam agree
almost exactly — the negative result is robust, not noise.

## The decisive decomposition

| | stock-coins (original study) | regular perps (this test) |
|---|---|---|
| avg per trade, net | **+$10.68** | **−$8.86** |
| win rate | 70% | 44–46% |
| emergency-stop rate | 29% | 42–51% |
| per-trade result at ZERO fees | ≈ +$10.7 (fees were ~0) | **−$2.81** (practice) / −$2.54 (exam) |

Even if Binance charged nothing at all, the strategy still loses on regular
perps. The failure is not the fee difference — **the edge itself is absent.**

## Why (interpretation)

On mainstream crypto pairs, the BTC/ETH→altcoin contagion propagates in
seconds: high-frequency market-makers arbitrage the lag away, so by the time
a coin looks like a "sleeper" five minutes into a panic, it is mostly a coin
that genuinely isn't going to follow — and shorting it is a coin-flip minus
costs (stop rate ~46% vs 29%). The stock-coin edge exists precisely because
those instruments are new, thinly arbitraged, and tethered to slow-moving
equity prices. The lag IS the product. Ordinary coins don't have it, and
haven't since long before 2024.

This is a genuinely useful negative: two independent markets now show the
same asymmetry, which strengthens the case that the stock-coin profit comes
from the specific microstructure of stock-coins, not from a generic
"short panics" effect that anyone could harvest anywhere.

## Files

- `PREREGISTRATION.md` — frozen rules, grid, selection rule, pass criteria
- `engine.py` — numba trade simulator (ladder, patient fills, pond rule)
- `data_pipeline.py` — Binance public-dump downloader/parser
- `run_grid.py` — practice/exam runner
- `results/practice_grid.csv` — all 109 practice results
- `results/exam_results.json` — the single exam run
- `results/liquidity_by_month.csv`, `results/universe_selected.csv`

## Honest limitations

- Re-implemented engine: parameter semantics may differ in detail from the
  original stock-coin code (that code was never pushed to any repo I could
  reach). The transplant is faithful to the written description.
- Funding payments not modeled (≤24h holds; roughly neutral-to-conservative
  for shorts during panics).
- Universe pre-selection by whole-period volume is mild lookahead; the
  causal pond rule (prior-month ≥ $1M) did the in-test gating. Given the
  result is a fail, lookahead could only have flattered it.
- Unlimited concurrency assumed (max 62 simultaneous positions observed in
  the exam) — irrelevant to the verdict, since the average trade loses.
