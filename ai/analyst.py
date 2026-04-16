"""
ai/analyst.py — Claude AI integration for generating trade plans.

Fetches live market prices before calling Claude so all levels
are anchored to real current market conditions.

Trade styles supported:
  - swing  : multi-session trades, larger TP targets
  - scalp  : intra-session trades, tight SL/TP, quick R:R
"""

import asyncio
import json
import logging
import re
from typing import Optional

import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_MAX_TOKENS, CLAUDE_TIMEOUT
from market.prices import fetch_live_price, fetch_all_prices

logger = logging.getLogger(__name__)


# ── Custom exception ──────────────────────────────────────────────────────────

class AnalystError(Exception):
    """Raised when the AI analyst cannot produce a valid trade plan."""


# ── System prompt — lean and non-redundant ────────────────────────────────────

SYSTEM_PROMPT = """\
You are an elite Forex and Crypto trade analyst specialising in Smart Money Concepts \
(SMC), ICT methodology, and disciplined risk management.

Return STRICT JSON only — no markdown, no explanation outside the JSON object.

EXECUTION TYPE — derive from live price vs entry:
  BUY:  live > entry → Buy Limit | live < entry → Buy Stop | live = entry → Buy Market
  SELL: live < entry → Sell Limit | live > entry → Sell Stop | live = entry → Sell Market

LOT SIZE — use EXACTLY what the trader specifies. Do not change it.
  Pip values per standard lot: USD pairs $10/pip | JPY pairs ~$8/pip | XAU/USD $10/pip per 0.1 lot
  Crypto PnL = price_difference × lot_size
  All dollar figures must be mathematically exact. No approximations.

TRADE STYLE:
  swing : 3 TPs, wider SL, multi-session outlook, confluence score ≥ 7
  scalp : 2 TPs only, SL ≤ 15 pips (Forex) or ≤ 0.3% (Crypto), quick R:R ≥ 1:1.5, \
entry near current price, best during active session killzones

JSON schema (follow exactly):
{
  "pair": "XAU/USD",
  "direction": "BUY",
  "trade_style": "scalp",
  "current_market_price": "2345.50",
  "execution": "Buy Stop",
  "entry": "2346.00",
  "stop_loss": "2343.50",
  "stop_loss_pips": 25,
  "lot_size": "0.01",
  "pip_value": "$0.10 per pip",
  "risk_amount": "$2.50",
  "risk_percent": 2.50,
  "take_profits": [
    {"label": "TP1", "price": "2349.50", "pips": 35, "rr": "1:1.4", "partial_close": "50%"},
    {"label": "TP2", "price": "2353.00", "pips": 70, "rr": "1:2.8", "partial_close": "50%"}
  ],
  "estimated_profit_at_tp1": "$1.75",
  "estimated_profit_at_tp2": "$3.50",
  "total_potential_profit": "$5.25",
  "trailing_stop": {
    "recommended": true,
    "activate_at": "TP1",
    "trail_distance": "15 pips",
    "rationale": "Trail after TP1 to protect profit while targeting TP2."
  },
  "confluence_score": 8,
  "session": "London Open",
  "rationale": "Concise 2-3 sentence SMC/ICT analysis explaining the setup based on the live price.",
  "caution": "One sentence — specific invalidation level or key risk."
}

For swing trades include 3 take_profits. For scalp trades include 2 take_profits only.
"""


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_user_prompt(
    balance: float,
    market: str,
    pair: Optional[str],
    risk: str,
    trade_style: str,
    notes: str,
    live_price: Optional[str],
    pair_prices: Optional[dict],
    lot_size: str,
) -> str:
    risk_map = {"conservative": 1, "moderate": 2, "aggressive": 3}
    risk_pct = risk_map.get(risk.lower(), 2)
    lot = float(lot_size)
    pip_val = lot * 10  # USD pairs baseline

    # Price block
    if pair:
        pair_line = f"Pair     : {pair.upper()}"
        if live_price:
            price_block = (
                f"Live Price : {live_price} ← REAL price fetched now. "
                f"Anchor ALL levels to this. Derive execution from live vs entry."
            )
        else:
            price_block = (
                "Live Price : UNAVAILABLE — estimate from market knowledge. "
                "State estimate in current_market_price. Flag in caution."
            )
    else:
        pair_line = "Pair     : AI selects best opportunity"
        if pair_prices:
            prices_str = " | ".join(f"{p}={v}" for p, v in pair_prices.items())
            price_block = (
                f"Live Prices: {prices_str}\n"
                f"Pick the strongest setup. Use chosen pair's live price as anchor."
            )
        else:
            price_block = "Live Prices: UNAVAILABLE — use market knowledge."

    scalp_note = (
        "\nSCALP MODE: Keep SL ≤ 15 pips (Forex) / ≤ 0.3% (Crypto). "
        "Entry must be within 10 pips of live price. 2 TPs only. Fast setup."
        if trade_style == "scalp" else ""
    )

    return (
        f"Balance  : ${balance:,.2f}\n"
        f"Market   : {market}\n"
        f"{pair_line}\n"
        f"Risk     : {risk.capitalize()} ({risk_pct}% = ${balance * risk_pct / 100:,.2f})\n"
        f"Lot Size : {lot_size} (exact — do not change)\n"
        f"Pip Val  : ~${pip_val:.3f}/pip at this lot size (adjust for pair type)\n"
        f"Style    : {trade_style.upper()}{scalp_note}\n"
        f"Notes    : {notes or 'None'}\n\n"
        f"{price_block}\n\n"
        f"Return ONLY the JSON object."
    )


# ── Main function ─────────────────────────────────────────────────────────────

async def get_trade_plan(
    balance: float,
    market: str,
    pair: Optional[str] = None,
    risk: str = "moderate",
    trade_style: str = "swing",
    notes: str = "",
    lot_size: str = "0.01",
) -> dict:
    """
    Fetch live price then generate a trade plan with Claude.
    trade_style: 'swing' or 'scalp'
    """

    # Step 1 — Fetch live price(s)
    live_price: Optional[str] = None
    pair_prices: dict = {}

    if pair:
        logger.info("Fetching live price: %s", pair)
        live_price = await fetch_live_price(pair)
        if live_price:
            logger.info("Live price: %s = %s", pair, live_price)
        else:
            logger.warning("Price fetch failed for %s — Claude will estimate", pair)
    else:
        logger.info("Fetching all %s prices for AI selection", market)
        pair_prices = await fetch_all_prices(market)
        logger.info("Prices: %s", pair_prices)

    # Step 2 — Build prompt
    user_prompt = _build_user_prompt(
        balance=balance,
        market=market,
        pair=pair,
        risk=risk,
        trade_style=trade_style,
        notes=notes,
        live_price=live_price,
        pair_prices=pair_prices,
        lot_size=lot_size,
    )

    logger.info(
        "Claude call | model=%s pair=%s style=%s lot=%s live=%s",
        CLAUDE_MODEL, pair or "AI", trade_style, lot_size, live_price or "N/A"
    )

    # Step 3 — Call Claude (executor — SDK is sync)
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

    # Step 4 — Parse
    raw: str = message.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        plan: dict = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("JSON parse failed: %s", raw[:400])
        raise AnalystError(f"Unexpected format from Claude. Try again. ({e})")

    # Step 5 — Validate
    required = {
        "pair", "direction", "trade_style", "current_market_price",
        "execution", "entry", "stop_loss", "take_profits",
        "lot_size", "risk_amount", "total_potential_profit", "rationale",
    }
    missing = required - plan.keys()
    if missing:
        raise AnalystError(f"Claude response missing: {', '.join(missing)}")

    logger.info(
        "Plan ready | %s %s %s | entry=%s live=%s lot=%s",
        plan["trade_style"].upper(), plan["direction"], plan["pair"],
        plan["entry"], plan.get("current_market_price"), plan.get("lot_size"),
    )
    return plan
