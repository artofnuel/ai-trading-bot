"""
bot/handlers.py — All Telegram command and callback handlers.

Implements:
  /start        — welcome message
  /trade        — step-by-step trade flow OR natural-language shortcut
  /setbalance   — save a default balance
  /history      — show last 5 trade plans
  /help         — usage instructions
  Natural language — parse free-form trade requests

ConversationHandler states:
  ASK_BALANCE  → ASK_MARKET → ASK_PAIR → ASK_RISK → ASK_NOTES → GENERATE
"""

import logging
import re
from typing import Optional

from telegram import Update, Message
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ChatAction

from ai.analyst import get_trade_plan, AnalystError
from bot.formatter import format_trade_plan, format_history_entry
from bot.keyboards import (
    market_keyboard,
    risk_keyboard,
    forex_pair_keyboard,
    crypto_pair_keyboard,
    cancel_keyboard,
)
from db.database import (
    get_user,
    log_trade,
    get_trade_history,
    set_user_balance,
    upsert_user,
)

logger = logging.getLogger(__name__)

# ── Conversation states ───────────────────────────────────────────────────────
ASK_BALANCE, ASK_MARKET, ASK_PAIR, ASK_RISK, ASK_NOTES, GENERATE = range(6)

# Key used to store partial trade data in user_data
TRADE_KEY = "pending_trade"


# ── /start ────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message and register the user."""
    user = update.effective_user
    await upsert_user(user.id, user.username)

    text = (
        f"👋 Welcome, {user.first_name}!\n\n"
        "I'm your *AI Trade Planner* — powered by Claude.\n\n"
        "Here's what I can do:\n"
        "• Analyse Forex & Crypto markets\n"
        "• Calculate risk-adjusted position sizing\n"
        "• Generate entry, stop loss & take profit levels\n"
        "• Provide trailing stop guidance\n\n"
        "📌 *Commands*\n"
        "/trade — Start a new trade analysis\n"
        "/setbalance — Set your default account balance\n"
        "/history — View your last 5 trade plans\n"
        "/help — Full usage guide\n\n"
        "💡 *Tip:* You can also type naturally, e.g.\n"
        "_\"I have $1000, analyse BTC/USDT for me, aggressive risk\"_"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ── /help ─────────────────────────────────────────────────────────────────────

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display full usage instructions."""
    text = (
        "📘 *AI Trade Planner — Help*\n\n"
        "*Commands:*\n"
        "• /trade — Start a guided trade analysis\n"
        "• /setbalance <amount> — Save your default balance\n"
        "  _Example: /setbalance 500_\n"
        "• /history — See your last 5 generated trade plans\n"
        "• /help — Show this message\n\n"
        "*Natural Language:*\n"
        "Skip the steps and just type something like:\n"
        "_\"I have $2000, check EUR/USD, moderate risk\"_\n"
        "_\"$500 account, pick the best crypto pair, conservative\"_\n\n"
        "*Risk Levels:*\n"
        "🛡 Conservative — 1% of balance at risk\n"
        "⚖️ Moderate      — 2% of balance at risk\n"
        "🔥 Aggressive    — 3% of balance at risk\n\n"
        "*Note:* This bot advises — you execute manually. Always apply your own judgment."
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ── /setbalance ───────────────────────────────────────────────────────────────

async def set_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save the user's default balance. Usage: /setbalance 1000"""
    user = update.effective_user
    await upsert_user(user.id, user.username)

    args = context.args
    if not args:
        await update.message.reply_text(
            "⚠️ Please provide an amount.\nExample: `/setbalance 1000`",
            parse_mode="Markdown",
        )
        return

    raw = args[0].replace(",", "").replace("$", "")
    try:
        balance = float(raw)
        if balance <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid amount. Please enter a positive number.\nExample: `/setbalance 500`",
            parse_mode="Markdown",
        )
        return

    await set_user_balance(user.id, balance)
    await update.message.reply_text(
        f"✅ Default balance saved: *${balance:,.2f}*\n\n"
        "Use /trade to start your next analysis.",
        parse_mode="Markdown",
    )


# ── /history ──────────────────────────────────────────────────────────────────

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display the last 5 trade plans for the user."""
    user = update.effective_user
    await upsert_user(user.id, user.username)

    trades = await get_trade_history(user.id, limit=5)

    if not trades:
        await update.message.reply_text(
            "📭 No trade plans found yet.\nUse /trade to generate your first one!"
        )
        return

    header = f"📋 *Your last {len(trades)} trade plan(s):*\n\n"
    entries = "\n\n".join(
        format_history_entry(t, i + 1) for i, t in enumerate(trades)
    )
    await update.message.reply_text(header + entries, parse_mode="Markdown")


# ── Natural language detection ────────────────────────────────────────────────

def _parse_natural_language(text: str) -> Optional[dict]:
    """
    Try to extract trade parameters from a free-form message.

    Returns a dict with keys: balance, market, pair, risk, notes
    or None if the message doesn't look like a trade request.
    """
    text_lower = text.lower()

    # Must mention money or trading intent
    has_money = bool(re.search(r"\$[\d,]+|[\d,]+\s*(dollar|usd|account|balance)", text_lower))
    has_intent = any(kw in text_lower for kw in ["trade", "analys", "check", "look at", "forex", "crypto", "pair"])

    if not (has_money or has_intent):
        return None

    result: dict = {}

    # Extract balance
    balance_match = re.search(r"\$?([\d,]+(?:\.\d+)?)\s*(?:dollar|usd|account|balance)?", text_lower)
    if balance_match:
        try:
            result["balance"] = float(balance_match.group(1).replace(",", ""))
        except ValueError:
            pass

    # Extract market
    if any(k in text_lower for k in ["crypto", "bitcoin", "btc", "eth", "sol", "usdt"]):
        result["market"] = "Crypto"
    elif any(k in text_lower for k in ["forex", "eur", "gbp", "usd", "jpy", "fx"]):
        result["market"] = "Forex"

    # Extract pair
    pair_match = re.search(
        r"\b(EUR/USD|GBP/USD|USD/JPY|GBP/JPY|BTC/USDT|ETH/USDT|SOL/USDT|BNB/USDT"
        r"|EURUSD|GBPUSD|USDJPY|GBPJPY|BTCUSDT|ETHUSDT|SOLUSDT|BNBUSDT)\b",
        text,
        re.IGNORECASE,
    )
    if pair_match:
        raw_pair = pair_match.group(1).upper()
        # Normalise to slash format
        if "/" not in raw_pair and len(raw_pair) == 6:
            raw_pair = raw_pair[:3] + "/" + raw_pair[3:]
        result["pair"] = raw_pair

    # Extract risk
    if "conservative" in text_lower or "low risk" in text_lower:
        result["risk"] = "conservative"
    elif "aggressive" in text_lower or "high risk" in text_lower:
        result["risk"] = "aggressive"
    else:
        result["risk"] = "moderate"

    return result if result.get("balance") or result.get("market") else None


# ── /trade ConversationHandler ────────────────────────────────────────────────

async def trade_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for /trade — ask for balance."""
    user = update.effective_user
    await upsert_user(user.id, user.username)

    # Pre-fill from saved defaults if available
    db_user = await get_user(user.id)
    context.user_data[TRADE_KEY] = {
        "balance": db_user.get("default_balance") if db_user else None,
        "risk": (db_user.get("default_risk") or "moderate") if db_user else "moderate",
        "market": None,
        "pair": None,
        "notes": "",
    }

    saved = context.user_data[TRADE_KEY]["balance"]
    if saved:
        prompt = (
            f"💰 Your saved balance is *${saved:,.2f}*.\n\n"
            "Reply with a new amount to override it, or type *same* to use it."
        )
    else:
        prompt = "💰 What is your *account balance*? (e.g. `500` or `$1,200`)"

    await update.message.reply_text(
        prompt,
        parse_mode="Markdown",
        reply_markup=cancel_keyboard(),
    )
    return ASK_BALANCE


async def received_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle manual balance input during /trade flow."""
    text = update.message.text.strip()
    pending = context.user_data.get(TRADE_KEY, {})

    if text.lower() in ("same", "s", "keep", "ok") and pending.get("balance"):
        pass  # keep saved balance
    else:
        raw = text.replace(",", "").replace("$", "")
        try:
            balance = float(raw)
            if balance <= 0:
                raise ValueError
            pending["balance"] = balance
        except ValueError:
            await update.message.reply_text(
                "❌ Please enter a valid positive number, e.g. `500`",
                parse_mode="Markdown",
            )
            return ASK_BALANCE

    context.user_data[TRADE_KEY] = pending

    await update.message.reply_text(
        "📊 Choose your *market type*:",
        parse_mode="Markdown",
        reply_markup=market_keyboard(),
    )
    return ASK_MARKET


async def received_market(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle market selection callback."""
    query = update.callback_query
    await query.answer()

    market_map = {"market_forex": "Forex", "market_crypto": "Crypto"}
    market = market_map.get(query.data)
    if not market:
        return ASK_MARKET

    context.user_data[TRADE_KEY]["market"] = market

    pair_kb = forex_pair_keyboard() if market == "Forex" else crypto_pair_keyboard()
    await query.edit_message_text(
        f"✅ Market: *{market}*\n\n🔍 Which *pair* would you like to analyse?",
        parse_mode="Markdown",
        reply_markup=pair_kb,
    )
    return ASK_PAIR


async def received_pair(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle pair selection callback or typed pair."""
    query = update.callback_query
    await query.answer()

    if query.data == "pair_auto":
        context.user_data[TRADE_KEY]["pair"] = None
        pair_label = "🤖 AI will select the best pair"
    elif query.data.startswith("pair_"):
        raw = query.data.replace("pair_", "")
        # Normalise raw codes like EURUSD → EUR/USD
        if "/" not in raw and len(raw) == 6:
            raw = raw[:3] + "/" + raw[3:]
        elif "/" not in raw and len(raw) == 7:
            raw = raw[:3] + "/" + raw[3:]
        context.user_data[TRADE_KEY]["pair"] = raw
        pair_label = raw
    else:
        return ASK_PAIR

    await query.edit_message_text(
        f"✅ Pair: *{pair_label}*\n\n⚖️ Choose your *risk appetite*:",
        parse_mode="Markdown",
        reply_markup=risk_keyboard(),
    )
    return ASK_RISK


async def received_risk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle risk selection callback."""
    query = update.callback_query
    await query.answer()

    risk_map = {
        "risk_conservative": "conservative",
        "risk_moderate": "moderate",
        "risk_aggressive": "aggressive",
    }
    risk = risk_map.get(query.data)
    if not risk:
        return ASK_RISK

    context.user_data[TRADE_KEY]["risk"] = risk

    risk_emoji = {"conservative": "🛡", "moderate": "⚖️", "aggressive": "🔥"}
    await query.edit_message_text(
        f"✅ Risk: *{risk_emoji[risk]} {risk.capitalize()}*\n\n"
        "📝 Any *additional notes* for Claude? (market outlook, news events, etc.)\n"
        "Or type *skip* to proceed.",
        parse_mode="Markdown",
    )
    return ASK_NOTES


async def received_notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle optional notes and trigger plan generation."""
    text = update.message.text.strip()
    if text.lower() not in ("skip", "s", "none", "no", "n/a"):
        context.user_data[TRADE_KEY]["notes"] = text
    else:
        context.user_data[TRADE_KEY]["notes"] = ""

    return await _generate_and_send(update.message, context)


async def _generate_and_send(message: Message, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Core generation step: call Claude, format, log, and send the trade plan.

    Called from both the step-by-step flow and natural language shortcut.
    """
    pending = context.user_data.get(TRADE_KEY, {})
    balance = pending.get("balance", 0) or 0
    market = pending.get("market", "Forex")
    pair = pending.get("pair")
    risk = pending.get("risk", "moderate")
    notes = pending.get("notes", "")
    user_id = message.from_user.id

    # Guard: ensure we have a balance
    if not balance or balance <= 0:
        await message.reply_text(
            "⚠️ Something went wrong — balance is missing. Please use /trade to start again."
        )
        return ConversationHandler.END

    thinking_msg = await message.reply_text(
        "⏳ Claude is analysing the market… This may take up to 30 seconds."
    )
    await message.chat.send_action(ChatAction.TYPING)

    try:
        plan, price_ok = await get_trade_plan(
            balance=balance,
            market=market,
            pair=pair,
            risk=risk,
            notes=notes,
        )
        
        # If live price fetch failed, notify the user
        if not price_ok:
            await message.reply_text(
                "⚠️ Notice: Live price fetching failed. Claude is estimating levels based on its training data. "
                "Please verify prices manually before executing."
            )
            
    except AnalystError as exc:
        await thinking_msg.delete()
        await message.reply_text(f"❌ Analysis failed:\n{exc}\n\nPlease try again with /trade.")
        return ConversationHandler.END
    except Exception as exc:
        logger.exception("Unexpected error generating trade plan")
        await thinking_msg.delete()
        await message.reply_text(
            "❌ An unexpected error occurred. Please try again later."
        )
        return ConversationHandler.END

    # Log to database before sending
    try:
        await log_trade(user_id, plan)
    except Exception:
        logger.exception("Failed to log trade to database")

    # Format and send
    formatted = format_trade_plan(plan, balance)
    await thinking_msg.delete()
    await message.reply_text(formatted)

    # Clean up conversation state
    context.user_data.pop(TRADE_KEY, None)
    return ConversationHandler.END


# ── Natural language message handler ─────────────────────────────────────────

async def handle_natural_language(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Intercept plain messages that look like trade requests and handle them
    without requiring the full /trade flow.
    """
    user = update.effective_user
    await upsert_user(user.id, user.username)

    text = update.message.text.strip()
    parsed = _parse_natural_language(text)

    if not parsed:
        # Not a trade request — give a helpful nudge
        await update.message.reply_text(
            "🤔 I'm not sure what you mean. Try /trade for a guided analysis, "
            "or /help to see how to use me."
        )
        return

    # Merge with user defaults for any missing fields
    db_user = await get_user(user.id)
    balance = parsed.get("balance") or (db_user.get("default_balance") if db_user else None)
    if not balance:
        await update.message.reply_text(
            "💰 I couldn't find your balance in your message.\n"
            "Please include it (e.g. *$500*) or set a default with /setbalance.",
            parse_mode="Markdown",
        )
        return

    market = parsed.get("market", "Forex")
    pair = parsed.get("pair")
    risk = parsed.get("risk") or (db_user.get("default_risk") if db_user else "moderate")

    context.user_data[TRADE_KEY] = {
        "balance": balance,
        "market": market,
        "pair": pair,
        "risk": risk,
        "notes": "",
    }

    await _generate_and_send(update.message, context)


# ── Cancel handler ────────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the current /trade conversation."""
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("❌ Trade analysis cancelled. Use /trade to start again.")
    else:
        await update.message.reply_text("❌ Cancelled. Use /trade to start again.")

    context.user_data.pop(TRADE_KEY, None)
    return ConversationHandler.END


# ── Application registration ──────────────────────────────────────────────────

def register_handlers(app: Application) -> None:
    """Attach all handlers to the Application instance."""

    # /trade conversation
    trade_conv = ConversationHandler(
        entry_points=[CommandHandler("trade", trade_start)],
        states={
            ASK_BALANCE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_balance)
            ],
            ASK_MARKET: [
                CallbackQueryHandler(received_market, pattern="^market_")
            ],
            ASK_PAIR: [
                CallbackQueryHandler(received_pair, pattern="^pair_")
            ],
            ASK_RISK: [
                CallbackQueryHandler(received_risk, pattern="^risk_")
            ],
            ASK_NOTES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_notes)
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel, pattern="^cancel$"),
            CommandHandler("cancel", cancel),
        ],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("setbalance", set_balance))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(trade_conv)

    # Natural language fallback — catches any non-command text outside a conversation
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_natural_language)
    )
