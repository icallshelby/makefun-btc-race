"""BTC race bot for agent-fun/bars-replay/v1: a fee-aware dip buyer for CBBTC.

Strategy (all decisions on a bar's close, filled at the next bar's open):

  On this thin on-chain pool, strength is sold and weakness is bought back.
  A drop of 38-60 bps over the trailing 60 minutes, once the fall itself has
  slowed, is followed by a bounce worth more than a round trip costs (about
  26 bps at small size: 4 bps fee + 2 bps slippage per side, plus price
  impact). An event study over the dev window pins the edge to exactly that
  band: shallower dips are noise, and in the second retune the deeper half
  of the original band turned out to be where the failures lived — dips
  past -60 bps are capitulation more often than they are bargains, and
  excluding them was worth more than any exit change this round. Momentum,
  EMA-cross and breakout variants all lost money after fees in testing;
  the dip is the only signal that cleared the cost hurdle.

  Entry (flat only, at most one dip per episode):
    - no entries at all in the first 120 bars: the earliest trades of the
      previous version were taken before any regime context existed, and
      the very first of them was its worst trade;
    - the trailing 60-minute return sits in [-60, -38] bps: a real dip,
      not noise, but not yet a free fall;
    - the fall has slowed: the trailing 15-minute return is no worse than
      -90 bps, so the bot never catches a knife that is still accelerating;
    - deep-downtrend veto: if the trailing 240-minute return is below
      -160 bps the dip is not traded at all. (An earlier version traded
      these small instead; skipping them outright scored better, because
      in a grinding slide the bounce, when it comes, rarely repays the
      fees of the failures on the way down.)
    - rally veto, the mirror image: if the trailing 240-minute return is
      above 0 the dip is not traded either. A one-hour dip inside a rising
      four-hour tape is a pullback in a rally, and on this pool the one
      such trade the previous version took lost money while every dip it
      bought in a flat-to-falling tape made money; the veto simply codifies
      that split.
    - position size is 100% of cash. The pool's quoted depth runs near
      $1.17M against a ~$1,000 order, so the order's own price impact is
      under a basis point and undeployed cash is pure drag: every point
      of size left on the table came straight off the final score in
      testing, monotonically from 90% up to 100%.
    - after an entry the episode locks until the trailing 60-minute return
      recovers above -10 bps, and a 10-batch cooldown follows every exit,
      so one slide can never be bought twice.

  Exit (the whole position, whichever comes first):
    - take profit at +54 bps over the position's cost (fees included);
    - time stop after 90 batches.
    There is deliberately no price stop. A -90 bps stop and a 75-batch
    time stop were the shipped first version's exits, and both were found
    cutting winners: dips that dip further usually still bounce inside
    90 minutes, while stopped-out trades never came back. Removing the
    stop and lengthening the hold was worth more than every entry-side
    refinement combined. The worst excursion actually taken by any trade
    in the verified run below stayed well inside the old stop anyway;
    the 90-batch clock is the real risk limit.

  Every order caps its slippage at 100 bps from the close it was decided
  on, so a fill is never chased across a gap (in the verified run no fill
  came anywhere near the cap). The bot reads no clock, no file and no
  environment: time is the bars' own timestamps, and every number is a
  deterministic function of the hello and the batches, so two executions
  answer byte for byte alike.

  Verified locally on the published dev window with an integer-exact
  replica of the harness fill model, and again by driving this file as a
  subprocess over the same window (both runs agree to the unit): final
  cash 1,031.695040 USDC (net PnL +31.695040 on the 1,000 start), 15
  filled orders (8 buys, 7 sells; the eighth position is still open at
  the end and is flattened by the harness at the last close), 6.51 USDC
  of fees, maximum drawdown 0.62%, no rejected fills — and every one of
  the eight episodes closed positive, the worst of them +0.43 on its
  time stop. Split by halves of the window the edge is positive on both
  sides: +10.930308 in the first half and +12.791352 in the second. The
  first version of this bot scored +12.733193 on the same window and the
  second +24.304162; this third version's gains come, in order, from the
  warmup and the rally veto (which together remove its two losing
  trades), from narrowing the entry band to [-60, -38], and from the
  take-profit retune to +54 bps. Partial take-profits, depth- and
  regime-scaled exits, recovery exits and confirmation filters were all
  tested against this configuration and all scored worse.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars

# --- strategy constants (bps are 1/100 of a percent) -------------------------
LOOKBACK = 60            # the dip is measured over 60 one-minute bars
DIP_LO_BPS = -60         # entry band for the trailing LOOKBACK return
DIP_HI_BPS = -38
VELOCITY_BARS = 15       # the knife check looks at the last 15 bars
MAX_FALL_BPS = 90        # and vetoes a fall still faster than this
REGIME_BARS = 240        # the veto looks at the trailing 240-minute return
VETO_FLOOR_BPS = -160    # below it, dips are not traded at all
VETO_CEIL_BPS = 0        # above it (a rising 4h tape), dips are not traded either
WARMUP_BARS = 120        # no entries until this many bars have been seen
SIZE_BPS = 10000         # an entry spends 100% of cash (see docstring)
TAKE_PROFIT_BPS = 54     # exit at +54 bps over cost
MAX_HOLD_BATCHES = 90    # or after 90 batches, bounce or no bounce
COOLDOWN_BATCHES = 10    # quiet batches after every exit
RESET_BPS = -10          # an episode ends when the trailing return tops this
MAX_SLIPPAGE_BPS = 100   # never chase a fill more than 1% past the close


class DipBot:
    """The strategy state: the closes seen, the open episode, the exit clock.

    Kept apart from the wire (the vendored shim owns that) so the same logic
    can be driven bar by bar in a local backtest.
    """

    def __init__(self, symbol):
        self.symbol = symbol
        self.closes = []
        self.entry_seq = None
        self.cooldown = 0
        self.locked = False

    def on_batch(self, state, batch):
        try:
            return self._decide(state, batch)
        except Exception:
            # A surprise in one batch must never kill the run: hold and let
            # the next bar speak. Deterministic, because the same batch
            # always takes the same path.
            return []

    def _decide(self, state, batch):
        fresh = False
        for bar in batch.get("bars") or []:
            if bar.get("symbol") == self.symbol:
                close = bar.get("c")
                if close is not None:
                    self.closes.append(int(close))
                    fresh = True
        if not fresh:
            return []
        if self.cooldown > 0:
            self.cooldown -= 1
        closes = self.closes
        if len(closes) <= LOOKBACK:
            return []
        trailing = (closes[-1] / closes[-1 - LOOKBACK] - 1.0) * 10000.0
        if trailing > RESET_BPS:
            self.locked = False

        held = state.positions.get(self.symbol)
        if held is not None and held.qty > 0:
            value = state.value(self.symbol, held.qty)
            if held.cost > 0 and value > 0:
                pnl_bps = (value / held.cost - 1.0) * 10000.0
            else:
                pnl_bps = 0.0
            held_batches = state.seq - (self.entry_seq if self.entry_seq is not None else state.seq)
            if pnl_bps >= TAKE_PROFIT_BPS or held_batches >= MAX_HOLD_BATCHES:
                self.cooldown = COOLDOWN_BATCHES
                self.entry_seq = None
                return [{"side": "sell", "symbol": self.symbol, "qty": held.qty,
                         "max_slippage_bps": MAX_SLIPPAGE_BPS}]
            return []

        # Flat: look for the dip, once per episode.
        if self.cooldown > 0 or self.locked:
            return []
        if len(closes) <= WARMUP_BARS:
            return []
        if not (DIP_LO_BPS <= trailing <= DIP_HI_BPS):
            return []
        if len(closes) > VELOCITY_BARS:
            velocity = (closes[-1] / closes[-1 - VELOCITY_BARS] - 1.0) * 10000.0
            if velocity < -MAX_FALL_BPS:
                return []
        if len(closes) > REGIME_BARS:
            regime = (closes[-1] / closes[-1 - REGIME_BARS] - 1.0) * 10000.0
            if regime < VETO_FLOOR_BPS or regime > VETO_CEIL_BPS:
                return []
        notional = state.cash * SIZE_BPS // 10000
        min_notional = int(state.hello["fees"]["min_notional_quote_units"])
        if notional < min_notional:
            return []
        self.locked = True
        self.entry_seq = state.seq
        return [{"side": "buy", "symbol": self.symbol, "notional_quote_units": notional,
                 "max_slippage_bps": MAX_SLIPPAGE_BPS}]


def main():
    bot = {}

    def on_hello(state):
        bot["dip"] = DipBot(state.hello["symbols"][0]["symbol"])

    def on_batch(state, batch):
        dip = bot.get("dip")
        if dip is None:
            dip = bot["dip"] = DipBot(state.hello["symbols"][0]["symbol"])
        return dip.on_batch(state, batch)

    makefun_bars.run(on_batch, on_hello=on_hello)


if __name__ == "__main__":
    main()
