"""
market/prices.py — Live market price fetcher.

Supports ANY valid trading pair — not limited to a predefined list.

Routing logic:
- Pairs containing USDT, BTC, ETH, SOL, BNB, XRP → Binance
- All others (Forex, Metals, Indices) → Twelve Data
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

CRYPTO_KEYWORDS = {"USDT", "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA"}


def is_crypto(pair: str) -> bool:
    """Detect crypto pairs by keyword — supports any crypto pair."""
    return any(kw in pair.upper() for kw in CRYPTO_KEYWORDS)


def normalise_binance(pair: str) -> str:
    """BTC/USDT → BTCUSDT"""
    return pair.replace("/", "").upper()


def normalise_twelve(pair: str) -> str:
    """
    Ensure pair has slash format for Twelve Data.
    XAUUSD → XAU/USD, EUR/USD → EUR/USD
    """
    pair = pair.upper().replace(" ", "")
    if "/" in pair:
        return pair
    # Split 6-char pairs: EURUSD → EUR/USD, XAUUSD → XAU/USD
    if len(pair) == 6:
        return pair[:3] + "/" + pair[3:]
    # Split 7-char pairs: XAGUSD → XAG/USD
    if len(pair) == 7:
        return pair[:4] + "/" + pair[4:]
    return pair


# ── Binance — Any crypto pair ─────────────────────────────────────────────────

async def _fetch_binance(pair: str) -> Optional[str]:
    """Fetch live crypto price from Binance public API."""
    symbol = normalise_binance(pair)
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(BINANCE_PRICE_URL, params={"symbol": symbol})
            r.raise_for_status()
            data = r.json()
            price = data.get("price")
            if price:
                p = float(price)
                # Format based on price magnitude
                if p >= 10000:
                    formatted = f"{p:.2f}"
                elif p >= 100:
                    formatted = f"{p:.3f}"
                elif p >= 1:
                    formatted = f"{p:.4f}"
                else:
                    formatted = f"{p:.6f}"
                logger.info("Binance | %s = %s", symbol, formatted)
                return formatted
            else:
                logger.warning("Binance returned no price for %s: %s", symbol, data)
    except httpx.TimeoutException:
        logger.warning("Binance timeout for %s", symbol)
    except httpx.HTTPStatusError as e:
        logger.warning("Binance HTTP %s for %s", e.response.status_code, symbol)
    except Exception as e:
        logger.exception("Binance error for %s: %s", symbol, e)
    return None


# ── Twelve Data — Any Forex, Metal, Index pair ────────────────────────────────

async def _fetch_twelve_data(pair: str) -> Optional[str]:
    """Fetch live price from Twelve Data — supports Forex, metals, indices."""
    if not TWELVE_DATA_API_KEY:
        logger.warning("TWELVE_DATA_API_KEY not set — cannot fetch price for %s", pair)
        return None

    symbol = normalise_twelve(pair)
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                TWELVE_DATA_URL,
                params={"symbol": symbol, "apikey": TWELVE_DATA_API_KEY},
            )
            r.raise_for_status()
            data = r.json()

            if data.get("status") == "error":
                logger.warning(
                    "Twelve Data error for %s: %s",
                    symbol, data.get("message", "unknown error")
                )
                return None

            price = data.get("price")
            if price:
                p = float(price)
                # Format based on price magnitude
                if p >= 1000:
                    formatted = f"{p:.2f}"    # Gold: 2345.67
                elif p >= 100:
                    formatted = f"{p:.3f}"    # JPY pairs: 157.832
                else:
                    formatted = f"{p:.5f}"    # EUR/USD: 1.08432
                logger.info("Twelve Data | %s = %s", symbol, formatted)
                return formatted
            else:
                logger.warning("Twelve Data returned no price for %s: %s", symbol, data)

    except httpx.TimeoutException:
        logger.warning("Twelve Data timeout for %s", symbol)
    except httpx.HTTPStatusError as e:
        logger.warning("Twelve Data HTTP %s for %s", e.response.status_code, symbol)
    except Exception as e:
        logger.exception("Twelve Data error for %s: %s", symbol, e)
    return None


# ── Public interface ──────────────────────────────────────────────────────────

async def fetch_live_price(pair: str) -> Optional[str]:
    """
    Fetch live price for ANY valid trading pair.
    Routes automatically to Binance (crypto) or Twelve Data (everything else).
    """
    if is_crypto(pair):
        return await _fetch_binance(pair)
    else:
        return await _fetch_twelve_data(pair)


async def fetch_all_prices(market: str) -> dict:
    """
    Fetch live prices for default recommended pairs when user
    has not specified a pair. Claude uses these to pick the best setup.
    """
    if market == "Crypto":
        pairs = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"]
    else:
        pairs = ["EUR/USD", "GBP/USD", "USD/JPY", "GBP/JPY", "XAU/USD"]

    prices = {}
    for pair in pairs:
        price = await fetch_live_price(pair)
        if price:
            prices[pair] = price
        else:
            logger.warning("Could not fetch price for %s", pair)

    logger.info("Fetched %d/%d prices: %s", len(prices), len(pairs), prices)
    return prices
