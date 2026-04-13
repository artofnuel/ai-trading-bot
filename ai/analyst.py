"""
ai/analyst.py — Claude AI integration for generating trade plans.

Always fetches live market prices before calling Claude so that
all entry, SL, TP, and execution type values are anchored to
real current market conditions — never estimated from training data.
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


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are an elite forex and crypto trade analyst with deep expertise in Smart Money \
Concepts (SMC), ICT methodology, technical analysis, and risk management.

You will ALWAYS be given the current live market price. All trade levels you generate \
MUST be anchored to this real price. Never invent or estimate prices — use only what \
is provided as your starting point.

Return STRICT JSON only — no markdown, no preamble, no text outside the JSON object.

════════════════════════════════════════
EXECUTION TYPE RULES — NON-NEGOTIABLE
════════════════════════════════════════
Always derive the order type from the relationship between
current live price and your chosen entry price.

SELL trades:
  - Live price BELOW entry → price must rise to entry → "Sell Limit"
  - Live price ABOVE entry → price must fall to entry → "Sell Stop"
  - Live price AT entry    → "Sell Market"

BUY trades:
  - Live price ABOVE entry → price must fall to entry → "Buy Limit"
  - Live price BELOW entry → price must rise to entry → "Buy Stop"
  - Live price AT entry    → "Buy Market"

Never guess. Always calculate from the live price provided.

════════════════════════════════════════
LOT SIZE RULES — NON-NEGOTIABLE
════════════════════════════════════════
Minimum lot size is 0.01. Never return below 0.01.

Steps:
1. Calculate: lot = Risk Amount ÷ (SL pips × pip value per standard lot)
2. If lot < 0.01, round UP to 0.01
3. Recalculate actual risk: actual_risk = SL pips × pip value × 0.01
4. Recalculate actual risk %: (actual_risk ÷ balance) × 100
5. Return actual values — not intended ones
6. Set minimum_lot_warning: true if rounding occurred

Pip values per standard lot:
  - USD pairs (EUR/USD, GBP/USD): $10/pip → $0.10/pip at 0.01 lot
  - JPY pairs (USD/JPY, GBP/JPY): ~$7–9/pip → ~$0.07–0.09/pip at 0.01 lot
  - Crypto: size in base currency units, minimum 0.001

════════════════════════════════════════
PROFIT CALCULATIONS
════════════════════════════════════════
- estimated_profit_at_tp1: profit from closing TP1 partial % only
- estimated_profit_at_tp2: profit from closing TP2 partial % only
- estimated_profit_at_tp3: profit from closing TP3 partial % only
- total_potential_profit: sum of all three

All profit values must use the actual lot size after minimum enforcement.

════════════════════════════════════════
MT5 SETUP
════════════════════════════════════════
- Include mt5_setup for Forex only. Set null for Crypto.
- symbol: remove slash (EUR/USD → EURUSD)
- order_type must match execution field exactly
- volume = lot_size
- tp = TP1 price only (trader manages TP2/TP3 manually)

════════════════════════════════════════
REQUIRED JSON SCHEMA
════════════════════════════════════════
{
  "pair": "EUR/USD",
  "direction": "BUY",
  "current_market_price": "1.08231",
  "execution": "Buy Limit",
  "entry": "1.07950",
  "stop_loss": "1.07450",
  "stop_loss_pips": 50,
  "lot_size": "0.02",
  "pip_value": "$0.10 per pip",
  "minimum_lot_warning": false,
  "risk_amount": "$10.00",
  "risk_percent": 2,
  "take_profits": [
    {"label": "TP1", "price": "1.08450", "pips": 50,  "rr": "1:1",   "partial_close": "40%"},
    {"label": "TP2", "price": "1.08950", "pips": 100, "rr": "1:2",   "partial_close": "30%"},
    {"label": "TP3", "price": "1.09700", "pips": 175, "rr": "1:3.5", "partial_close": "30%"}
  ],
  "estimated_profit_at_tp1": "$4.00",
  "estimated_profit_at_tp2": "$6.00",
  "estimated_profit_at_tp3": "$10.50",
  "total_potential_profit": "$20.50",
  "trailing_stop": {
    "recommended": true,
    "activate_at": "TP1",
    "trail_distance": "30 pips",
    "rationale": "Activate after TP1 hit to lock in profit while trade runs toward TP2/TP3."
  },
  "confluence_score": 8,
  "session": "London Open",
  "mt5_setup": {
    "symbol": "EURUSD",
    "order_type": "Buy Limit",
    "volume": "0.02",
    "price": "1.07950",
    "sl": "1.07450",
    "tp": "1.08450",
    "comment": "AI-TradeBot TP1",
    "note": "For TP2 and TP3, place additional pending orders at same entry with reduced volume. Or set TP1 only and move SL to entry after TP1 hits."
  },
  "rationale": "Detailed multi-sentence analysis covering market structure, SMC reasoning, and why this setup is valid right now based on the live price provided.",
  "caution": "Specific invalidation level or risk the trader must watch."
}
"""


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_user_prompt(
    balance: float,
    market: str,
    pair: Optional[str],
    risk: str,
    notes: str,
    live_price: Optional[str],
    pair_prices: Optional[dict],
) -> str:
    risk_map = {"conservative": 1, "moderate": 2, "aggressive": 3}
    risk_pct = risk_map.get(risk.lower(), 2)
    risk_amount = balance * risk_pct / 100

    # ── Pair and price section ────────────────────────────────
    if pair:
        pair_line = f"Trading Pair    : {pair.upper()}"
        if live_price:
            price_section = (
                f"Current Live Price : {live_price}\n"
                f"⚠️  This is the REAL current market price fetched right now.\n"
                f"    All levels MUST be calculated relative to this price.\n"
                f"    Derive execution type from: live price vs entry relationship."
            )
        else:
            price_section = (
                "Current Live Price : UNAVAILABLE (API fetch failed)\n"
                "⚠️  Use your best current market knowledge to estimate price.\n"
                "    Clearly state your estimated price in current_market_price field.\n"
                "    Flag this uncertainty in the caution field."
            )
    else:
        pair_line = "Trading Pair    : AI selects best opportunity"
        if pair_prices:
            prices_formatted = "\n".join(
                f"    {p:<12}: {v}" for p, v in pair_prices.items()
            )
            price_section = (
                f"Live Prices (fetched RIGHT NOW):\n"
                f"{prices_formatted}\n"
                f"Select the pair with the strongest current setup.\n"
                f"Use the live price of your selected pair as the anchor for all levels."
            )
        else:
            price_section = (
                "Current Prices  : UNAVAILABLE (API fetch failed)\n"
                "Use your best market knowledge. State estimated price in current_market_price."
            )

    return f"""\
Generate a trade plan for the following:

Account Balance : ${balance:,.2f}
Market          : {market}
{pair_line}
Risk Appetite   : {risk.capitalize()} ({risk_pct}% = ${risk_amount:,.2f} at risk)
Additional Notes: {notes or 'None'}

{price_section}

Return ONLY the JSON object. No markdown, no extra text.
"""


# ── Main function ─────────────────────────────────────────────────────────────

async def get_trade_plan(
    balance: float,
    market: str,
    pair: Optional[str] = None,
    risk: str = "moderate",
    notes: str = "",
) -> tuple[dict, bool]:
    """
    Fetch live market price then call Claude to generate
    a trade plan anchored to real current market conditions.

    Returns:
        (plan_dict, price_ok_bool)
    """

    # ── Step 1: Fetch live price(s) ───────────────────────────
    live_price: Optional[str] = None
    pair_prices: dict = {}
    price_ok = False

    if pair:
        logger.info("Fetching live price for %s...", pair)
        live_price = await fetch_live_price(pair)
        if live_price:
            logger.info("Live price confirmed: %s = %s", pair, live_price)
            price_ok = True
        else:
            logger.warning(
                "Live price unavailable for %s — Claude will estimate. "
                "Check API keys and connectivity.", pair
            )
    else:
        logger.info("No pair specified — fetching all %s prices...", market)
        pair_prices = await fetch_all_prices(market)
        if pair_prices:
            logger.info("Live prices ready: %s", pair_prices)
            price_ok = True
        else:
            logger.warning("No live prices available — Claude will estimate all levels.")

    # ── Step 2: Build prompt ──────────────────────────────────
    user_prompt = _build_user_prompt(
        balance=balance,
        market=market,
        pair=pair,
        risk=risk,
        notes=notes,
        live_price=live_price,
        pair_prices=pair_prices,
    )

    logger.info(
        "Calling Claude | model=%s market=%s pair=%s risk=%s balance=%.2f live_price=%s",
        CLAUDE_MODEL, market, pair, risk, balance, live_price or "N/A"
    )

    # ── Step 3: Call Claude (in executor — SDK is synchronous) ─
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
        raise AnalystError("Claude timed out. Please try again in a moment.")
    except anthropic.APIConnectionError as e:
        raise AnalystError(f"Connection error reaching Claude: {e}")
    except anthropic.RateLimitError:
        raise AnalystError("Rate limit reached. Please wait a moment and try again.")
    except anthropic.APIStatusError as e:
        raise AnalystError(f"Claude API error {e.status_code}: {e.message}")

    # ── Step 4: Parse response ────────────────────────────────
    raw: str = message.content[0].text.strip()
    logger.debug("Claude raw response (first 400 chars): %s", raw[:400])

    # Strip accidental markdown fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        plan: dict = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("JSON parse failed. Raw: %s", raw[:500])
        raise AnalystError(
            f"Claude returned an unexpected format. Please try again. (Detail: {e})"
        )

    # ── Step 5: Validate required fields ─────────────────────
    required_keys = {
        "pair", "direction", "current_market_price", "execution",
        "entry", "stop_loss", "take_profits", "rationale",
        "lot_size", "total_potential_profit", "minimum_lot_warning",
    }
    missing = required_keys - plan.keys()
    if missing:
        raise AnalystError(
            f"Claude response missing required fields: {', '.join(missing)}"
        )

    logger.info(
        "Trade plan ready | %s %s | entry=%s | live_price=%s | lot=%s",
        plan["direction"], plan["pair"],
        plan["entry"], plan.get("current_market_price"),
        plan.get("lot_size"),
    )
    return plan, price_ok
