"""
BUILDERR ROUND 1 — agent.py  v5  FINAL  (June 26, 2026)
=========================================================
SITUATION:
  Rank 8/31 | Capital $97,768 + recovery | 5 days left (Jun 26 - Jul 2)
  Target: +10% total return to challenge #1 (+9%)
  Problem: only ~5 trades so far — need MORE trades, MORE conviction

MARKET INTELLIGENCE (June 25-26, 2026 — LIVE DATA):
  ✅ MEMORY/CHIPS EXPLODING:
     • Micron +15.78% after blowout Q3 ($41.46B revenue, 346% YoY)
     • SanDisk +22% Thursday (S&P 500's top 2026 performer +464% YTD)
     • Memory supply tight through 2027 — structural not cyclical
     • SMH semiconductor ETF recovering from Tuesday's pullback
  ✅ ROTATION TRADE IN PLAY:
     • Mag7 (AAPL -6%, MSFT -3%) selling off on hardware price hikes
     • Russell 2000 +1.29%, Dow +1.13% — rotation to industrials/healthcare/financials
     • IWM (small caps) and XLI (industrials) outperforming
  ✅ OIL FALLING — GOOD FOR MARKET:
     • Crude below $72 (Strait of Hormuz partially reopening)
     • Lower oil → lower inflation → Fed less hawkish
     • Benefits: XLY (consumer), XLI (industrials), airlines/transport
  ✅ GOOGL ADDED TO DOW — positive momentum
     • Alphabet replacing Verizon in DJIA effective Monday Jun 30
     • Institutional rebalancing will FORCE buying of GOOGL next week
  ⚠️  APPLE/MSFT WEAK — avoid
     • Hardware price hikes hurting both — stay underweight
  ⚠️  PCE DATA FRIDAY (Jun 27) — could add volatility
     • Oil falling → likely benign PCE → positive for markets

STRATEGY FOR 5 DAYS:
  Target: +10% net total return
  
  WEEK SPLIT:
  Friday Jun 27:   Memory surge continuation (MU, SNDK, SMH, WDC)
                   Rotation (IWM, XLI, XLF)
                   GOOGL pre-Dow inclusion buy
  
  Mon-Tue Jun 30-Jul 1: Post-PCE positioning
                   Rebalance Sunday night for Mon open
                   Continue memory/chips if momentum holds
                   Watch for end-of-quarter rebalancing flows
  
  Wed-Thu Jul 1-2: End of round — hold winners, trim losers

TRADE FREQUENCY FIX:
  Previous agent: rebalanced every 3-5 days → only ~5 trades
  THIS agent: rebalances EVERY DAY (REBALANCE_DAYS=1)
              + dual momentum scoring (short + medium term)
              + position drift threshold 15% (very sensitive)
              → generates 8-15 trades per day like leader's ~30 total

RISK MANAGEMENT:
  • Beta gross cap: 1.40x (higher, need returns)
  • Per-ticker cap: 0.26 (concentrated but diversified)
  • crash_bail: fires on -2.5% 3-bar drop (more sensitive)
  • Deploy: 97% of equity in risk_on (max exposure)
  • Stop mechanism: if equity drops >4% from peak → go defensive
"""

from __future__ import annotations
from math import sqrt
from statistics import mean, pstdev
from typing import Any

# ── UNIVERSE — carefully selected based on June 26 live intelligence ──────────

RISK_CANDIDATES = (
    # MEMORY/CHIPS — primary thesis (Micron blowout, SanDisk +22%)
    "MU",     # Micron — +15% Thursday, guidance $50B next Q
    "SMH",    # Semiconductor ETF — recovering from Tuesday selloff
    "NVDA",   # Still the AI backbone
    "AVGO",   # Broadcom — AI networking, strong momentum

    # ROTATION PLAYS — winning this week
    "IWM",    # Small caps +1.29% — rotation from Mag7
    "XLF",    # Financials — benefiting from rotation
    "XLI",    # Industrials — CAT, GE, strong all year

    # GOOGL — special catalyst: added to Dow Jones Jun 30
    "GOOGL",  # Institutional FORCED buying next week for Dow inclusion

    # BROAD MARKET — momentum anchors
    "QQQ",    # Nasdaq recovery post-selloff
    "SPY",    # S&P 500 — broad exposure

    # CONSUMER/HEALTHCARE — defensive with growth
    "XLV",    # Healthcare — rotation beneficiary
    "XLY",    # Consumer — oil falling = consumer spending up

    # ENERGY — oil falling but energy still has momentum YTD
    "XLE",    # Reduced weight but included

    # TECH — selective (not AAPL/MSFT which are weak)
    "XLK",    # Tech ETF — post-selloff recovery
    "META",   # Still strong AI play, not affected by hardware prices
)

# Score multipliers — based on June 26 live market intelligence
SECTOR_BOOST = {
    # 🔥 HOT — primary thesis
    "MU":    1.50,   # Micron — blowout earnings, momentum king
    "SMH":   1.40,   # Semis recovering, memory halo effect
    "NVDA":  1.30,   # AI backbone, always relevant
    "AVGO":  1.25,   # AI networking strong

    # ✅ ROTATION WINNERS this week
    "IWM":   1.30,   # Small caps outperforming
    "XLF":   1.25,   # Financials rotation
    "XLI":   1.20,   # Industrials strong all year

    # ✅ SPECIAL CATALYST
    "GOOGL": 1.35,   # Dow inclusion Jun 30 — forced institutional buying

    # ✅ BROAD
    "QQQ":   1.10,
    "SPY":   1.05,

    # ✅ DEFENSIVE WITH GROWTH
    "XLV":   1.10,
    "XLY":   1.15,   # Oil falling = consumer spending boost

    # ⚠️ REDUCED
    "XLE":   0.65,   # Oil falling hurts energy names
    "XLK":   0.90,   # Tech ETF has AAPL/MSFT drag
    "META":  1.20,   # Strong but depressed by sector weakness
}

# Defensive retreat (crash only — we want maximum exposure)
DEFENSIVE_CRASH   = (("XLV", 0.45), ("XLF", 0.35), ("IWM", 0.20))
DEFENSIVE_RISKOFF = (("XLV", 0.35), ("XLF", 0.30), ("GLD", 0.20), ("IWM", 0.15))
CAUTIOUS_DEF      = (("XLV", 0.10), ("XLF", 0.08))

BETA_MULTIPLE: dict[str, float] = {
    "QLD": 2.0, "SSO": 2.0, "TQQQ": 3.0, "SOXL": 3.0,
    "UPRO": 3.0, "SPXL": 3.0,
}

# ── TUNING — max trades, max exposure ────────────────────────────────────────
REBALANCE_DAYS  = 1      # EVERY DAY — fixes the low trade count problem
MAX_WEIGHT      = 0.26   # concentrated but not over-exposed per name
DRIFT_LIM       = 0.15   # very sensitive — triggers rebalance on small drift
MAX_BETA_GROSS  = 1.40   # higher cap — need returns
MIN_TRADE_PCT   = 0.008  # lower threshold = more trades executed
TOP_N_RISKON    = 6      # top 6 picks
DEPLOY_PCT      = 0.97   # deploy 97% in risk_on

# Regime thresholds
VOL_CAUTION      = 0.30
CRASH_DROP_3BAR  = -0.025  # more sensitive crash detection
CRASH_VOL_RATIO  = 1.6
RISKOFF_MOM_FLOOR= -0.05

# Peak tracking for drawdown stop
_peak_equity: float = 0.0
_last_rebal_date: str | None = None


# ── Price utilities ────────────────────────────────────────────────────────────
def closes(bars):
    if not bars: return []
    out = []
    for b in bars:
        try: c = float(b["close"])
        except: return []
        if c <= 0: return []
        out.append(c)
    return out

def sma(v, n): return mean(v[-n:]) if len(v) >= n else None
def mom(v, n): return (v[-1]/v[-(n+1)] - 1.0) if len(v) > n and v[-(n+1)] > 0 else None
def rvol(v, n):
    if len(v) <= n: return None
    w = v[-(n+1):]
    rs = [w[i]/w[i-1]-1.0 for i in range(1,len(w)) if w[i-1]>0]
    return pstdev(rs)*sqrt(252.0) if len(rs)>=4 else None

# ── Portfolio helpers ──────────────────────────────────────────────────────────
def cur_pos(ps):
    out = {}
    for r in (ps.get("positions") or []):
        t = str(r.get("ticker","")).upper()
        if not t: continue
        try: qty=float(r.get("quantity",0)); cost=float(r.get("avg_cost",0))
        except: continue
        if qty<=0: continue
        e = out.setdefault(t, {"quantity":0.0,"avg_cost":cost})
        e["quantity"] += qty
    return out

def tot_equity(ps, cash):
    try: total = float(ps.get("cash", cash))
    except: total = float(cash or 0)
    lp = ps.get("last_prices", {}) or {}
    for t, p in cur_pos(ps).items():
        try: price = float(lp.get(t, p["avg_cost"]))
        except: price = p["avg_cost"]
        total += p["quantity"] * max(price, 0)
    return max(total, 0)

def mkt_prices(ms):
    out = {}
    for t, bars in ms.items():
        cs = closes(bars)
        if cs: out[t.upper()] = cs[-1]
    return out

def bar_date(ms):
    bars = ms.get("SPY") or ms.get("QQQ") or []
    if not bars: return None
    ts = bars[-1].get("ts")
    return str(ts)[:10] if ts is not None else str(len(bars))

def days_since(ms):
    if _last_rebal_date is None: return None
    bars = ms.get("SPY") or ms.get("QQQ") or []
    dates = [str(b.get("ts", i))[:10] for i, b in enumerate(bars)]
    if not dates or _last_rebal_date not in dates: return None
    return len(dates) - dates.index(_last_rebal_date) - 1

def drifted(ps, eq):
    if eq <= 0: return False
    lp = ps.get("last_prices", {}) or {}
    for t, p in cur_pos(ps).items():
        try: price = float(lp.get(t, p["avg_cost"]))
        except: price = p["avg_cost"]
        if price > 0 and (p["quantity"]*price/eq) > DRIFT_LIM:
            return True
    return False

# ── Cap enforcement ────────────────────────────────────────────────────────────
def cap(w):
    c = {t: min(max(v,0), MAX_WEIGHT) for t, v in w.items() if v > 0}
    bg = sum(v*BETA_MULTIPLE.get(t,1.0) for t, v in c.items())
    if bg > MAX_BETA_GROSS:
        sc = MAX_BETA_GROSS / bg
        c = {t: v*sc for t, v in c.items()}
    return {t: round(v,6) for t, v in c.items() if v > 0.001}

# ── Regime detection ───────────────────────────────────────────────────────────
def regime(ms):
    spy = closes(ms.get("SPY")); qqq = closes(ms.get("QQQ"))
    if len(spy)<30 or len(qqq)<30: return "risk_off"

    # Use shorter lookback when we have fewer bars
    n_sma = min(50, len(spy)-1)
    spy50 = sma(spy, n_sma); qqq50 = sma(qqq, n_sma)
    qv20  = rvol(qqq, min(20, len(qqq)-2))
    qm20  = mom(qqq, min(20, len(qqq)-2))
    sm20  = mom(spy, min(20, len(spy)-2))

    if any(x is None for x in (spy50, qqq50, qv20)): return "risk_off"

    # crash_bail
    qm3 = mom(qqq, min(3, len(qqq)-2))
    if qm3 is not None and qm3 < CRASH_DROP_3BAR: return "crash_bail"
    if len(qqq) >= 22:
        v3 = rvol(qqq, 3); v20 = rvol(qqq, 20)
        if v3 and v20 and v20 > 0 and v3 > CRASH_VOL_RATIO*v20:
            return "crash_bail"

    # Drawdown stop — if we're down >4% from peak, go cautious
    # (handled in decide() via _peak_equity)

    # risk_off — BOTH conditions
    if spy[-1] < spy50 and (qm20 is not None and qm20 < RISKOFF_MOM_FLOOR):
        return "risk_off"

    # cautious
    if qv20 >= VOL_CAUTION: return "cautious"
    if qm20 is not None and qm20 < 0: return "cautious"
    if sm20 is not None and sm20 < 0:  return "cautious"

    return "risk_on"

# ── Dual-factor scoring (short + medium momentum) ─────────────────────────────
def score_universe(ms):
    """
    Multi-factor with SHORT-TERM momentum added for responsiveness.
    
    Factors:
      0.35 × mom20    — medium momentum (primary)
      0.25 × mom60    — longer trend
      0.20 × gap_sma  — vs 20-SMA (faster signal than SMA50)
      0.15 × mom5     — SHORT-TERM (captures THIS week's moves)
      0.05 × risk_adj — quality filter
    
    Then multiply by SECTOR_BOOST for live market intelligence.
    
    Note: mom5 is ADDED here (not subtracted like in v3/v4) because
    we WANT to chase this week's momentum (MU+22%, GOOGL Dow inclusion).
    """
    scored = []
    for t in RISK_CANDIDATES:
        v = closes(ms.get(t))
        if len(v) < 25: continue  # lower bar — less history needed
        m20  = mom(v, min(20, len(v)-2))
        m60  = mom(v, min(60, len(v)-2))
        m5   = mom(v, min(5,  len(v)-2))
        n_sma = min(20, len(v)-1)
        s20  = sma(v, n_sma)
        v20  = rvol(v, min(20, len(v)-2))
        if any(x is None for x in (m20, m5, s20, v20)):
            continue
        if v20 <= 0: continue
        gap  = v[-1]/s20 - 1.0
        ramo = (m60 or m20) / v20   # use m20 if m60 not available
        # Short-term momentum ADDED (chase this week's winners)
        raw = (0.35*m20 + 0.25*(m60 or m20) + 0.20*gap
               + 0.15*m5 + 0.05*ramo)
        boosted = raw * SECTOR_BOOST.get(t, 1.0)
        scored.append((boosted, t, v20))
    scored.sort(reverse=True)
    return scored

def inv_vol_w(cands, budget):
    if not cands: return {}
    ivs = [1.0/max(v, 1e-6) for _, _, v in cands]
    tot = sum(ivs)
    if tot <= 0:
        n = len(cands)
        return {t: min(budget/n, MAX_WEIGHT) for _, t, _ in cands}
    return {t: min(budget*iv/tot, MAX_WEIGHT)
            for (_, t, _), iv in zip(cands, ivs)}

# ── Target weights ─────────────────────────────────────────────────────────────
def target_weights(ms, equity_drawdown: float = 0.0):
    r = regime(ms)

    # Emergency drawdown stop — if down >4% from peak, go cautious
    if equity_drawdown > 0.04 and r == "risk_on":
        r = "cautious"

    if r == "crash_bail":
        return cap({t: w for t, w in DEFENSIVE_CRASH
                    if closes(ms.get(t))})
    if r == "risk_off":
        return cap({t: w for t, w in DEFENSIVE_RISKOFF
                    if closes(ms.get(t))})

    scored = score_universe(ms)
    pos = [(s, t, v) for s, t, v in scored if s > 0]

    if r == "cautious":
        winners = pos[:4]
        if not winners:
            return cap({t: w for t, w in DEFENSIVE_RISKOFF
                        if closes(ms.get(t))})
        cdef = {t: w for t, w in CAUTIOUS_DEF if closes(ms.get(t))}
        rb = min(0.75, 1.0 - sum(cdef.values()))
        return cap({**cdef, **inv_vol_w(winners, rb)})

    # risk_on — top 6, deploy 97%
    winners = pos[:TOP_N_RISKON]
    if not winners:
        return cap({t: w for t, w in DEFENSIVE_RISKOFF
                    if closes(ms.get(t))})
    return cap(inv_vol_w(winners, DEPLOY_PCT))

# ── Order generation ───────────────────────────────────────────────────────────
def build_orders(targets, positions, eq, prices, cash):
    if eq <= 0: return []
    min_t = eq * MIN_TRADE_PCT
    orders = []; sell_proc = 0.0

    # Sells first
    for t, p in positions.items():
        price = prices.get(t)
        if not price or price <= 0: continue
        qty = p["quantity"]; cv = qty*price
        tv = eq * targets.get(t, 0.0)
        if t not in targets:
            sq = int(qty)
            if sq > 0 and cv >= min_t:
                orders.append({"ticker": t, "side": "sell", "quantity": sq})
                sell_proc += sq*price
        elif tv - cv < -min_t:
            sq = min(int(abs(tv-cv)/price), int(qty))
            if sq > 0:
                orders.append({"ticker": t, "side": "sell", "quantity": sq})
                sell_proc += sq*price

    spendable = max(float(cash), 0.0) + sell_proc*0.98

    # Buys second
    for t, w in sorted(targets.items(), key=lambda x: -x[1]):
        price = prices.get(t)
        if not price or price <= 0: continue
        cv = positions.get(t, {}).get("quantity", 0.0) * price
        tv = eq * w
        if tv - cv < min_t: continue
        bq = int(min(tv-cv, spendable) / price)
        if bq > 0:
            orders.append({"ticker": t, "side": "buy", "quantity": bq})
            spendable -= bq * price

    return orders[:45]

# ── Entry point ────────────────────────────────────────────────────────────────
def decide(market_state, portfolio_state, cash):
    """
    Rebalances EVERY DAY for maximum trade count and responsiveness.
    Driven by June 26, 2026 live intelligence:
      - Memory/chips (MU, SMH) surging post-Micron earnings
      - Rotation to small caps, industrials, financials
      - GOOGL Dow inclusion catalyst June 30
      - Oil falling = consumer/industrial tailwind
    """
    global _last_rebal_date, _peak_equity

    if not market_state: return []
    today = bar_date(market_state)
    if today is None: return []

    eq = tot_equity(portfolio_state, cash)

    # Track peak for drawdown stop
    if eq > _peak_equity:
        _peak_equity = eq
    drawdown = (_peak_equity - eq) / _peak_equity if _peak_equity > 0 else 0.0

    dsince = days_since(market_state)
    drift  = drifted(portfolio_state, eq)
    r      = regime(market_state)

    should_rebal = (
        _last_rebal_date is None
        or dsince is None
        or dsince >= REBALANCE_DAYS    # every single day
        or drift                        # position drifted >15%
        or r == "crash_bail"
    )
    if not should_rebal: return []

    tgts   = target_weights(market_state, drawdown)
    if not tgts: return []

    prices = mkt_prices(market_state)
    pos    = cur_pos(portfolio_state)
    orders = build_orders(tgts, pos, eq, prices, cash)

    if orders:
        _last_rebal_date = today
    return orders
