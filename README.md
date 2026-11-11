# BTC Race — Dip Buyer (bars-replay/v1)

A paper-trading bot for the make.fun competition
`btc-race-24h-20261111-0801` ("Race a BTC trading bot live for 24 hours").
It trades Coinbase Wrapped BTC (cbBTC) against USDC on 1-minute bars with
1,000 USDC of paper capital. Nothing is traded for real.

## What it does

The bot buys dips and sells the bounce, and does nothing else:

- **Entry** (flat only, one dip per episode): the trailing 60-minute return
  is between −80 and −38 bps, and the fall has slowed (trailing 15-minute
  return no worse than −90 bps — it never catches an accelerating knife).
- **Size**: 100% of cash. The pool's quoted depth (~$1.17M) dwarfs a
  ~$1,000 order, so price impact is under a basis point and undeployed
  cash is pure drag; in testing the score rose monotonically from 90%
  sizing up to 100%.
- **Deep-downtrend veto**: if the trailing 240-minute return is below
  −160 bps the dip is skipped entirely. (The first version traded these
  at reduced size instead; skipping scored better — in a grinding slide
  the bounce rarely repays the failures on the way down.)
- **Exit** (whole position, first trigger wins): +52 bps take profit, or
  a 90-batch time stop. There is deliberately **no price stop**: the
  first version's −90 bps stop and 75-batch hold were found cutting
  winners — dips that dip further usually still bounce inside 90 minutes,
  while stopped-out trades never came back. Removing the stop and
  lengthening the hold was worth more than every entry-side refinement
  combined.
- A 10-batch cooldown after every exit and an episode lock until the
  trailing return recovers above −10 bps keep one slide from being bought
  repeatedly. Every order caps slippage at 100 bps from the decision close.

The design comes from an event study of the dev window: on this thin
on-chain pool, strength was sold (trailing +60 bps → sharply negative
forward returns) and weakness was bought back (an hourly drop in the
38–80 bps zone → a bounce larger than the ~26 bps a round trip costs in
fees, slippage and price impact; drops deeper than ~120 bps kept falling
on a 75-minute horizon). Momentum, EMA-cross and breakout variants all
lost money after fees in the same replica; the dip was the only signal
that cleared the cost hurdle. This is version 2 of the bot: a parameter
sweep and structural review of version 1 (+12.73) on the same replica —
exit redesign first (no stop, 90-batch hold), then the entry retune
(band, veto in place of down-sizing, full sizing).

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
| Final cash | 1,024.304162 USDC |
| Net PnL | **+24.304162 USDC** (dev gate: > 0 ✓) |
| Filled orders | 20 (10 buys, 10 sells; last exit is a time stop, no closing flatten) |
| Fees paid | 8.071141 USDC |
| Max drawdown | 0.69% (guardrail: ≤ 25%) |
| Split halves | +4.562823 (first half) / +13.415336 (second half) |
| Do-nothing baseline | 0.00 USDC, 0 trades (fails the gate by rule) |
| Buy-and-hold reference | −13.44 USDC |
| Version 1 of this bot | +12.733193 USDC, 19 fills |

Two executions produce byte-identical replies; the whole 1,440-batch run
takes ~0.2 s of wall time on one core (budget: 1,800 s), and the largest
reply is 147 bytes (limit: 65,536).

## Notes

- Deterministic: no wall clock, no randomness, no file or network access;
  time is the bars' own timestamps.
- The bot only trades CBBTC, the competition's single symbol, taken from
  the hello's universe rather than hard-coded.
- Bars missing from a batch (recorder gaps) are handled: the bot holds
  and waits for the next fresh bar.
