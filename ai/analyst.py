"""
ai/analyst.py — Claude AI integration for generating trade plans.

Builds a structured prompt, calls the Anthropic API, and returns a
validated trade plan dict. All API errors are caught and re-raised as
a descriptive AnalystError so callers can respond gracefully.
"""

import asyncio
import json
import logging
import re
from typing import Optional

from anthropic import (
    AsyncAnthropic,
    APITimeoutError,
    APIConnectionError,
    RateLimitError,
    APIStatusError,
)

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_MAX_TOKENS, CLAUDE_TIMEOUT

logger = logging.getLogger(__name__)

# ── Custom Exception ──────────────────────────────────────────────────────────

class AnalystError(Exception):
    """Raised when the AI analyst cannot produce a valid trade plan."""


# ── Prompt builder ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are an elite forex and crypto trade analyst with deep knowledge of technical analysis, \
Smart Money Concepts (SMC), ICT methodology, and risk management.

Your job is to produce STRICT JSON trade plans — no markdown, no preamble, no explanation \
outside the JSON object. The response must be parseable directly by json.loads().

Use your knowledge of current market conditions and price structure to give realistic price \
levels. If no pair is specified, recommend the highest-probability setup across:
- Forex majors: EUR/USD, GBP/USD, USD/JPY, GBP/JPY, XAU/USD
- Crypto: BTC/USDT, ETH/USDT

The JSON schema you MUST follow exactly:
{
  "pair": "EUR/USD",
  "direction": "BUY" | "SELL",
  "execution": "Market Order" | "Limit Order",
  "entry": "<price as string>",
  "stop_loss": "<price as string>",
  "stop_loss_pips": <integer>,
  "risk_amount": "$<amount>",
  "risk_percent": <1 | 2 | 3>,
  "market_trend": "<bullish | bearish | neutral>",
  "take_profits": [
    {"label": "TP1", "price": "<string>", "pips": <int>, "rr": "1:X", "partial_close": "<percent>"},
    {"label": "TP2", "price": "<string>", "pips": <int>, "rr": "1:X", "partial_close": "<percent>"},
    {"label": "TP3", "price": "<string>", "pips": <int>, "rr": "1:X", "partial_close": "<percent>"}
  ],
  "trailing_stop": {
    "recommended": <true | false>,
    "activate_at": "TP1",
    "trail_distance": "<N> pips",
    "rationale": "<string>"
  },
  "estimated_profit_at_tp2": "$<amount>",
  "estimated_profit_at_tp3": "$<amount>",
  "confluence_score": <1-10>,
  "session": "<London Open | New York Open | Asian Session | etc.>",
  "rationale": "<multi-sentence analysis>",
  "caution": "<key risk or invalidation note>"
}
"""


def _build_user_prompt(
    balance: float,
    market: str,
    pair: Optional[str],
    risk: str,
    notes: str,
) -> str:
    """Compose the user-facing part of the prompt sent to Claude."""
    risk_map = {"conservative": 1, "moderate": 2, "aggressive": 3}
    risk_pct = risk_map.get(risk.lower(), 2)

    pair_instruction = (
        f"Trading Pair  : {pair.upper()}"
        if pair
        else "Trading Pair  : [You choose — recommend the best opportunity right now]"
    )

    return f"""\
Generate a trade plan with the following parameters:

Account Balance : ${balance:,.2f}
Market          : {market}
{pair_instruction}
Risk Appetite   : {risk.capitalize()} ({risk_pct}% of account = ${balance * risk_pct / 100:,.2f} at risk)
Additional Notes: {notes or 'None'}

Return ONLY the JSON object. No markdown, no extra text.
"""


# ── Main analyst function ─────────────────────────────────────────────────────

async def get_trade_plan(
    balance: float,
    market: str,
    pair: Optional[str] = None,
    risk: str = "moderate",
    notes: str = "",
) -> dict:
    """
    Call Claude to generate a trade plan and return the parsed dict.

    Raises:
        AnalystError — on API failure, timeout, or invalid JSON response.
    """
    async with AsyncAnthropic(api_key=ANTHROPIC_API_KEY) as client:
        user_prompt = _build_user_prompt(balance, market, pair, risk, notes)

        logger.info(
            "Requesting trade plan | market=%s pair=%s risk=%s balance=%.2f",
            market, pair, risk, balance,
        )

        try:
            message = await client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=CLAUDE_MAX_TOKENS,
                timeout=CLAUDE_TIMEOUT,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except APITimeoutError:
            raise AnalystError("Claude API timed out. Please try again in a moment.")
        except APIConnectionError as exc:
            raise AnalystError(f"Connection error reaching Claude: {exc}")
        except RateLimitError:
            raise AnalystError("Rate limit reached. Please wait a moment and try again.")
        except APIStatusError as exc:
            raise AnalystError(f"Claude API error {exc.status_code}: {exc.message}")

    # Extract raw text from the response
    raw_text: str = message.content[0].text.strip()
    logger.debug("Claude raw response: %s", raw_text[:400])

    # Strip any accidental markdown code fences Claude might have added
    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
    raw_text = re.sub(r"\s*```$", "", raw_text)

    # Parse and validate JSON
    try:
        plan: dict = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse Claude response as JSON: %s", raw_text[:500])
        raise AnalystError(
            f"Claude returned an unexpected format. Please try again. (Detail: {exc})"
        )

    # Minimal sanity check: ensure required keys exist
    required_keys = {"pair", "direction", "entry", "stop_loss", "take_profits", "rationale"}
    missing = required_keys - plan.keys()
    if missing:
        raise AnalystError(
            f"Claude response is missing required fields: {', '.join(missing)}"
        )

    logger.info("Trade plan generated successfully for %s %s", plan["direction"], plan["pair"])
    return plan
