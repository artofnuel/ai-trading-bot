"""
market/prices.py — Forex-only live market data fetcher via Twelve Data.

Per analysis, fetches concurrently:
  1. Live price      — anchor for all levels
  2. PDH / PDL       — previous day high/low (ICT liquidity targets)
  3. ATR (14, 1H)    — volatility baseline for SL sizing

Crypto removed entirely.
"""

import asyncio
import logging
from typing import Optional

import httpx

from config import TWELVE_DATA_BASE, TWELVE_DATA_API_KEY

logger = logging.getLogger(__name__)

FOREX_PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY", "GBP/JPY", "XAU/USD", "XAG/USD"]


# ── Internal helpers ──────────────────────────────────────────────────────────

def _normalise(pair: str) -> str:
    """Ensure slash-format. XAUUSD → XAU/USD."""
    pair = pair.upper().replace(" ", "")
    if "/" in pair:
        return pair
    if len(pair) == 6:
        return pair[:3] + "/" + pair[3:]
    if len(pair) == 7:
        return pair[:4] + "/" + pair[4:]
    return pair


def _fmt(price: float, pair: str) -> str:
    """Format price to correct decimal places for each instrument."""
    p = pair.upper()
    if "JPY" in p:
        return f"{price:.3f}"
    if "XAU" in p:
        return f"{price:.2f}"
    if "XAG" in p:
        return f"{price:.3f}"
    if price >= 100:
        return f"{price:.3f}"
    return f"{price:.5f}"


async def _get(endpoint: str, params: dict) -> Optional[dict]:
    """Shared Twelve Data GET with error handling."""
    params["apikey"] = TWELVE_DATA_API_KEY
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{TWELVE_DATA_BASE}/{endpoint}", params=params)
            r.raise_for_status()
            data = r.json()
            if data.get("status") == "error":
                logger.warning("TD error [%s]: %s", endpoint, data.get("message"))
                return None
            return data
    except httpx.TimeoutException:
        logger.warning("TD timeout [%s %s]", endpoint, params.get("symbol"))
    except Exception as e:
        logger.warning("TD error [%s]: %s", endpoint, e)
    return None


# ── Public fetchers ───────────────────────────────────────────────────────────

async def fetch_live_price(pair: str) -> Optional[str]:
    """Real-time price for any Forex/metal pair."""
    symbol = _normalise(pair)
    data = await _get("price", {"symbol": symbol})
    if not data or not data.get("price"):
        return None
    p = float(data["price"])
    result = _fmt(p, pair)
    logger.info("Live | %s = %s", symbol, result)
    return result


async def fetch_pdh_pdl(pair: str) -> Optional[dict]:
    """
    Previous complete daily candle OHLC.
    PDH and PDL are primary ICT liquidity targets.
    Returns: {date, open, high, low, close} all formatted strings.
    """
    symbol = _normalise(pair)
    data = await _get("time_series", {
        "symbol": symbol,
        "interval": "1day",
        "outputsize": 2,
        "order": "DESC",
    })
    if not data:
        return None
    values = data.get("values", [])
    if len(values) < 2:
        return None
    prev = values[1]   # values[0] = today (incomplete)
    try:
        return {
            "date":  prev.get("datetime", ""),
            "open":  _fmt(float(prev["open"]),  pair),
            "high":  _fmt(float(prev["high"]),  pair),
            "low":   _fmt(float(prev["low"]),   pair),
            "close": _fmt(float(prev["close"]), pair),
        }
    except (KeyError, ValueError) as e:
        logger.warning("PDH/PDL parse error %s: %s", symbol, e)
        return None


async def fetch_atr(pair: str, period: int = 14, interval: str = "1h") -> Optional[str]:
    """
    14-period ATR on 1H. Gives Claude real volatility context for SL sizing.
    """
    symbol = _normalise(pair)
    data = await _get("atr", {
        "symbol": symbol,
        "interval": interval,
        "time_period": period,
        "outputsize": 1,
    })
    if not data:
        return None
    values = data.get("values", [])
    if not values:
        return None
    try:
        val = float(values[0]["atr"])
        result = _fmt(val, pair)
        logger.info("ATR(%d,%s) | %s = %s", period, interval, symbol, result)
        return result
    except (KeyError, ValueError) as e:
        logger.warning("ATR parse error %s: %s", symbol, e)
        return None


async def fetch_market_context(pair: str) -> dict:
    """
    Fetch live price, PDH/PDL, and ATR concurrently for a single pair.
    Returns a dict with all context needed to build the Claude prompt.
    """
    live, ohlc, atr = await asyncio.gather(
        fetch_live_price(pair),
        fetch_pdh_pdl(pair),
        fetch_atr(pair),
        return_exceptions=True,
    )

    # Exceptions become None — never crash the analysis
    if isinstance(live, Exception):
        live = None
    if isinstance(ohlc, Exception):
        ohlc = None
    if isinstance(atr, Exception):
        atr = None

    ctx = {
        "live_price": live,
        "pdh":        ohlc["high"]  if ohlc else None,
        "pdl":        ohlc["low"]   if ohlc else None,
        "pd_open":    ohlc["open"]  if ohlc else None,
        "pd_close":   ohlc["close"] if ohlc else None,
        "atr_1h":     atr,
    }
    logger.info(
        "Context | %s | price=%s PDH=%s PDL=%s ATR=%s",
        _normalise(pair), ctx["live_price"], ctx["pdh"], ctx["pdl"], ctx["atr_1h"],
    )
    return ctx


async def fetch_all_forex_prices() -> dict:
    """
    Live prices for all standard pairs concurrently.
    Used when AI selects best opportunity (no pair specified).
    """
    results = await asyncio.gather(
        *[fetch_live_price(p) for p in FOREX_PAIRS],
        return_exceptions=True,
    )
    return {
        pair: price
        for pair, price in zip(FOREX_PAIRS, results)
        if isinstance(price, str)
    }
