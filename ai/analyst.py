"""
ai/analyst.py — Claude AI integration for constraint-aware Forex trade plans.

v3 changes:
  - ICT AMD (Accumulation/Manipulation/Distribution) methodology
  - Constraint-first logic: Claude calculates max SL pips from budget,
    then finds a pair/setup that fits — never the other way around
  - NO_TRADE response: clean rejection with reason when no setup qualifies
  - Claude owns 100% of all maths — server does zero parallel calculation
  - Market context (PDH/PDL, ATR) fed as named structural references
  - Compact single-message output schema
  - Crypto removed
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_MAX_TOKENS, CLAUDE_TIMEOUT
from market.prices import fetch_market_context, fetch_all_forex_prices, _normalise

logger = logging.getLogger(__name__)


class AnalystError(Exception):
    """Raised when the analyst cannot produce any response."""


# ── Instrument specification table ────────────────────────────────────────────
# Passed verbatim into the prompt so Claude has exact instrument specs.
# pip_size  = smallest price increment that counts as 1 pip
# pip_usd   = USD value of 1 pip at 1.00 standard lot
# min_sl    = tightest structurally meaningful SL in pips (below this = noise)
# notes     = any broker/instrument quirks Claude must know

INSTRUMENT_SPECS = {
    "EUR/USD": {"pip_size": 0.0001, "pip_usd_std": 10.00, "min_sl_pips": 5,  "notes": "Most liquid pair. 5-pip SL viable on M1 scalp only."},
    "GBP/USD": {"pip_size": 0.0001, "pip_usd_std": 10.00, "min_sl_pips": 7,  "notes": "Volatile. Wider spreads during London open."},
    "USD/JPY": {"pip_size": 0.01,   "pip_usd_std": 6.50,  "min_sl_pips": 8,  "notes": "pip_size=0.01. pip_usd varies with rate (~$6.50 at 154)."},
    "GBP/JPY": {"pip_size": 0.01,   "pip_usd_std": 6.50,  "min_sl_pips": 12, "notes": "pip_size=0.01. Highly volatile cross — wider SL needed."},
    "EUR/JPY": {"pip_size": 0.01,   "pip_usd_std": 6.50,  "min_sl_pips": 10, "notes": "pip_size=0.01."},
    "AUD/USD": {"pip_size": 0.0001, "pip_usd_std": 10.00, "min_sl_pips": 6,  "notes": "Commodity-linked. Watch AUD/CNH correlation."},
    "USD/CAD": {"pip_size": 0.0001, "pip_usd_std": 7.60,  "min_sl_pips": 6,  "notes": "pip_usd ~$7.60 at 1.32 rate."},
    "USD/CHF": {"pip_size": 0.0001, "pip_usd_std": 11.00, "min_sl_pips": 6,  "notes": "pip_usd ~$11.00 at 0.89 rate."},
    "EUR/GBP": {"pip_size": 0.0001, "pip_usd_std": 12.50, "min_sl_pips": 5,  "notes": "Range-bound cross. Good for tight scalps."},
    "GBP/AUD": {"pip_size": 0.0001, "pip_usd_std": 10.00, "min_sl_pips": 12, "notes": "Very volatile cross."},
    "XAU/USD": {"pip_size": 0.01,   "pip_usd_std": 100.00,"min_sl_pips": 200,"notes": "pip_size=0.01. pip_usd=$1.00/pip at 0.01 lot. min_sl=200 pips (~$2 at 0.01 lot). Needs large balance for structural SL."},
    "XAG/USD": {"pip_size": 0.001,  "pip_usd_std": 50.00, "min_sl_pips": 100,"notes": "pip_size=0.001. Volatile metal."},
}

def _spec_block() -> str:
    """Format the instrument spec table for inclusion in the prompt."""
    lines = ["INSTRUMENT SPECIFICATIONS (exact — use these values, not estimates):"]
    lines.append(f"{'Pair':<10} {'pip_size':<12} {'pip_usd/std_lot':<18} {'min_sl_pips':<14} Notes")
    lines.append("─" * 90)
    for pair, s in INSTRUMENT_SPECS.items():
        lines.append(
            f"{pair:<10} {s['pip_size']:<12} ${s['pip_usd_std']:<17.2f} {s['min_sl_pips']:<14} {s['notes']}"
        )
    return "\n".join(lines)


# ── Session detector ──────────────────────────────────────────────────────────

def _session_info() -> dict:
    now = datetime.now(timezone.utc)
    h = now.hour
    if   2 <= h <  5: session, kz = "London Open",        True
    elif 7 <= h < 10: session, kz = "New York Open",      True
    elif 0 <= h <  2: session, kz = "Asian",              False
    elif 5 <= h <  7: session, kz = "London (mid)",       False
    elif 10 <= h < 12:session, kz = "London-NY Overlap",  False
    elif 12 <= h < 17:session, kz = "New York (mid)",     False
    else:              session, kz = "Off-Peak",           False
    return {
        "session":  session,
        "kz":       kz,
        "utc_time": now.strftime("%H:%M UTC"),
        "weekday":  now.strftime("%A"),
    }


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are an elite Forex trade analyst. Your methodology is ICT's AMD model \
(Accumulation / Manipulation / Distribution). Your job is to find the \
highest-probability trade that fits the trader's exact account constraints \
and return it. You must always return a TRADE unless the market is \
genuinely closed or price data is unavailable.

══════════════════════════════════════════════════════
STEP 1 — CALCULATE CONSTRAINTS FIRST (always do this)
══════════════════════════════════════════════════════

Before analysing any chart, calculate:

  risk_dollars  = balance × (risk_pct / 100)
  pip_value_usd = pip_usd_per_std_lot × lot_size
  max_sl_pips   = risk_dollars / pip_value_usd    ← hard ceiling

Example: balance=$30, risk=2%, lot=0.01, pair=EUR/USD
  risk_dollars  = 30 × 0.02 = $0.60
  pip_value_usd = 10.00 × 0.01 = $0.10/pip
  max_sl_pips   = 0.60 / 0.10 = 6 pips maximum

This ceiling is absolute. You CANNOT place a SL wider than max_sl_pips.

══════════════════════════════════════════════════════
STEP 2 — PAIR SELECTION AGAINST CONSTRAINTS
══════════════════════════════════════════════════════

If the user specified a pair:
  - Check: can a structurally valid SL exist within max_sl_pips?
  - If the pair's min_sl_pips > max_sl_pips, you cannot trade that pair.
  - Switch to the best alternative pair where max_sl_pips ≥ min_sl_pips.
  - Explain the switch in the rationale field.
  - Never force a trade on an incompatible pair.

If the user said "AI picks":
  - From the live prices provided, select the pair where:
    a) max_sl_pips ≥ pair's min_sl_pips (constraint fits)
    b) AMD structure is clearest at this moment
  - Prefer EUR/USD and GBP/USD for tight-budget accounts.

══════════════════════════════════════════════════════
STEP 3 — AMD ENTRY FRAMEWORK
══════════════════════════════════════════════════════

A valid trade requires evidence of all three AMD phases:

A) ACCUMULATION — Consolidation range that defines the dealing range.
   Asian range high/low and equal highs/lows are accumulation zones.

B) MANIPULATION (Judas Swing) — Price sweeps liquidity to the wrong side
   before the real move. Look for:
   - Wick below equal lows (BUY setup) or above equal highs (SELL setup)
   - PDL or PDH sweep with fast rejection candle
   - Stop hunt spike on M1/M5
   If a Judas swing is clearly visible: +3.0 confluence
   If it is probable but not confirmed: +1.5 confluence
   If there is no evidence at all: +0.0 (trade can still proceed if
   other factors compensate — be transparent in the AMD field)

C) DISTRIBUTION — The directional move begins after manipulation.
   Evidence: CHoCH or BOS on M5/M15, price leaving imbalance (FVG),
   displacement candle away from the manipulation wick.

Entry is placed at the origin of the distribution — the OB or FVG
left by the manipulation candle — not at the live price itself.

If AMD evidence is weak across all pairs, still return the best available
setup but set confluence honestly and note the weakness in caution.

══════════════════════════════════════════════════════
STEP 4 — TRADE STYLE RULES
══════════════════════════════════════════════════════

SCALP:
  - 2 TPs: TP1 minimum 1:1.5 RR, TP2 minimum 1:2.5 RR
  - SL within max_sl_pips AND ≥ min_sl_pips for the instrument
  - Entry within 15 pips of live price (Forex majors)
  - partial close: TP1=50%, TP2=50%

SWING:
  - 3 TPs targeting PDH/PDL and beyond
  - SL behind structural level (OB base, swing high/low)
  - partial close: TP1=40%, TP2=40%, TP3=20%
  - Minimum confluence score: 6.5/10

══════════════════════════════════════════════════════
STEP 5 — CONFLUENCE SCORING
══════════════════════════════════════════════════════

Score each factor. Sum them. This is the confluence field in the JSON:

  Judas swing confirmed                              : +3.0
  Judas swing probable but not confirmed             : +1.5
  CHoCH or BOS on LTF after sweep                   : +2.0
  Entry at OB or FVG (not mid-range)                 : +1.5
  Price in discount (BUY) or premium (SELL) of range : +1.0
  Kill zone active at time of analysis               : +1.0
  PDH or PDL as TP target                            : +0.5
  ATR confirms SL has breathing room                 : +0.5
  Maximum: 9.5

Kill zone NOT active: score 0 for that factor and note it in caution.
It does NOT prevent a trade. Other factors can compensate.

Minimum to generate TRADE response: 5.0 for scalp, 6.5 for swing.
If below minimum after scoring all factors: return NO_TRADE.
This should be rare — if constraints are satisfiable, a setup usually exists.

══════════════════════════════════════════════════════
STEP 6 — MATHS (own every number)
══════════════════════════════════════════════════════

  stop_loss_pips = |entry - sl| / pip_size
  risk_amount    = stop_loss_pips × pip_value_usd      ← must ≤ risk_dollars
  tp_pips[n]     = |tp[n] - entry| / pip_size
  profit[n]      = tp_pips[n] × pip_value_usd × partial_close_fraction
  total_profit   = sum of all profit[n]
  rr[n]          = tp_pips[n] / stop_loss_pips          (format "1:X.X")

  execution type:
    BUY:  live > entry → Buy Limit  | live < entry → Buy Stop  | equal → Market
    SELL: live < entry → Sell Limit | live > entry → Sell Stop | equal → Market

══════════════════════════════════════════════════════
OUTPUT — TWO POSSIBLE JSON SCHEMAS
══════════════════════════════════════════════════════

TRADE (use this almost always):
{
  "type": "TRADE",
  "pair": "EUR/USD",
  "direction": "BUY",
  "style": "scalp",
  "session": "New York Mid",
  "kill_zone": false,
  "confluence": 6.5,
  "live_price": "1.08430",
  "execution": "Buy Limit",
  "entry": "1.08380",
  "sl": "1.08320",
  "sl_pips": 6,
  "lot": "0.01",
  "pip_value": "$0.10",
  "risk_usd": "$0.60",
  "tps": [
    {"label":"TP1","price":"1.08470","pips":9,"rr":"1:1.5","close":"50%","profit":"$0.45"},
    {"label":"TP2","price":"1.08530","pips":15,"rr":"1:2.5","close":"50%","profit":"$0.75"}
  ],
  "total_profit": "$1.20",
  "trail": {"active": true, "from": "TP1", "pips": 5},
  "pdh": "1.08610",
  "pdl": "1.08190",
  "amd": "Brief 1-2 sentence description of the AMD structure observed.",
  "rationale": "2 sentences: why this entry, anchored to live price and AMD phases.",
  "caution": "Kill zone not active — reduced probability. Invalidated on 1H close below 1.08320."
}

NO_TRADE (use only when constraints genuinely cannot be satisfied,
or market is closed/data unavailable):
{
  "type": "NO_TRADE",
  "reason": "One sentence: the specific unsolvable constraint.",
  "suggestion": "One sentence: what to watch for or when to retry."
}

No markdown. No text outside the JSON object.
"""


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_prompt(
    balance: float,
    pair: Optional[str],
    risk: str,
    style: str,
    lot: str,
    notes: str,
    ctx: dict,
    all_prices: dict,
    sess: dict,
) -> str:
    risk_map  = {"conservative": 1, "moderate": 2, "aggressive": 3}
    risk_pct  = risk_map.get(risk.lower(), 2)
    risk_usd  = balance * risk_pct / 100

    # ── Account constraints block ────
    constraints = (
        f"ACCOUNT CONSTRAINTS (calculate from these — do not ignore)\n"
        f"  Balance      : ${balance:,.2f}\n"
        f"  Risk         : {risk.capitalize()} — {risk_pct}% = ${risk_usd:.2f}\n"
        f"  Lot size     : {lot} (exact — never change)\n"
        f"  Trade style  : {style.upper()}\n"
        f"  Pair request : {pair.upper() if pair else 'AI selects best opportunity'}\n"
    )

    # ── Instrument spec table ────
    spec = _spec_block()

    # ── Market data block ────
    if pair and ctx.get("live_price"):
        # Try to calculate premium/discount zone
        try:
            pdh_f = float(ctx["pdh"])
            pdl_f = float(ctx["pdl"])
            lp_f  = float(ctx["live_price"])
            mid   = pdl_f + (pdh_f - pdl_f) / 2
            zone  = "DISCOUNT (favour BUY)" if lp_f <= mid else "PREMIUM (favour SELL)"
            pct   = (lp_f - pdl_f) / (pdh_f - pdl_f) * 100
            zone_line = f"  Zone         : {zone} ({pct:.1f}% of prev day range)"
        except Exception:
            zone_line = "  Zone         : N/A"

        market_block = (
            f"LIVE MARKET DATA (fetched now — anchor all levels to live_price)\n"
            f"  Live price   : {ctx['live_price']}\n"
            f"  PDH          : {ctx.get('pdh', 'N/A')}  ← yesterday high (liquidity above)\n"
            f"  PDL          : {ctx.get('pdl', 'N/A')}  ← yesterday low  (liquidity below)\n"
            f"  Prev D open  : {ctx.get('pd_open', 'N/A')}\n"
            f"  Prev D close : {ctx.get('pd_close', 'N/A')}\n"
            f"  ATR (14,1H)  : {ctx.get('atr_1h', 'N/A')}  ← volatility baseline\n"
            f"{zone_line}"
        )
    elif pair:
        market_block = (
            "LIVE MARKET DATA\n"
            "  Live price   : UNAVAILABLE — estimate from market knowledge.\n"
            "  Flag in rationale that price is estimated."
        )
    else:
        if all_prices:
            price_lines = "\n".join(f"  {p} : {v}" for p, v in all_prices.items())
            market_block = (
                f"LIVE PRICES (AI pair selection — pick best AMD setup that fits constraints)\n"
                f"{price_lines}"
            )
        else:
            market_block = "LIVE PRICES: UNAVAILABLE — use market knowledge."

    # ── Session block ────
    kz = "✅ ACTIVE" if sess["kz"] else "❌ NOT ACTIVE"
    session_block = (
        f"SESSION\n"
        f"  Time    : {sess['utc_time']} ({sess['weekday']})\n"
        f"  Session : {sess['session']}\n"
        f"  Kill Zone: {kz}"
    )

    notes_line = f"TRADER NOTES: {notes}" if notes else "TRADER NOTES: None"

    return "\n\n".join([
        constraints,
        spec,
        market_block,
        session_block,
        notes_line,
        "Return ONLY the JSON object. No markdown. No text outside JSON.",
    ])


# ── Main entry point ──────────────────────────────────────────────────────────

async def get_trade_plan(
    balance: float,
    pair: Optional[str] = None,
    risk: str = "moderate",
    style: str = "swing",
    notes: str = "",
    lot: str = "0.01",
) -> dict:
    """
    Fetch market context then call Claude.
    Returns a dict with key 'type': 'TRADE' or 'NO_TRADE'.
    """
    sess = _session_info()
    ctx: dict = {}
    all_prices: dict = {}

    if pair:
        ctx = await fetch_market_context(pair)
    else:
        all_prices = await fetch_all_forex_prices()

    prompt = _build_prompt(
        balance=balance,
        pair=pair,
        risk=risk,
        style=style,
        lot=lot,
        notes=notes,
        ctx=ctx,
        all_prices=all_prices,
        sess=sess,
    )

    logger.info(
        "Claude call | pair=%s style=%s lot=%s balance=$%.2f live=%s",
        pair or "AI", style, lot, balance, ctx.get("live_price", "N/A"),
    )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    try:
        loop = asyncio.get_event_loop()
        msg = await loop.run_in_executor(
            None,
            lambda: client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=CLAUDE_MAX_TOKENS,
                timeout=CLAUDE_TIMEOUT,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            ),
        )
    except anthropic.APITimeoutError:
        raise AnalystError("Claude timed out. Please try again.")
    except anthropic.APIConnectionError as e:
        raise AnalystError(f"Connection error: {e}")
    except anthropic.RateLimitError:
        raise AnalystError("Rate limit hit. Wait a moment.")
    except anthropic.APIStatusError as e:
        raise AnalystError(f"Claude API error {e.status_code}: {e.message}")

    raw = msg.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        plan: dict = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("JSON parse failed: %s", raw[:400])
        raise AnalystError(f"Unexpected format from Claude. ({e})")

    if "type" not in plan:
        raise AnalystError("Claude response missing 'type' field.")

    if plan["type"] == "TRADE":
        required = {
            "pair", "direction", "style", "live_price", "execution",
            "entry", "sl", "sl_pips", "lot", "pip_value", "risk_usd",
            "tps", "total_profit", "rationale", "caution",
        }
        missing = required - plan.keys()
        if missing:
            raise AnalystError(f"TRADE response missing: {', '.join(sorted(missing))}")

    logger.info(
        "Response | type=%s pair=%s confluence=%s",
        plan.get("type"),
        plan.get("pair", "N/A"),
        plan.get("confluence", "N/A"),
    )
    return plan
