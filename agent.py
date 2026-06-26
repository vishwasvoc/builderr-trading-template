"""
Momentum + Risk-Off Rotation bot — builderr trading challenge.

Strategy (still few moving parts, now with a faster safety switch):
  1. Rank a fixed basket of liquid, diversified tickers by N-day momentum.
  2. Hold the top K names, equally weighted, capped per position.
  3. Only hold a name if it's also above its own short SMA (trend filter) —
     otherwise that slot goes to cash.
  4. SLOW switch: if QQQ is below its long SMA, go to 100% cash.
  5. FAST switch: if QQQ's recent realized volatility has spiked well above
     its normal level, go to 100% cash immediately — this no longer waits
     for price to cross a slow-moving average, which was the weak point
     exposed by the vol_spike_snapback preview window.
  6. Sizing (new): winners aren't all weighted equally anymore. Each name's
     slice is scaled down if ITS OWN recent volatility is high relative to
     the basket average, and up (up to the same hard cap) if it's calm.
     This shrinks total exposure automatically as a selloff gets choppier,
     even on days neither cash-switch has fired yet — this is the fix for
     the moderate_selloff regime, which was too gradual to trip either
     switch but still cost money while fully, equally invested.
  7. Rebalance weekly on a normal schedule, but the cash-flip check itself
     runs every day so a vol spike isn't missed between rebalances.

No network calls. No LLM. No lookahead — every calculation only uses bars
already given to decide() up to "today." Long-only, no leveraged ETFs used,
so the beta-adjusted gross cap is automatically respected. Basket is
unchanged from the original submission — same 8 tickers, nothing added.

Hard caps enforced in code, matching what preview.py has verified PASSes
against twice already: per-position weight stays under MAX_WEIGHT (24%,
comfortably under the rules' 30% concentration limit) and total deployed
capital never exceeds ~96% of equity (comfortably under the 1.5x gross
leverage cap, since this bot is long-only with no leveraged instruments).

8 real parameters total: BASKET, TOP_K, MOM_LOOKBACK, TREND_SMA, MARKET_SMA,
VOL_LOOKBACK, VOL_SPIKE_MULT, MIN_WEIGHT_MULT. Still deliberately short —
every parameter maps to one sentence of english above.
"""

from __future__ import annotations
from statistics import mean, pstdev
from typing import Any

# ---- Parameters (deliberately few) -----------------------------------
BASKET = ("NVDA", "AMD", "MU", "MRVL", "AVGO", "SMH", "AAPL", "MSFT")
MARKET_TICKER = "QQQ"          # broad market gauge for both risk-off switches
TOP_K = 4                      # how many names to hold at once
MAX_WEIGHT = 0.24              # cap per position (margin under the 30% rule)
MOM_LOOKBACK = 63              # ~3 months of trading days, for ranking
TREND_SMA = 50                 # per-stock trend filter length
MARKET_SMA = 100               # slow risk-off switch length on QQQ
VOL_LOOKBACK = 20              # window for realized volatility, both legs
VOL_SPIKE_MULT = 1.8           # fast switch: cash if vol > MULT x its own 100d average
MIN_WEIGHT_MULT = 0.5          # a name at 2x basket-average vol gets at least this fraction of a full slice
REBALANCE_EVERY_DAYS = 5       # ~once a week for the momentum re-ranking
MIN_TRADE_PCT = 0.01           # ignore rebalances smaller than 1% of equity

_last_rebalance_date: str | None = None


# ---- Small helpers (unchanged, kept minimal) ----------------------------
def _closes(bars: list[dict[str, Any]] | None) -> list[float]:
    if not bars:
        return []
    out = []
    for b in bars:
        try:
            c = float(b["close"])
        except (KeyError, TypeError, ValueError):
            return []
        if c <= 0:
            return []
        out.append(c)
    return out


def _sma(values: list[float], n: int) -> float | None:
    if len(values) < n:
        return None
    return mean(values[-n:])


def _momentum(values: list[float], n: int) -> float | None:
    if len(values) <= n:
        return None
    start = values[-(n + 1)]
    if start <= 0:
        return None
    return values[-1] / start - 1.0


def _daily_returns(values: list[float]) -> list[float]:
    return [values[i] / values[i - 1] - 1.0 for i in range(1, len(values)) if values[i - 1] > 0]


def _realized_vol(values: list[float], n: int) -> float | None:
    """Annualized realized vol over the trailing n days."""
    rets = _daily_returns(values)
    if len(rets) < n:
        return None
    window = rets[-n:]
    if len(window) < 2:
        return None
    return pstdev(window) * (252 ** 0.5)


def _vol_is_spiking(values: list[float]) -> bool:
    """Fast switch: today's realized vol vs. its own longer-run average."""
    current_vol = _realized_vol(values, VOL_LOOKBACK)
    baseline_vol = _realized_vol(values, MARKET_SMA)
    if current_vol is None or baseline_vol is None or baseline_vol <= 0:
        return False  # not enough history yet -> don't trigger on missing data
    return current_vol > baseline_vol * VOL_SPIKE_MULT


def _bar_date(market_state: dict, ticker: str) -> str | None:
    bars = market_state.get(ticker) or []
    if not bars:
        return None
    ts = bars[-1].get("ts")
    return str(ts)[:10] if ts is not None else str(len(bars))


def _days_since(market_state: dict, ticker: str, last_date: str | None) -> int | None:
    if last_date is None:
        return None
    bars = market_state.get(ticker) or []
    dates = [str(b.get("ts", i))[:10] for i, b in enumerate(bars)]
    if last_date not in dates:
        return None
    return len(dates) - dates.index(last_date) - 1


def _positions(portfolio_state: dict) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for raw in portfolio_state.get("positions", []) or []:
        ticker = str(raw.get("ticker", "")).upper()
        if not ticker:
            continue
        try:
            qty = float(raw.get("quantity", 0.0))
        except (TypeError, ValueError):
            continue
        if qty > 0:
            out[ticker] = {"quantity": qty}
    return out


def _equity(portfolio_state: dict, cash: float) -> float:
    try:
        total = float(portfolio_state.get("cash", cash))
    except (TypeError, ValueError):
        total = float(cash or 0.0)
    last_prices = portfolio_state.get("last_prices", {}) or {}
    for ticker, pos in _positions(portfolio_state).items():
        price = last_prices.get(ticker)
        if price:
            total += pos["quantity"] * float(price)
    return max(total, 0.0)


# ---- Core signal: what should we hold, and how much? -------------------
def target_weights(market_state: dict) -> dict[str, float]:
    qqq = _closes(market_state.get(MARKET_TICKER))
    if len(qqq) < MARKET_SMA:
        return {}  # not enough history yet -> stay in cash

    market_sma = _sma(qqq, MARKET_SMA)
    slow_risk_on = market_sma is not None and qqq[-1] > market_sma
    fast_risk_off = _vol_is_spiking(qqq)

    if not slow_risk_on or fast_risk_off:
        return {}  # either switch alone is enough to force cash

    scored: list[tuple[float, str]] = []
    for ticker in BASKET:
        values = _closes(market_state.get(ticker))
        if len(values) <= MOM_LOOKBACK or len(values) < TREND_SMA:
            continue
        mom = _momentum(values, MOM_LOOKBACK)
        trend = _sma(values, TREND_SMA)
        if mom is None or trend is None:
            continue
        if values[-1] <= trend:
            continue  # trend filter: only hold names still above their own SMA
        scored.append((mom, ticker))

    scored.sort(reverse=True)
    winners = [t for _, t in scored[:TOP_K]]
    if not winners:
        return {}

    # Inverse-volatility sizing: a calmer-than-average winner gets a fuller
    # slice, a choppier-than-average one gets scaled down (never below
    # MIN_WEIGHT_MULT of a full slice). This shrinks total exposure as a
    # selloff gets choppier, even before either cash-switch has fired.
    vols: dict[str, float] = {}
    for t in winners:
        v = _realized_vol(_closes(market_state.get(t)), VOL_LOOKBACK)
        vols[t] = v if v is not None and v > 0 else None

    known_vols = [v for v in vols.values() if v is not None]
    avg_vol = mean(known_vols) if known_vols else None

    raw_weights: dict[str, float] = {}
    for t in winners:
        if avg_vol is None or vols[t] is None:
            raw_weights[t] = 1.0  # not enough data yet -> treat as average
        else:
            ratio = avg_vol / vols[t]  # >1 if calmer than average, <1 if choppier
            raw_weights[t] = max(MIN_WEIGHT_MULT, min(ratio, 1.0 / MIN_WEIGHT_MULT))

    base_slice = 0.96 / len(winners)
    return {t: min(MAX_WEIGHT, base_slice * raw_weights[t]) for t in winners}


# ---- Turn target weights into orders -----------------------------------
def _orders_to_rebalance(
    targets: dict[str, float],
    positions: dict[str, dict[str, float]],
    total_equity: float,
    prices: dict[str, float],
    cash_available: float,
) -> list[dict]:
    if total_equity <= 0:
        return []
    min_trade = total_equity * MIN_TRADE_PCT
    orders: list[dict] = []
    sell_proceeds = 0.0

    for ticker, pos in positions.items():
        price = prices.get(ticker)
        if not price or price <= 0:
            continue
        current_value = pos["quantity"] * price
        target_value = total_equity * targets.get(ticker, 0.0)
        delta = target_value - current_value
        if ticker not in targets:
            qty = int(pos["quantity"])
            if qty > 0:
                orders.append({"ticker": ticker, "side": "sell", "quantity": qty})
                sell_proceeds += qty * price
        elif delta < -min_trade:
            qty = min(int(abs(delta) // price), int(pos["quantity"]))
            if qty > 0:
                orders.append({"ticker": ticker, "side": "sell", "quantity": qty})
                sell_proceeds += qty * price

    spendable = max(cash_available, 0.0) + sell_proceeds * 0.98

    for ticker, weight in sorted(targets.items()):
        price = prices.get(ticker)
        if not price or price <= 0:
            continue
        current_qty = positions.get(ticker, {}).get("quantity", 0.0)
        current_value = current_qty * price
        target_value = total_equity * weight
        delta = target_value - current_value
        if delta < min_trade:
            continue
        buy_value = min(delta, spendable)
        qty = int(buy_value // price)
        if qty > 0:
            orders.append({"ticker": ticker, "side": "buy", "quantity": qty})
            spendable -= qty * price

    return orders[:40]  # comfortable margin under the 50-trade/day cap


# ---- Entry point ---------------------------------------------------------
def decide(market_state: dict, portfolio_state: dict, cash: float) -> list[dict]:
    """Called once per day. Returns a list of long-only orders."""
    global _last_rebalance_date

    if not market_state:
        return []

    latest_date = _bar_date(market_state, MARKET_TICKER)
    if latest_date is None:
        return []

    qqq = _closes(market_state.get(MARKET_TICKER))
    fast_risk_off_today = _vol_is_spiking(qqq) if qqq else False

    days_since = _days_since(market_state, MARKET_TICKER, _last_rebalance_date)
    scheduled_rebalance = (
        _last_rebalance_date is None
        or days_since is None
        or days_since >= REBALANCE_EVERY_DAYS
    )

    positions = _positions(portfolio_state)
    holding_anything = len(positions) > 0

    # The fast vol switch can force an off-schedule flatten on ANY day,
    # not just rebalance days -- that's the whole point of it being fast.
    should_act = scheduled_rebalance or (fast_risk_off_today and holding_anything)
    if not should_act:
        return []

    targets = target_weights(market_state)
    prices = {t: _closes(b)[-1] for t, b in market_state.items() if _closes(b)}
    total_equity = _equity(portfolio_state, cash)

    orders = _orders_to_rebalance(targets, positions, total_equity, prices, cash)

    if scheduled_rebalance:
        _last_rebalance_date = latest_date
    return orders
