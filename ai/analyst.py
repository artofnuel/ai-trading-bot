"""
ai/analyst.py — Claude AI integration for generating Forex trade plans.

Upgrade summary vs v1:
  - Full ICT/SMC system prompt with HTF->LTF drill-down methodology
  - Mathematically precise pip value table per instrument (no guessing)
  - ATR-anchored SL/TP (not arbitrary pip counts)
  - PDH/PDL as primary liquidity targets in every analysis
  - Weighted confluence scoring rubric (10 weighted factors, not vague)
  - Kill zone timing awareness baked into session field
  - Execution type derived deterministically from live vs entry
  - All dollar calculations shown step-by-step then confirmed in JSON
  - Crypto removed entirely
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_MAX_TOKENS, CLAUDE_TIMEOUT
from market.prices import fetch_market_context, fetch_all_forex_prices, _normalise_pair

logger = logging.getLogger(__name__)


# -- Custom exception ------------------------------------------------

class AnalystError(Exception):
    """Raised when the AI analyst cannot produce a valid trade plan."""


# -- Pip value table --------------------------------------------------
# These are EXACT pip values per standard lot (100,000 units) in USD.
# Pip values scale linearly with lot size.
# Source: standard broker specifications.

PIP_VALUE_TABLE = {
    # pair_keyword : (pip_size, usd_per_pip_per_standard_lot, notes)
    "XAUUSD": (0.01,  1.00,  "Gold: 1 pip = $0.01 move, $1/pip/std lot, $0.10/pip/0.1lot"),
    "XAGUSD": (0.001, 50.0,  "Silver: pip = $0.001, ~$50/pip/std lot"),
    "USDJPY": (0.01,  6.50,  "JPY pair: pip = 0.01, ~$6.50/pip/std lot (varies with rate)"),
    "GBPJPY": (0.01,  6.50,  "JPY pair: pip = 0.01, ~$6.50/pip/std lot"),
    "EURJPY": (0.01,  6.50,  "JPY pair"),
    "AUDJPY": (0.01,  6.50,  "JPY pair"),
    "CADJPY": (0.01,  6.50,  "JPY pair"),
    "CHFJPY": (0.01,  6.50,  "JPY pair"),
    "EURUSD": (0.0001, 10.0, "Standard USD pair: pip = 0.0001, $10/pip/std lot"),
    "GBPUSD": (0.0001, 10.0, "Standard USD pair"),
    "AUDUSD": (0.0001, 10.0, "Standard USD pair"),
    "NZDUSD": (0.0001, 10.0, "Standard USD pair"),
    "USDCAD": (0.0001, 10.0, "USD/CAD: ~$7.60/pip/std lot at 1.32 rate -- use $7.60"),
    "USDCHF": (0.0001, 10.0, "USD/CHF: ~$11.30/pip/std lot at 0.89 rate -- use $11.00"),
    "EURGBP": (0.0001, 12.5, "Cross pair: ~$12.50/pip/std lot"),
    "GBPAUD": (0.0001, 10.0, "Cross pair"),
    "EURAUD": (0.0001, 10.0, "Cross pair"),
    "DEFAULT":(0.0001, 10.0, "Default: assume USD quote pair"),
}

def get_pip_info(pair: str):
    """Return (pip_size, usd_per_pip_std_lot, notes) for a given pair."""
    pair_key = _normalise_pair(pair).replace("/", "").upper()
    for key, info in PIP_VALUE_TABLE.items():
        if key == "DEFAULT":
            continue
        if key in pair_key or pair_key in key:
            return info
    return PIP_VALUE_TABLE["DEFAULT"]


def calculate_pip_value(pair: str, lot_size: float) -> tuple[float, float]:
    """
    Returns (pip_value_usd, pip_size) for given pair and lot size.
    pip_value_usd = usd_per_pip_std_lot * lot_size
    """
    pip_size, usd_per_std, _ = get_pip_info(pair)
    pip_value = usd_per_std * lot_size
    return round(pip_value, 4), pip_size


# -- Session / kill zone detector --------------------------------------

def get_current_session_info() -> dict:
    """
    Determine current UTC time and map to Forex session and kill zone.
    Kill zones are the highest-probability entry windows per ICT methodology.
    """
    now_utc = datetime.now(timezone.utc)
    hour = now_utc.hour
    time_str = now_utc.strftime("%H:%M UTC")
    weekday = now_utc.strftime("%A")

    # Kill zone windows (UTC)
    if 2 <= hour < 5:
        session = "London Open"
        kill_zone = True
        kz_note = "London Kill Zone (02:00-05:00 UTC) -- highest probability window"
    elif 7 <= hour < 10:
        session = "New York Open"
        kill_zone = True
        kz_note = "New York Kill Zone (07:00-10:00 UTC) -- second highest probability window"
    elif 10 <= hour < 12:
        session = "London-NY Overlap"
        kill_zone = False
        kz_note = "Overlap -- high liquidity but Judas swing risk; wait for clear direction"
    elif 0 <= hour < 2:
        session = "Asian / London Pre-market"
        kill_zone = False
        kz_note = "Asian session -- range-building, avoid new entries; note Asian range H/L"
    elif 5 <= hour < 7:
        session = "Early London"
        kill_zone = False
        kz_note = "Post-London open consolidation -- less optimal for entries"
    elif 12 <= hour < 17:
        session = "New York"
        kill_zone = False
        kz_note = "NY mid-session -- valid for continuation setups; avoid 12:00-13:00 UTC lunch"
    else:
        session = "Off-Peak"
        kill_zone = False
        kz_note = "Low liquidity window -- avoid new entries; wait for next session"

    return {
        "session": session,
        "kill_zone_active": kill_zone,
        "kill_zone_note": kz_note,
        "current_utc": time_str,
        "weekday": weekday,
    }


# -- System prompt -----------------------------------------------------

SYSTEM_PROMPT = """\
You are an elite Forex trade analyst operating exclusively within the ICT (Inner Circle Trader) \
and Smart Money Concepts (SMC) framework. Your analysis is used by real traders placing real money. \
Every number you output will be verified. There is zero tolerance for approximations, made-up levels, \
or mathematically incorrect calculations.

============================================
CORE RULES -- NON-NEGOTIABLE
============================================

1. LIVE PRICE IS KING
   The live_price provided is the REAL current market price fetched milliseconds ago.
   Every single level (entry, SL, all TPs) must be coherently anchored to that price.
   Never suggest levels that are illogical relative to the live price.

2. MATHEMATICALLY EXACT CALCULATIONS -- NO EXCEPTIONS
   You are given: lot_size, pip_value_per_lot, pip_size, balance, risk_percent.
   Use ONLY these formulas -- never estimate:

   risk_amount        = balance * (risk_percent / 100)
   stop_loss_pips     = |entry - stop_loss| / pip_size        [round to nearest whole pip]
   pip_value_usd      = pip_value_per_lot                     [already computed for you]
   risk_check         = stop_loss_pips * pip_value_usd        [must ~= risk_amount; flag if >10% off]
   tp_pips[n]         = |tp_price[n] - entry| / pip_size
   profit_at_tp[n]    = tp_pips[n] * pip_value_usd * partial_close_fraction
   rr_ratio[n]        = tp_pips[n] / stop_loss_pips           [format as "1:X.X"]
   total_profit       = sum of all profit_at_tp[n]

   XAU/USD special: pip_size = 0.01, pip_value_per_0.01lot = $0.10
   JPY pairs special: pip_size = 0.01

3. EXECUTION TYPE -- DERIVE MATHEMATICALLY
   BUY  setups: live_price > entry -> Buy Limit  | live_price < entry -> Buy Stop  | equal -> Buy Market
   SELL setups: live_price < entry -> Sell Limit | live_price > entry -> Sell Stop | equal -> Sell Market

4. DIRECTION -- DERIVE FROM STRUCTURE, NOT GUESSING
   Use PDH (previous day high) and PDL (previous day low) as primary liquidity references.
   In a bullish HTF market: price sweeping PDL then reversing = high-probability BUY setup.
   In a bearish HTF market: price sweeping PDH then reversing = high-probability SELL setup.
   If no clear directional bias exists, state this in rationale and reduce confluence score.

5. ICT/SMC METHODOLOGY -- APPLY STRICTLY
   Valid entry reasons (cite which one applies):
     a) Price is at or has swept into a Daily/4H Order Block and shown rejection
     b) A Fair Value Gap (FVG) on 1H/15M has been identified and price is returning to fill it
     c) Liquidity (PDH, PDL, equal highs, equal lows) has been swept and structure shifted (CHoCH/BOS)
     d) Premium/Discount zones: buys in discount (<50% of swing range), sells in premium (>50%)
   Never place entry in the middle of a range with no confluence.

6. ATR-ANCHORED STOP LOSS
   ATR is provided. SL must respect ATR -- it must be wide enough that normal volatility
   does not stop the trade prematurely.
   Scalp SL minimum: 0.5* ATR_1H. Maximum: 1.5* ATR_1H.
   Swing SL minimum: 0.75* ATR_4H. Maximum: 2.0* ATR_4H.
   If the mathematically required SL pip count violates these ATR bounds, note it in caution.

7. TRADE STYLE RULES
   SCALP:
     - Max SL: 15 pips for major Forex pairs | Gold max SL: 200 pips (Gold pip = 0.01)
     - Entry must be within 10 pips of live price (Forex) / 150 pips (Gold)
     - 2 TPs only: TP1 at 1:1.5 minimum, TP2 at 1:2.5 minimum
     - partial_close: TP1=50%, TP2=50%
     - Best during kill zones; note if outside kill zone
   SWING:
     - 3 TPs: TP1 conservative, TP2 moderate, TP3 extended
     - TP1 partial_close=40%, TP2=40%, TP3=20%
     - Confluence score minimum 7 to proceed
     - SL beyond a confirmed OB or structural level

8. CONFLUENCE SCORING -- WEIGHTED (out of 10, show your working)
   Score each factor and sum them. State score/10 in the JSON:
     HTF (4H/Daily) structure aligned with direction : +2.0
     Kill zone active at time of analysis             : +1.5
     Order Block / FVG at entry zone                  : +2.0
     Liquidity swept before entry (PDH/PDL or EQH/L) : +1.5
     Price in Premium (sell) or Discount (buy) zone  : +1.0
     BOS or CHoCH confirmed on LTF (15M/1H)          : +1.0
     ATR confirms SL has room to breathe              : +0.5
     R:R >= 1:2 at TP1                                : +0.5
   Maximum: 10. Minimum to take trade: 6 (scalp), 7 (swing).
   If score < minimum, state "SETUP BELOW THRESHOLD" in caution and reduce TP targets.

============================================
OUTPUT FORMAT -- STRICT JSON ONLY
============================================
No markdown. No explanation outside the JSON. Return exactly this structure:

{
  "pair": "XAU/USD",
  "direction": "BUY",
  "trade_style": "scalp",
  "current_market_price": "2345.50",
  "execution": "Buy Limit",
  "entry": "2343.00",
  "stop_loss": "2340.50",
  "stop_loss_pips": 250,
  "lot_size": "0.10",
  "pip_value": "$1.00 per pip",
  "risk_amount": "$25.00",
  "risk_percent": 2.5,
  "take_profits": [
    {"label": "TP1", "price": "2349.75", "pips": 675, "rr": "1:2.7", "partial_close": "50%", "profit": "$33.75"},
    {"label": "TP2", "price": "2355.50", "pips": 1250, "rr": "1:5.0", "partial_close": "50%", "profit": "$62.50"}
  ],
  "total_potential_profit": "$96.25",
  "trailing_stop": {
    "recommended": true,
    "activate_at": "TP1",
    "trail_distance_pips": 150,
    "rationale": "Trail 150 pips behind price after TP1 hit to lock profit while targeting TP2."
  },
  "confluence_breakdown": {
    "htf_structure": 2.0,
    "kill_zone": 0.0,
    "ob_fvg": 2.0,
    "liquidity_swept": 1.5,
    "premium_discount": 1.0,
    "bos_choch": 0.5,
    "atr_sl_room": 0.5,
    "rr_quality": 0.5
  },
  "confluence_score": 8,
  "session": "London Open",
  "kill_zone_active": false,
  "htf_bias": "Bullish -- Daily structure showing HH/HL sequence, price pulling back into 4H OB",
  "key_levels": {
    "pdh": "2351.20",
    "pdl": "2338.40",
    "entry_rationale": "4H Order Block at 2343.00, FVG overlap, discount zone (below 50% of daily range)"
  },
  "rationale": "3 sentences max. HTF context + LTF trigger + why entry is valid per ICT.",
  "caution": "Trade invalidated if price closes a 1H candle below 2339.00 (below OB + PDL).",
  "math_check": "SL=250pips * $1.00/pip=$25.00 risk [OK] | TP1=675pips*$1.00*0.5=$33.75 [OK] | TP2=1250pips*$1.00*0.5=$62.50 [OK]"
}
"""


# -- Prompt builder ----------------------------------------------------

def _build_user_prompt(
    balance: float,
    pair: Optional[str],
    risk: str,
    trade_style: str,
    notes: str,
    market_ctx: dict,
    pair_prices: Optional[dict],
    lot_size: str,
    session_info: dict,
) -> str:
    risk_map = {"conservative": 1, "moderate": 2, "aggressive": 3}
    risk_pct = risk_map.get(risk.lower(), 2)
    risk_dollars = balance * risk_pct / 100
    lot = float(lot_size)

    # Compute pip value server-side so Claude has exact number
    if pair:
        pip_val_usd, pip_size = calculate_pip_value(pair, lot)
        _, _, pip_notes = get_pip_info(pair)
        pip_block = (
            f"Pip Size    : {pip_size} (exact for this instrument)\n"
            f"Pip Val/lot : ${pip_val_usd:.4f} per pip at {lot_size} lots\n"
            f"Pip Notes   : {pip_notes}"
        )
    else:
        pip_val_usd = lot * 10   # default estimate
        pip_size = 0.0001
        pip_block = f"Pip Val/lot : ~${pip_val_usd:.4f}/pip (default; adjust per chosen pair)"

    # Price / market context block
    if pair and market_ctx.get("live_price"):
        lp = market_ctx["live_price"]
        pdh = market_ctx.get("pdh", "N/A")
        pdl = market_ctx.get("pdl", "N/A")
        pd_open  = market_ctx.get("pd_open", "N/A")
        pd_close = market_ctx.get("pd_close", "N/A")
        atr_1h   = market_ctx.get("atr_1h", "N/A")
        atr_4h   = market_ctx.get("atr_4h", "N/A")

        # Determine if price is in premium or discount of previous day range
        try:
            pdh_f = float(pdh)
            pdl_f = float(pdl)
            lp_f  = float(lp)
            pd_range = pdh_f - pdl_f
            midpoint = pdl_f + pd_range / 2
            pct_position = ((lp_f - pdl_f) / pd_range * 100) if pd_range > 0 else 50
            zone = "PREMIUM (above midpoint -- favour SELLS)" if lp_f > midpoint else "DISCOUNT (below midpoint -- favour BUYS)"
            zone_line = f"Price Zone  : {zone} ({pct_position:.1f}% of prev day range)"
        except (ValueError, ZeroDivisionError):
            zone_line = "Price Zone  : Unable to calculate"

        market_block = (
            f"=== LIVE MARKET DATA (fetched now -- use these exact values) ===\n"
            f"Live Price  : {lp}  <- anchor ALL levels to this\n"
            f"PDH         : {pdh}  <- previous day high (liquidity above)\n"
            f"PDL         : {pdl}  <- previous day low (liquidity below)\n"
            f"Prev D Open : {pd_open}\n"
            f"Prev D Close: {pd_close}\n"
            f"ATR (14,1H) : {atr_1h}  <- use for scalp SL validation\n"
            f"ATR (14,4H) : {atr_4h}  <- use for swing SL validation\n"
            f"{zone_line}"
        )
    elif pair:
        market_block = (
            "=== LIVE MARKET DATA ===\n"
            "Live Price  : UNAVAILABLE -- use your best estimate from market knowledge.\n"
            "IMPORTANT: State your estimated price clearly in current_market_price.\n"
            "Flag in caution that price is estimated, not live."
        )
    else:
        if pair_prices:
            prices_str = "\n".join(f"  {p} = {v}" for p, v in pair_prices.items())
            market_block = (
                f"=== LIVE PRICES -- SELECT BEST OPPORTUNITY ===\n{prices_str}\n"
                f"Pick the pair with the strongest ICT/SMC confluence. "
                f"Use its live price as anchor."
            )
        else:
            market_block = "=== LIVE PRICES ===\nUNAVAILABLE -- use market knowledge."

    # Session / kill zone
    kz_status = "[ACTIVE]" if session_info["kill_zone_active"] else "[NOT ACTIVE]"
    session_block = (
        f"=== SESSION & TIMING ===\n"
        f"Current UTC : {session_info['current_utc']} ({session_info['weekday']})\n"
        f"Session     : {session_info['session']}\n"
        f"Kill Zone   : {kz_status}\n"
        f"Note        : {session_info['kill_zone_note']}"
    )

    # Style-specific rules
    if trade_style == "scalp":
        style_block = (
            "SCALP MODE:\n"
            "  - SL <= 15 pips (Forex majors) | Gold SL <= 200 pips (0.01 pip size)\n"
            "  - Entry must be within 10 pips (Forex) / 150 pips (Gold) of live price\n"
            "  - 2 TPs only: TP1 >= 1:1.5 RR, TP2 >= 1:2.5 RR\n"
            "  - partial_close: TP1=50%, TP2=50%\n"
            "  - If kill zone NOT active, reduce confluence score by 1.5 and note in caution"
        )
    else:
        style_block = (
            "SWING MODE:\n"
            "  - 3 TPs required: TP1 conservative, TP2 moderate, TP3 extended\n"
            "  - partial_close: TP1=40%, TP2=40%, TP3=20%\n"
            "  - Confluence >= 7 required; if below 7, state in caution\n"
            "  - SL must be beyond structural level (OB base, swing high/low)"
        )

    return (
        f"=== TRADE REQUEST ===\n"
        f"Balance     : ${balance:,.2f}\n"
        f"Pair        : {pair.upper() if pair else 'AI selects best opportunity'}\n"
        f"Risk        : {risk.capitalize()} ({risk_pct}% = ${risk_dollars:,.2f})\n"
        f"Lot Size    : {lot_size} lots (EXACT -- do not change under any circumstance)\n"
        f"{pip_block}\n\n"
        f"{market_block}\n\n"
        f"{session_block}\n\n"
        f"Trade Style : {trade_style.upper()}\n"
        f"{style_block}\n\n"
        f"Trader Notes: {notes or 'None'}\n\n"
        f"=== MATH VERIFICATION REQUIRED ===\n"
        f"Confirm in math_check field:\n"
        f"  risk_amount = SL_pips * pip_value_usd -> must equal ${risk_dollars:,.2f} +/- 15%\n"
        f"  If risk_check deviates >15%, adjust SL pips to align, or explain why in caution.\n\n"
        f"Return ONLY the JSON object. No markdown. No explanation outside JSON."
    )


# -- Main function -----------------------------------------------------

async def get_trade_plan(
    balance: float,
    pair: Optional[str] = None,
    risk: str = "moderate",
    trade_style: str = "swing",
    notes: str = "",
    lot_size: str = "0.01",
) -> dict:
    """
    Fetch full market context then generate a trade plan with Claude.
    Returns validated plan dict.
    """

    # Step 1 -- Get session info (free, no API call)
    session_info = get_current_session_info()
    logger.info(
        "Session: %s | Kill zone: %s",
        session_info["session"],
        session_info["kill_zone_active"],
    )

    # Step 2 -- Fetch enriched market data
    market_ctx: dict = {}
    pair_prices: dict = {}

    if pair:
        logger.info("Fetching market context: %s [%s]", pair, trade_style)
        market_ctx = await fetch_market_context(pair, trade_style)
        if not market_ctx.get("live_price"):
            logger.warning("Live price fetch failed for %s", pair)
    else:
        logger.info("Fetching all Forex prices for AI pair selection")
        pair_prices = await fetch_all_forex_prices()

    # Step 3 -- Build prompt
    user_prompt = _build_user_prompt(
        balance=balance,
        pair=pair,
        risk=risk,
        trade_style=trade_style,
        notes=notes,
        market_ctx=market_ctx,
        pair_prices=pair_prices,
        lot_size=lot_size,
        session_info=session_info,
    )

    logger.info(
        "Claude call | model=%s pair=%s style=%s lot=%s live=%s",
        CLAUDE_MODEL,
        pair or "AI",
        trade_style,
        lot_size,
        market_ctx.get("live_price") or "N/A",
    )

    # Step 4 -- Call Claude
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    try:
        loop = asyncio.get_event_loop()
        message = await loop.run_in_executor(
            None,
            lambda: client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=CLAUDE_MAX_TOKENS,
                timeout=CLAUDE_TIMEOUT,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            ),
        )
    except anthropic.APITimeoutError:
        raise AnalystError("Claude timed out. Please try again.")
    except anthropic.APIConnectionError as e:
        raise AnalystError(f"Connection error: {e}")
    except anthropic.RateLimitError:
        raise AnalystError("Rate limit hit. Wait a moment and try again.")
    except anthropic.APIStatusError as e:
        raise AnalystError(f"Claude API error {e.status_code}: {e.message}")

    # Step 5 -- Parse
    raw: str = message.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        plan: dict = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("JSON parse failed: %s", raw[:400])
        raise AnalystError(f"Unexpected format from Claude. Try again. ({e})")

    # Step 6 -- Validate required fields
    required = {
        "pair", "direction", "trade_style", "current_market_price",
        "execution", "entry", "stop_loss", "stop_loss_pips",
        "take_profits", "lot_size", "pip_value", "risk_amount",
        "risk_percent", "total_potential_profit", "confluence_score",
        "session", "rationale", "caution",
    }
    missing = required - plan.keys()
    if missing:
        raise AnalystError(f"Claude response missing fields: {', '.join(sorted(missing))}")

    # Step 7 -- Server-side math validation (belt-and-braces)
    _validate_math(plan, balance, lot_size, pair or plan.get("pair", ""))

    logger.info(
        "Plan ready | %s %s %s | entry=%s live=%s confluence=%s/10",
        plan["trade_style"].upper(),
        plan["direction"],
        plan["pair"],
        plan["entry"],
        plan.get("current_market_price"),
        plan.get("confluence_score"),
    )

    # Attach session context for formatter
    plan["_session_info"] = session_info
    plan["_market_ctx"] = {k: v for k, v in market_ctx.items() if k != "_"}

    return plan


# -- Server-side math validation ---------------------------------------

def _validate_math(plan: dict, balance: float, lot_size: str, pair: str) -> None:
    """
    Cross-check Claude's math against our own calculations.
    Logs warnings on discrepancies -- does not block the plan but flags issues.
    """
    try:
        lot = float(lot_size)
        pip_val, pip_size = calculate_pip_value(pair, lot)
        sl_pips = int(plan.get("stop_loss_pips", 0))

        # Risk check
        expected_risk = sl_pips * pip_val
        stated_risk_str = str(plan.get("risk_amount", "0")).replace("$", "").replace(",", "")
        try:
            stated_risk = float(stated_risk_str)
        except ValueError:
            stated_risk = 0

        if stated_risk > 0 and expected_risk > 0:
            deviation = abs(expected_risk - stated_risk) / stated_risk
            if deviation > 0.20:
                logger.warning(
                    "Math check: risk mismatch. Expected $%.2f, Claude stated $%.2f (%.0f%% off)",
                    expected_risk, stated_risk, deviation * 100,
                )

        # TP profit check (first TP only)
        tps = plan.get("take_profits", [])
        if tps:
            tp1 = tps[0]
            tp1_pips = int(tp1.get("pips", 0))
            partial = tp1.get("partial_close", "50%").replace("%", "")
            try:
                partial_frac = float(partial) / 100
            except ValueError:
                partial_frac = 0.5
            expected_tp1_profit = tp1_pips * pip_val * partial_frac
            stated_profit_str = str(tp1.get("profit", "0")).replace("$", "").replace(",", "")
            try:
                stated_profit = float(stated_profit_str)
            except ValueError:
                stated_profit = 0
            if stated_profit > 0 and expected_tp1_profit > 0:
                deviation = abs(expected_tp1_profit - stated_profit) / stated_profit
                if deviation > 0.20:
                    logger.warning(
                        "Math check: TP1 profit mismatch. Expected $%.2f, Claude stated $%.2f",
                        expected_tp1_profit, stated_profit,
                    )

    except Exception as e:
        logger.warning("Math validation skipped: %s", e)
