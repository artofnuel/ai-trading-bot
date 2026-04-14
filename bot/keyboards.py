"""
bot/keyboards.py — Inline and reply keyboard definitions.

Keeps all keyboard markup in one place so handlers stay clean.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup


# ── Market selection ──────────────────────────────────────────────────────────

def market_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard for choosing Forex or Crypto."""
    buttons = [
        [
            InlineKeyboardButton("💱 Forex", callback_data="market_forex"),
            InlineKeyboardButton("₿ Crypto", callback_data="market_crypto"),
        ]
    ]
    return InlineKeyboardMarkup(buttons)


# ── Risk appetite ─────────────────────────────────────────────────────────────

def risk_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard for choosing risk appetite."""
    buttons = [
        [
            InlineKeyboardButton("🛡 Conservative (1%)", callback_data="risk_conservative"),
            InlineKeyboardButton("⚖️ Moderate (2%)", callback_data="risk_moderate"),
        ],
        [
            InlineKeyboardButton("🔥 Aggressive (3%)", callback_data="risk_aggressive"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


# ── Pair selection (Forex) ────────────────────────────────────────────────────

def forex_pair_keyboard() -> InlineKeyboardMarkup:
    """Quick-pick inline keyboard for common Forex pairs."""
    buttons = [
        [
            InlineKeyboardButton("EUR/USD", callback_data="pair_EURUSD"),
            InlineKeyboardButton("GBP/USD", callback_data="pair_GBPUSD"),
        ],
        [
            InlineKeyboardButton("USD/JPY", callback_data="pair_USDJPY"),
            InlineKeyboardButton("GBP/JPY", callback_data="pair_GBPJPY"),
        ],
        [
            InlineKeyboardButton("XAU/USD (Gold)", callback_data="pair_XAUUSD"),
            InlineKeyboardButton("XAG/USD (Silver)", callback_data="pair_XAGUSD"),
        ],
        [
            InlineKeyboardButton("✏️ Type any pair", callback_data="pair_custom"),
        ],
        [
            InlineKeyboardButton("🤖 AI picks best pair", callback_data="pair_auto"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


# ── Pair selection (Crypto) ───────────────────────────────────────────────────

def crypto_pair_keyboard() -> InlineKeyboardMarkup:
    """Quick-pick inline keyboard for common Crypto pairs."""
    buttons = [
        [
            InlineKeyboardButton("BTC/USDT", callback_data="pair_BTCUSDT"),
            InlineKeyboardButton("ETH/USDT", callback_data="pair_ETHUSDT"),
        ],
        [
            InlineKeyboardButton("SOL/USDT", callback_data="pair_SOLUSDT"),
            InlineKeyboardButton("BNB/USDT", callback_data="pair_BNBUSDT"),
        ],
        [
            InlineKeyboardButton("✏️ Type any pair", callback_data="pair_custom"),
        ],
        [
            InlineKeyboardButton("🤖 AI picks best pair", callback_data="pair_auto"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def lot_size_keyboard() -> InlineKeyboardMarkup:
    """Quick-pick inline keyboard for lot size selection."""
    buttons = [
        [
            InlineKeyboardButton("0.01 (micro)", callback_data="lot_0.01"),
            InlineKeyboardButton("0.02", callback_data="lot_0.02"),
        ],
        [
            InlineKeyboardButton("0.05", callback_data="lot_0.05"),
            InlineKeyboardButton("0.10", callback_data="lot_0.10"),
        ],
        [
            InlineKeyboardButton("0.25", callback_data="lot_0.25"),
            InlineKeyboardButton("0.50", callback_data="lot_0.50"),
        ],
        [
            InlineKeyboardButton("1.00 (standard)", callback_data="lot_1.00"),
        ],
        [
            InlineKeyboardButton("✏️ Type custom lot size", callback_data="lot_custom"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


# ── Cancel ────────────────────────────────────────────────────────────────────

def cancel_keyboard() -> InlineKeyboardMarkup:
    """Single cancel button for mid-flow cancellation."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
    )
