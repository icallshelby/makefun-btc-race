# BTC Race — Dip Buyer (bars-replay/v1)

A paper-trading bot for the make.fun competition
`btc-race-24h-20261111-0801` ("Race a BTC trading bot live for 24 hours").
It trades Coinbase Wrapped BTC (cbBTC) against USDC on 1-minute bars with
1,000 USDC of paper capital. Nothing is traded for real.

## What it does

The bot buys dips and sells the bounce, and does nothing else:

- **Entry** (flat only, one dip per episode): the trailing 60-minute return
  is between −110 and −40 bps, and the fall has slowed (trailing 15-minute
  return no worse than −70 bps — it never catches an accelerating knife).
- **Size**: 90% of cash, cut to 36% when the trailing 240-minute return is
  below −100 bps (in a grinding downtrend the same dip is traded, but
  small, because more of them fail there).
- **Exit** (whole position, first trigger wins): +60 bps take profit,
  −90 bps stop loss, or a 75-batch time stop.
- A 15-batch cooldown after every exit and an episode lock until the
  trailing return recovers above −10 bps keep one slide from being bought
  repeatedly. Every order caps slippage at 100 bps from the decision close.

The design comes from an event study of the dev window: on this thin
on-chain pool, strength was sold (trailing +60 bps → sharply negative
forward returns) and weakness was bought back (a 40–110 bps hourly drop →
a bounce larger than the ~26 bps a full-size round trip costs in fees,
slippage and price impact). Momentum, EMA-cross and breakout variants all
lost money after fees in the same replica; the dip was the only signal
that cleared the cost hurdle.

## Files

- `main.py` — the bot (Python 3.13, standard library only). Run as
  `python3 -P -s /bot/main.py`, exactly as the grader runs it.
- `vendor/makefun_bars.py` — a byte-for-byte copy of the kit's Python SDK
  shim for `agent-fun/bars-replay/v1` (sha256
  `6d00c4ca212adfacc3e10cc4b9fabdac0169b1fed3c62922f33d704dfd98c0f9`,
  matching `sdk/makefun_bars.py` in the competition kit). It owns the
  wire: hello, one reply per batch echoing its seq, done.
- `LICENSE` — MIT.

## Local result on the dev window

Graded with an integer-exact replica of the harness fill model (fee
4 bps, slippage 2 bps, constant-product impact against each bar's open
depth, flatten at the last close), driving `main.py` as a subprocess over
the kit's frozen 1,440-bar dev window:

| | |
|---|---|
| Final cash | 1,012.733193 USDC |
| Net PnL | **+12.733193 USDC** (dev gate: > 0 ✓) |
| Filled orders | 19 (10 buys, 9 sells) + closing flatten |
| Fees paid | 6.359710 USDC |
| Max drawdown | 0.55% (guardrail: ≤ 25%) |
| Do-nothing baseline | 0.00 USDC, 0 trades (fails the gate by rule) |
| Buy-and-hold reference | −13.44 USDC |

Two executions produce byte-identical replies; the whole 1,440-batch run
takes ~0.2 s of wall time on one core (budget: 1,800 s), and the largest
reply is 146 bytes (limit: 65,536).

## Notes

- Deterministic: no wall clock, no randomness, no file or network access;
  time is the bars' own timestamps.
- The bot only trades CBBTC, the competition's single symbol, taken from
  the hello's universe rather than hard-coded.
- Bars missing from a batch (recorder gaps) are handled: the bot holds
  and waits for the next fresh bar.
