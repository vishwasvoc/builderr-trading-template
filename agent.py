"""
Simple Momentum + Risk-Off Rotation bot — builderr trading challenge.

Strategy (deliberately few moving parts):
  1. Rank a fixed basket of liquid, diversified tickers by N-day momentum.
  2. Hold the top K names, equally weighted, capped per position.
  3. Only hold a name if it's also above its own short SMA (trend filter) —
     otherwise that slot goes to cash.
  4. Market-wide safety switch: if QQQ is below its long SMA, go to 100% cash,
     full stop, regardless of individual stock signals.
  5. Rebalance weekly, not daily — avoids churn, easy to stay under trade caps.

No network calls. No LLM. No lookahead — every calculation only uses bars
already given to decide() up to "today." Long-only, no leveraged ETFs used,
so the beta-adjusted gross cap is automatically respected.

Only 5 real parameters: BASKET, TOP_K, MOM_LOOKBACK, TREND_SMA, MARKET_SMA.
"""

from __future__ import annotations
from statistics import mean
from typing import Any

# ---- Parameters (deliberately few) -----------------------------------
BASKET = ("NVDA", "AMD", "MU", "MRVL", "AVGO", "SMH", "AAPL", "MSFT")  # candidates to rank
MARKET_TICKER = "QQQ"        # broad market gauge for the risk-off switch
TOP_K = 4                    # how many names to hold at once
MAX_WEIGHT = 0.24             # cap per position (stays under the 30% rule with margin)
MOM_LOOKBACK = 63             # ~3 months of trading days, for ranking
TREND_SMA = 50                # per-stock trend filter length
MARKET_SMA = 100               # risk-off switch length on QQQ
REBALANCE_EVERY_DAYS = 5       # ~once a week
MIN_TRADE_PCT = 0.01           # ignore rebalances smaller than 1% of equity

_last_rebalance_date: str | None = None


# ---- Small helpers ------------------------------------------------------
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
        return {}  # not enough history yet — stay in cash

    market_sma = _sma(qqq, MARKET_SMA)
    risk_on = market_sma is not None and qqq[-1] > market_sma
    if not risk_on:
        return {}  # the one rule that matters: step aside into cash

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

    weight_each = min(MAX_WEIGHT, 0.96 / len(winners))
    return {t: weight_each for t in winners}


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

    # Sell anything not in targets, or trimmed down to target.
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

    # Buy up to target for everything we want to hold.
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

    days_since = _days_since(market_state, MARKET_TICKER, _last_rebalance_date)
    should_rebalance = (
        _last_rebalance_date is None
        or days_since is None
        or days_since >= REBALANCE_EVERY_DAYS
    )
    if not should_rebalance:
        return []

    targets = target_weights(market_state)

    prices = {t: _closes(b)[-1] for t, b in market_state.items() if _closes(b)}
    positions = _positions(portfolio_state)
    total_equity = _equity(portfolio_state, cash)

    orders = _orders_to_rebalance(targets, positions, total_equity, prices, cash)

    # Only mark "rebalanced" if we actually evaluated the book (even if that
    # produced zero orders because we were already correctly positioned).
    _last_rebalance_date = latest_date
    return orders
