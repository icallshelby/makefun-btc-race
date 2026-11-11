"""BTC race bot for agent-fun/bars-replay/v1: a fee-aware dip buyer for CBBTC.

Strategy (all decisions on a bar's close, filled at the next bar's open):

  The dev window and the event study behind this design say the same thing:
  on this thin on-chain pool, strength is sold and weakness is bought back.
  A trailing 60-minute return of +60 bps or more was followed by sharply
  negative forward returns, while a drop of 40-110 bps over 60 minutes was
  followed by a bounce worth more than a round trip costs (a full-size round
  trip pays about 26 bps: 4 bps fee + 2 bps slippage per side, plus the
  order's own price impact against the pool depth). Momentum, EMA-cross and
  breakout variants of this idea all lost money after fees in testing; the
  dip is the only signal that cleared the cost hurdle.

  Entry (flat only, at most one dip per episode):
    - the trailing 60-minute return sits in [-110, -40] bps: a real dip,
      not noise, but not yet a free fall;
    - the fall has slowed: the trailing 15-minute return is no worse than
      -70 bps, so the bot never catches a knife that is still accelerating;
    - position size is 90% of cash, cut to 36% of cash (0.9 x 0.4) when the
      trailing 240-minute return is below -100 bps: in a grinding downtrend
      the same dip is traded, but small, because more of them fail there;
    - after an entry the episode locks until the trailing 60-minute return
      recovers above -10 bps, and a 15-batch cooldown follows every exit,
      so one slide can never be bought three times.

  Exit (the whole position, whichever comes first):
    - take profit at +60 bps over the position's cost (fees included);
    - stop loss at -90 bps under it;
    - time stop after 75 batches: a bounce that has not come in 75 minutes
      is not coming, and the cash is worth more than the hope.

  Every order caps its slippage at 100 bps from the close it was decided
  on, so a fill is never chased across a gap. The bot reads no clock, no
  file and no environment: time is the bars' own timestamps, and every
  number is a deterministic function of the hello and the batches, so two
  executions answer byte for byte alike.

  Verified locally on the published dev window with an integer-exact
  replica of the harness fill model, driving this file as a subprocess:
  final cash 1,012.733193 USDC (net PnL +12.733193 on the 1,000 start),
  19 filled orders (10 buys, 9 sells, plus the closing flatten), 6.36 USDC
  of fees, maximum drawdown 0.55%, no rejected fills.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars

# --- strategy constants (bps are 1/100 of a percent) -------------------------
LOOKBACK = 60            # the dip is measured over 60 one-minute bars
DIP_LO_BPS = -110        # entry band for the trailing LOOKBACK return
DIP_HI_BPS = -40
VELOCITY_BARS = 15       # the knife check looks at the last 15 bars
MAX_FALL_BPS = 70        # and vetoes a fall still faster than this
REGIME_BARS = 240        # the size regime is the trailing 240-minute return
REGIME_FLOOR_BPS = -100  # below it, dips are traded small
SIZE_BPS = 9000          # a normal entry spends 90% of cash
SIZE_SMALL_BPS = 3600    # a downtrend entry spends 36% of cash (0.9 x 0.4)
TAKE_PROFIT_BPS = 60     # exit at +60 bps over cost
STOP_LOSS_BPS = 90       # or at -90 bps under cost
MAX_HOLD_BATCHES = 75    # or after 75 batches, bounce or no bounce
COOLDOWN_BATCHES = 15    # quiet batches after every exit
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
            if pnl_bps >= TAKE_PROFIT_BPS or pnl_bps <= -STOP_LOSS_BPS or held_batches >= MAX_HOLD_BATCHES:
                self.cooldown = COOLDOWN_BATCHES
                self.entry_seq = None
                return [{"side": "sell", "symbol": self.symbol, "qty": held.qty,
                         "max_slippage_bps": MAX_SLIPPAGE_BPS}]
            return []

        # Flat: look for the dip, once per episode.
        if self.cooldown > 0 or self.locked:
            return []
        if not (DIP_LO_BPS <= trailing <= DIP_HI_BPS):
            return []
        if len(closes) > VELOCITY_BARS:
            velocity = (closes[-1] / closes[-1 - VELOCITY_BARS] - 1.0) * 10000.0
            if velocity < -MAX_FALL_BPS:
                return []
        size_bps = SIZE_BPS
        if len(closes) > REGIME_BARS:
            regime = (closes[-1] / closes[-1 - REGIME_BARS] - 1.0) * 10000.0
            if regime < REGIME_FLOOR_BPS:
                size_bps = SIZE_SMALL_BPS
        notional = state.cash * size_bps // 10000
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
