"""
market/prices.py — Live market price fetcher.

Supports ANY valid trading pair — not limited to a predefined list.

Routing:
  Crypto keywords (USDT, BTC, ETH, etc.) → Binance public API (no key needed)
  Everything else (Forex, metals, indices) → Twelve Data API (free tier)
"""

import logging
from typing import Optional

import httpx

from config import BINANCE_PRICE_URL, TWELVE_DATA_URL, TWELVE_DATA_API_KEY

logger = logging.getLogger(__name__)

# ── Pair classification ───────────────────────────────────────────────────────

CRYPTO_KEYWORDS = {"USDT", "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA"}


def is_crypto(pair: str) -> bool:
    return any(kw in pair.upper() for kw in CRYPTO_KEYWORDS)


def normalise_binance(pair: str) -> str:
    """BTC/USDT → BTCUSDT"""
    return pair.replace("/", "").upper()


def normalise_twelve(pair: str) -> str:
    """Ensure slash format for Twelve Data. XAUUSD → XAU/USD"""
    pair = pair.upper().replace(" ", "")
    if "/" in pair:
        return pair
    if len(pair) == 6:
        return pair[:3] + "/" + pair[3:]
    if len(pair) == 7:
        return pair[:4] + "/" + pair[4:]
    return pair


# ── Binance ───────────────────────────────────────────────────────────────────

async def _fetch_binance(pair: str) -> Optional[str]:
    symbol = normalise_binance(pair)
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(BINANCE_PRICE_URL, params={"symbol": symbol})
            r.raise_for_status()
            data = r.json()
            price = data.get("price")
            if price:
                p = float(price)
                if p >= 10000:
                    fmt = f"{p:.2f}"
                elif p >= 100:
                    fmt = f"{p:.3f}"
                elif p >= 1:
                    fmt = f"{p:.4f}"
                else:
                    fmt = f"{p:.6f}"
                logger.info("Binance | %s = %s", symbol, fmt)
                return fmt
            logger.warning("Binance no price for %s: %s", symbol, data)
    except httpx.TimeoutException:
        logger.warning("Binance timeout: %s", symbol)
    except httpx.HTTPStatusError as e:
        logger.warning("Binance HTTP %s: %s", e.response.status_code, symbol)
    except Exception as e:
        logger.exception("Binance error %s: %s", symbol, e)
    return None


# ── Twelve Data ───────────────────────────────────────────────────────────────

async def _fetch_twelve_data(pair: str) -> Optional[str]:
    if not TWELVE_DATA_API_KEY:
        logger.warning("TWELVE_DATA_API_KEY not set — skipping %s", pair)
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
                logger.warning("Twelve Data error %s: %s", symbol, data.get("message"))
                return None
            price = data.get("price")
            if price:
                p = float(price)
                if p >= 1000:
                    fmt = f"{p:.2f}"     # Gold: 2345.67
                elif p >= 100:
                    fmt = f"{p:.3f}"     # JPY: 157.832
                else:
                    fmt = f"{p:.5f}"     # EUR/USD: 1.08432
                logger.info("TwelveData | %s = %s", symbol, fmt)
                return fmt
            logger.warning("Twelve Data no price for %s: %s", symbol, data)
    except httpx.TimeoutException:
        logger.warning("Twelve Data timeout: %s", symbol)
    except httpx.HTTPStatusError as e:
        logger.warning("Twelve Data HTTP %s: %s", e.response.status_code, symbol)
    except Exception as e:
        logger.exception("Twelve Data error %s: %s", symbol, e)
    return None


# ── Public interface ──────────────────────────────────────────────────────────

async def fetch_live_price(pair: str) -> Optional[str]:
    """Fetch live price for any valid pair. Auto-routes to correct API."""
    if is_crypto(pair):
        return await _fetch_binance(pair)
    return await _fetch_twelve_data(pair)


async def fetch_all_prices(market: str) -> dict:
    """
    Fetch live prices for recommended pairs when no pair is specified.
    Claude uses these real prices to select the best opportunity.
    """
    pairs = (
        ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"]
        if market == "Crypto"
        else ["EUR/USD", "GBP/USD", "USD/JPY", "GBP/JPY", "XAU/USD"]
    )
    prices = {}
    for pair in pairs:
        price = await fetch_live_price(pair)
        if price:
            prices[pair] = price
        else:
            logger.warning("No price for %s", pair)
    logger.info("Prices fetched %d/%d: %s", len(prices), len(pairs), prices)
    return prices
