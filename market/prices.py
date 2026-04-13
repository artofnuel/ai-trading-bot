"""
market/prices.py — Live market price fetcher.

Crypto  → Binance public API (no key required, unlimited)
Forex   → Twelve Data API (free tier, 800 req/day)

Falls back gracefully if a price cannot be fetched,
so the bot never crashes — it just warns Claude to estimate.
"""

import logging
from typing import Optional

import httpx

from config import (
    BINANCE_PRICE_URL,
    TWELVE_DATA_URL,
    TWELVE_DATA_API_KEY,
)

logger = logging.getLogger(__name__)

# ── Pair classification ───────────────────────────────────────────────────────

CRYPTO_KEYWORDS = {"USDT", "BTC", "ETH", "SOL", "BNB", "XRP"}

FOREX_PAIRS = {
    "EUR/USD", "GBP/USD", "USD/JPY", "GBP/JPY",
    "AUD/USD", "USD/CAD", "USD/CHF", "NZD/USD",
}

CRYPTO_PAIRS = {
    "BTC/USDT", "ETH/USDT", "SOL/USDT",
    "BNB/USDT", "XRP/USDT",
}


def is_crypto(pair: str) -> bool:
    pair_upper = pair.upper()
    return any(kw in pair_upper for kw in CRYPTO_KEYWORDS)


def normalise(pair: str) -> str:
    """EUR/USD → EURUSD, BTC/USDT → BTCUSDT"""
    return pair.replace("/", "").upper()


# ── Binance — Crypto prices ───────────────────────────────────────────────────

async def _fetch_binance(pair: str) -> Optional[str]:
    """Fetch live crypto price from Binance public API. No key needed."""
    symbol = normalise(pair)
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(BINANCE_PRICE_URL, params={"symbol": symbol})
            r.raise_for_status()
            data = r.json()
            price = data.get("price")
            if price:
                # Format to reasonable decimal places
                p = float(price)
                formatted = f"{p:.2f}" if p > 100 else f"{p:.5f}"
                logger.info("Binance | %s = %s", symbol, formatted)
                return formatted
    except httpx.TimeoutException:
        logger.warning("Binance timeout for %s", symbol)
    except httpx.HTTPStatusError as e:
        logger.warning("Binance HTTP error for %s: %s", symbol, e.response.status_code)
    except Exception as e:
        logger.exception("Binance unexpected error for %s: %s", symbol, e)
    return None


# ── Twelve Data — Forex prices ────────────────────────────────────────────────

async def _fetch_twelve_data(pair: str) -> Optional[str]:
    """Fetch live Forex price from Twelve Data."""
    if not TWELVE_DATA_API_KEY:
        logger.warning("TWELVE_DATA_API_KEY not configured — cannot fetch Forex price for %s", pair)
        return None

    # Twelve Data expects EUR/USD format (with slash)
    symbol = pair.upper()
    if "/" not in symbol and len(symbol) == 6:
        symbol = symbol[:3] + "/" + symbol[3:]

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                TWELVE_DATA_URL,
                params={"symbol": symbol, "apikey": TWELVE_DATA_API_KEY},
            )
            r.raise_for_status()
            data = r.json()

            # Twelve Data returns {"price": "1.08432"} on success
            # or {"status": "error", "message": "..."} on failure
            if data.get("status") == "error":
                logger.warning(
                    "Twelve Data error for %s: %s",
                    symbol, data.get("message")
                )
                return None

            price = data.get("price")
            if price:
                p = float(price)
                formatted = f"{p:.5f}" if p < 100 else f"{p:.3f}"
                logger.info("Twelve Data | %s = %s", symbol, formatted)
                return formatted

    except httpx.TimeoutException:
        logger.warning("Twelve Data timeout for %s", symbol)
    except httpx.HTTPStatusError as e:
        logger.warning("Twelve Data HTTP error for %s: %s", symbol, e.response.status_code)
    except Exception as e:
        logger.exception("Twelve Data unexpected error for %s: %s", symbol, e)
    return None


# ── Public interface ──────────────────────────────────────────────────────────

async def fetch_live_price(pair: str) -> Optional[str]:
    """
    Fetch the current live price for any supported pair.

    Automatically routes to Binance (crypto) or Twelve Data (forex).
    Returns price as string or None if unavailable.
    """
    if is_crypto(pair):
        return await _fetch_binance(pair)
    else:
        return await _fetch_twelve_data(pair)


async def fetch_all_prices(market: str) -> dict:
    """
    Fetch live prices for all recommended pairs in the given market.
    Used when Claude selects the best pair automatically.

    Returns: {"EUR/USD": "1.08432", "GBP/USD": "1.26710", ...}
    """
    pairs = CRYPTO_PAIRS if market == "Crypto" else FOREX_PAIRS

    prices = {}
    for pair in pairs:
        price = await fetch_live_price(pair)
        if price:
            prices[pair] = price
        else:
            logger.warning("Could not fetch live price for %s", pair)

    logger.info("Live prices fetched (%d/%d): %s", len(prices), len(pairs), prices)
    return prices
