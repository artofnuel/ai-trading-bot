"""
market/prices.py — Live Forex market data fetcher using Twelve Data API.

Fetches per analysis request:
  1. Live price         — anchor for all levels
  2. Previous day OHLC  — PDH/PDL are ICT key liquidity targets
  3. 14-period ATR      — objective volatility measure for SL/TP sizing
  4. Bulk prices        — when AI selects best pair (no pair specified)

All Forex pairs go through Twelve Data. Crypto removed entirely.
"""

import asyncio
import logging
from typing import Optional

import httpx

from config import TWELVE_DATA_URL, TWELVE_DATA_API_KEY

logger = logging.getLogger(__name__)

# Standard Forex pairs for AI auto-selection
FOREX_PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY", "GBP/JPY", "XAU/USD", "XAG/USD"]


# ── Formatting helpers ────────────────────────────────────────────────────────

def _format_price(p: float, pair: str) -> str:
    """Format price with correct decimal places per instrument."""
    pair_upper = pair.upper()
    if "JPY" in pair_upper:
        return f"{p:.3f}"           # 157.832
    elif "XAU" in pair_upper:
        return f"{p:.2f}"           # 2345.67
    elif "XAG" in pair_upper:
        return f"{p:.3f}"           # 30.125
    elif p >= 100:
        return f"{p:.3f}"
    else:
        return f"{p:.5f}"           # EUR/USD: 1.08432


# ── Twelve Data helpers ───────────────────────────────────────────────────────

def _normalise_pair(pair: str) -> str:
    """Ensure slash format. XAUUSD → XAU/USD"""
    pair = pair.upper().replace(" ", "")
    if "/" in pair:
        return pair
    if len(pair) == 6:
        return pair[:3] + "/" + pair[3:]
    if len(pair) == 7:
        return pair[:4] + "/" + pair[4:]
    return pair


async def _td_get(endpoint: str, params: dict) -> Optional[dict]:
    """Generic Twelve Data GET with shared timeout and error handling."""
    params["apikey"] = TWELVE_DATA_API_KEY
    url = f"{TWELVE_DATA_URL}/{endpoint}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
            if data.get("status") == "error":
                logger.warning("Twelve Data error [%s %s]: %s", endpoint, params, data.get("message"))
                return None
            return data
    except httpx.TimeoutException:
        logger.warning("Twelve Data timeout [%s %s]", endpoint, params)
    except httpx.HTTPStatusError as e:
        logger.warning("Twelve Data HTTP %s [%s]", e.response.status_code, endpoint)
    except Exception as e:
        logger.exception("Twelve Data unexpected error [%s]: %s", endpoint, e)
    return None


# ── Live price ────────────────────────────────────────────────────────────────

async def fetch_live_price(pair: str) -> Optional[str]:
    """Fetch real-time price for any Forex/metal pair."""
    symbol = _normalise_pair(pair)
    data = await _td_get("price", {"symbol": symbol})
    if not data:
        return None
    raw = data.get("price")
    if not raw:
        logger.warning("No price field for %s: %s", symbol, data)
        return None
    p = float(raw)
    fmt = _format_price(p, pair)
    logger.info("Live price | %s = %s", symbol, fmt)
    return fmt


# ── Previous day OHLC (PDH / PDL) ────────────────────────────────────────────

async def fetch_previous_day_ohlc(pair: str) -> Optional[dict]:
    """
    Fetch the previous completed daily candle OHLC.
    PDH and PDL are primary ICT liquidity targets — critical for swing bias.
    Returns dict with keys: open, high, low, close, date
    """
    symbol = _normalise_pair(pair)
    data = await _td_get("time_series", {
        "symbol": symbol,
        "interval": "1day",
        "outputsize": 2,      # today (incomplete) + yesterday (complete)
        "order": "DESC",
    })
    if not data:
        return None

    values = data.get("values", [])
    if len(values) < 2:
        logger.warning("Not enough daily candles for %s", symbol)
        return None

    # values[0] is today (incomplete), values[1] is previous complete day
    prev = values[1]
    try:
        result = {
            "date": prev.get("datetime", ""),
            "open":  _format_price(float(prev["open"]),  pair),
            "high":  _format_price(float(prev["high"]),  pair),
            "low":   _format_price(float(prev["low"]),   pair),
            "close": _format_price(float(prev["close"]), pair),
        }
        logger.info("PDH/PDL | %s | H=%s L=%s", symbol, result["high"], result["low"])
        return result
    except (KeyError, ValueError) as e:
        logger.warning("OHLC parse error %s: %s", symbol, e)
        return None


# ── ATR (Average True Range) ──────────────────────────────────────────────────

async def fetch_atr(pair: str, period: int = 14, interval: str = "1h") -> Optional[str]:
    """
    Fetch ATR for objective volatility-based SL/TP sizing.
    ATR on 1H gives intraday volatility; on 4H gives swing volatility.
    Claude uses this to set SL that breathes with the market, not arbitrary pips.
    """
    symbol = _normalise_pair(pair)
    data = await _td_get("atr", {
        "symbol": symbol,
        "interval": interval,
        "time_period": period,
        "outputsize": 1,
    })
    if not data:
        return None

    values = data.get("values", [])
    if not values:
        logger.warning("No ATR values for %s", symbol)
        return None

    try:
        atr_val = float(values[0]["atr"])
        fmt = _format_price(atr_val, pair)
        logger.info("ATR(%d, %s) | %s = %s", period, interval, symbol, fmt)
        return fmt
    except (KeyError, ValueError) as e:
        logger.warning("ATR parse error %s: %s", symbol, e)
        return None


# ── Combined enrichment fetch ─────────────────────────────────────────────────

async def fetch_market_context(pair: str, trade_style: str) -> dict:
    """
    Fetch all enrichment data for a single pair concurrently.
    Returns dict with: live_price, pdh, pdl, pd_open, pd_close, atr_1h, atr_4h
    """
    symbol = _normalise_pair(pair)
    logger.info("Fetching full market context for %s [%s]", symbol, trade_style)

    # Concurrent fetches — all independent API calls
    results = await asyncio.gather(
        fetch_live_price(pair),
        fetch_previous_day_ohlc(pair),
        fetch_atr(pair, period=14, interval="1h"),
        fetch_atr(pair, period=14, interval="4h"),
        return_exceptions=True,
    )

    live_price, ohlc, atr_1h, atr_4h = results

    # Safely unpack — exceptions become None
    if isinstance(live_price, Exception):
        live_price = None
    if isinstance(ohlc, Exception):
        ohlc = None
    if isinstance(atr_1h, Exception):
        atr_1h = None
    if isinstance(atr_4h, Exception):
        atr_4h = None

    context = {
        "live_price": live_price,
        "pdh": ohlc["high"]  if ohlc else None,
        "pdl": ohlc["low"]   if ohlc else None,
        "pd_open":  ohlc["open"]  if ohlc else None,
        "pd_close": ohlc["close"] if ohlc else None,
        "pd_date":  ohlc["date"]  if ohlc else None,
        "atr_1h": atr_1h,
        "atr_4h": atr_4h,
    }

    logger.info(
        "Market context | %s | price=%s PDH=%s PDL=%s ATR1H=%s ATR4H=%s",
        symbol,
        context["live_price"], context["pdh"], context["pdl"],
        context["atr_1h"], context["atr_4h"],
    )
    return context


# ── Bulk prices for AI pair selection ────────────────────────────────────────

async def fetch_all_forex_prices() -> dict:
    """
    Fetch live prices for all standard Forex pairs concurrently.
    Used when no pair is specified and AI selects the best opportunity.
    """
    tasks = [fetch_live_price(p) for p in FOREX_PAIRS]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    prices = {}
    for pair, result in zip(FOREX_PAIRS, results):
        if isinstance(result, str):
            prices[pair] = result
        else:
            logger.warning("No price for %s", pair)
    logger.info("Bulk prices %d/%d: %s", len(prices), len(FOREX_PAIRS), prices)
    return prices
