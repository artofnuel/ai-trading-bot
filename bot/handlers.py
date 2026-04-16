"""
bot/handlers.py — All Telegram command and conversation handlers.

Conversation flow:
  /trade → Balance → Market → Pair → Trade Style → Risk → Lot Size → Notes → Generate

Natural language also supported:
  "I have $500, analyse EUR/USD, aggressive risk, scalp"
"""

import logging
import re
from typing import Optional

from telegram import Update, Message
from telegram.constants import ChatAction
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from ai.analyst import get_trade_plan, AnalystError
from bot.formatter import format_trade_plan, format_history_entry
from bot.keyboards import (
    market_keyboard,
    trade_style_keyboard,
    risk_keyboard,
    forex_pair_keyboard,
    crypto_pair_keyboard,
    lot_size_keyboard,
    cancel_keyboard,
)
from db.database import get_user, upsert_user, log_trade, get_trade_history

logger = logging.getLogger(__name__)

# ── Conversation states ───────────────────────────────────────────────────────

(
    ASK_BALANCE,
    ASK_MARKET,
    ASK_PAIR,
    ASK_STYLE,
    ASK_RISK,
    ASK_LOT_SIZE,
    ASK_NOTES,
    GENERATE,
) = range(8)

TRADE_KEY = "trade_data"


# ── /start ────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await upsert_user(user.id, user.username or "")
    await update.message.reply_text(
        f"👋 Welcome, {user.first_name}!\n\n"
        "I'm your *AI Trade Planner* — powered by live market data and Claude AI.\n\n"
        "I analyse the market in real time and return a complete trade plan:\n"
        "• Entry, Stop Loss, Take Profits\n"
        "• Exact lot sizing and risk in dollars\n"
        "• Scalp or Swing setups\n"
        "• Live-price anchored levels\n\n"
        "Commands:\n"
        "/trade — Start a new trade analysis\n"
        "/history — View your last 5 trade plans\n"
        "/setbalance — Save your default balance\n"
        "/help — Show this message\n\n"
        "⚠️ *For educational purposes only. Always verify before trading.*",
        parse_mode="Markdown",
    )


# ── /help ─────────────────────────────────────────────────────────────────────

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


# ── /setbalance ───────────────────────────────────────────────────────────────

async def set_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: `/setbalance 500`", parse_mode="Markdown"
        )
        return
    try:
        balance = float(args[0].replace(",", "").replace("$", ""))
        if balance <= 0:
            raise ValueError
        user_id = update.effective_user.id
        await upsert_user(user_id, update.effective_user.username or "", default_balance=balance)
        await update.message.reply_text(
            f"✅ Default balance saved: *${balance:,.2f}*", parse_mode="Markdown"
        )
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Invalid amount. Use: `/setbalance 500`", parse_mode="Markdown")


# ── /history ──────────────────────────────────────────────────────────────────

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    trades = await get_trade_history(user_id, limit=5)
    if not trades:
        await update.message.reply_text("📭 No trade history yet. Use /trade to get started.")
        return
    lines = ["📋 *Your last trade plans:*\n"]
    for i, trade in enumerate(trades, 1):
        lines.append(format_history_entry(trade, i))
    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")


# ── /trade — conversation entry ───────────────────────────────────────────────

async def trade_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data[TRADE_KEY] = {}
    user = await get_user(update.effective_user.id)
    default_balance = user.get("default_balance") if user else None

    if default_balance:
        context.user_data[TRADE_KEY]["balance"] = default_balance
        await update.message.reply_text(
            f"💰 Using saved balance: *${default_balance:,.2f}*\n\n"
            f"📊 Which *market* do you want to trade?",
            parse_mode="Markdown",
            reply_markup=market_keyboard(),
        )
        return ASK_MARKET

    await update.message.reply_text(
        "💰 What is your *account balance* in USD?\n\n"
        "_Example: 500 or 1000_",
        parse_mode="Markdown",
        reply_markup=cancel_keyboard(),
    )
    return ASK_BALANCE


async def received_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().replace(",", "").replace("$", "")
    try:
        balance = float(text)
        if balance <= 0:
            raise ValueError
        context.user_data[TRADE_KEY]["balance"] = balance
        await update.message.reply_text(
            f"✅ Balance: *${balance:,.2f}*\n\n📊 Which *market*?",
            parse_mode="Markdown",
            reply_markup=market_keyboard(),
        )
        return ASK_MARKET
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid amount. Enter a number like `500` or `1000`.",
            parse_mode="Markdown",
        )
        return ASK_BALANCE


async def received_market(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    market_map = {"market_forex": "Forex", "market_crypto": "Crypto"}
    market = market_map.get(query.data)
    if not market:
        return ASK_MARKET

    context.user_data[TRADE_KEY]["market"] = market
    kb = forex_pair_keyboard() if market == "Forex" else crypto_pair_keyboard()
    await query.edit_message_text(
        f"✅ Market: *{market}*\n\n🔀 Which *pair*?",
        parse_mode="Markdown",
        reply_markup=kb,
    )
    return ASK_PAIR


async def received_pair(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "pair_auto":
        context.user_data[TRADE_KEY]["pair"] = None
        pair_label = "🤖 AI selects best pair"
    elif query.data == "pair_custom":
        await query.edit_message_text(
            "✏️ Type your pair (e.g. `XAU/USD`, `EUR/GBP`, `BTC/USDT`):",
            parse_mode="Markdown",
        )
        return ASK_PAIR
    else:
        raw = query.data.replace("pair_", "")
        if "/" not in raw:
            if len(raw) == 6:
                raw = raw[:3] + "/" + raw[3:]
            elif len(raw) == 7:
                raw = raw[:4] + "/" + raw[4:]
        context.user_data[TRADE_KEY]["pair"] = raw
        pair_label = raw

    await query.edit_message_text(
        f"✅ Pair: *{pair_label}*\n\n⚡ *Trade style?*",
        parse_mode="Markdown",
        reply_markup=trade_style_keyboard(),
    )
    return ASK_STYLE


async def received_pair_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle manually typed pair."""
    text = update.message.text.strip().upper().replace(" ", "")
    if "/" not in text:
        if len(text) == 6:
            text = text[:3] + "/" + text[3:]
        elif len(text) == 7:
            text = text[:4] + "/" + text[4:]
    context.user_data[TRADE_KEY]["pair"] = text
    await update.message.reply_text(
        f"✅ Pair: *{text}*\n\n⚡ *Trade style?*",
        parse_mode="Markdown",
        reply_markup=trade_style_keyboard(),
    )
    return ASK_STYLE


async def received_style(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    style_map = {"style_scalp": "scalp", "style_swing": "swing"}
    style = style_map.get(query.data, "swing")
    context.user_data[TRADE_KEY]["trade_style"] = style
    style_label = "⚡ Scalp" if style == "scalp" else "📈 Swing"
    await query.edit_message_text(
        f"✅ Style: *{style_label}*\n\n⚖️ *Risk appetite?*",
        parse_mode="Markdown",
        reply_markup=risk_keyboard(),
    )
    return ASK_RISK


async def received_risk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
        f"📐 *Lot size?*\n_0.01 is the broker minimum on most platforms._",
        parse_mode="Markdown",
        reply_markup=lot_size_keyboard(),
    )
    return ASK_LOT_SIZE


async def received_lot_size_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "lot_custom":
        await query.edit_message_text(
            "✏️ Type your lot size (e.g. `0.03`, `0.15`, `2.00`):",
            parse_mode="Markdown",
        )
        return ASK_LOT_SIZE
    lot = query.data.replace("lot_", "")
    context.user_data[TRADE_KEY]["lot_size"] = lot
    await query.edit_message_text(
        f"✅ Lot size: *{lot}*\n\n"
        f"📝 Any *additional notes*? (e.g. 'bearish bias', 'avoid NFP hour')\n"
        f"Or type *skip* to generate now.",
        parse_mode="Markdown",
    )
    return ASK_NOTES


async def received_lot_size_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    try:
        lot = float(text)
        if lot <= 0:
            raise ValueError
        if lot < 0.01:
            await update.message.reply_text(
                "⚠️ Minimum lot size is *0.01*. Please enter 0.01 or higher.",
                parse_mode="Markdown",
            )
            return ASK_LOT_SIZE
        lot_str = f"{lot:.2f}"
        context.user_data[TRADE_KEY]["lot_size"] = lot_str
        await update.message.reply_text(
            f"✅ Lot size: *{lot_str}*\n\n"
            f"📝 Any *additional notes*? Or type *skip* to generate.",
            parse_mode="Markdown",
        )
        return ASK_NOTES
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid. Enter a number like `0.01`, `0.10`, or `1.00`.",
            parse_mode="Markdown",
        )
        return ASK_LOT_SIZE


async def received_notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    notes = "" if text.lower() == "skip" else text
    context.user_data[TRADE_KEY]["notes"] = notes
    return await _generate_and_send(update.message, context)


# ── Natural language handler ──────────────────────────────────────────────────

def _parse_natural_language(text: str) -> Optional[dict]:
    """
    Parse a free-text trade request.
    Handles: balance, market, pair, risk, trade style.
    """
    text_lower = text.lower()

    # Balance
    balance_match = re.search(
        r'\$?([\d,]+(?:\.\d{1,2})?)\s*(?:dollars?|usd)?', text_lower
    )
    if not balance_match:
        return None
    try:
        balance = float(balance_match.group(1).replace(",", ""))
    except ValueError:
        return None

    # Market
    if any(w in text_lower for w in ["crypto", "bitcoin", "btc", "eth", "sol", "usdt"]):
        market = "Crypto"
    else:
        market = "Forex"

    # Pair — look for known pattern like EUR/USD, XAUUSD, BTC/USDT etc.
    pair_match = re.search(
        r'\b([A-Za-z]{2,4}[/]?[A-Za-z]{2,4})\b', text
    )
    pair = None
    if pair_match:
        raw = pair_match.group(1).upper()
        # Filter out words that match but aren't pairs
        non_pairs = {"HAVE", "WANT", "NEED", "WITH", "FOR", "AND", "THE"}
        if raw not in non_pairs and len(raw) >= 5:
            if "/" not in raw and len(raw) == 6:
                raw = raw[:3] + "/" + raw[3:]
            pair = raw

    # Risk
    if "conservative" in text_lower or "low risk" in text_lower:
        risk = "conservative"
    elif "aggressive" in text_lower or "high risk" in text_lower:
        risk = "aggressive"
    else:
        risk = "moderate"

    # Trade style
    if "scalp" in text_lower or "quick" in text_lower or "fast" in text_lower:
        trade_style = "scalp"
    else:
        trade_style = "swing"

    return {
        "balance": balance,
        "market": market,
        "pair": pair,
        "risk": risk,
        "trade_style": trade_style,
        "lot_size": "0.01",
        "notes": "",
    }


async def handle_natural_language(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    text = update.message.text.strip()
    parsed = _parse_natural_language(text)
    if not parsed:
        await update.message.reply_text(
            "I couldn't parse your request. Try:\n"
            "_\"I have $500, analyse EUR/USD, aggressive risk, scalp\"_\n\n"
            "Or use /trade to go step by step.",
            parse_mode="Markdown",
        )
        return

    context.user_data[TRADE_KEY] = parsed
    pair_label = parsed["pair"] or "AI picks best pair"
    style_icon = "⚡" if parsed["trade_style"] == "scalp" else "📈"
    await update.message.reply_text(
        f"📋 *Parsed request:*\n"
        f"Balance : ${parsed['balance']:,.2f}\n"
        f"Market  : {parsed['market']}\n"
        f"Pair    : {pair_label}\n"
        f"Risk    : {parsed['risk'].capitalize()}\n"
        f"Style   : {style_icon} {parsed['trade_style'].capitalize()}\n\n"
        f"⏳ Fetching live data and generating plan…",
        parse_mode="Markdown",
    )
    await _generate_and_send(update.message, context)


# ── Trade generation ──────────────────────────────────────────────────────────

async def _generate_and_send(
    message: Message, context: ContextTypes.DEFAULT_TYPE
) -> int:
    pending = context.user_data.get(TRADE_KEY, {})
    balance     = pending.get("balance", 0)
    market      = pending.get("market", "Forex")
    pair        = pending.get("pair")
    risk        = pending.get("risk", "moderate")
    trade_style = pending.get("trade_style", "swing")
    lot_size    = pending.get("lot_size", "0.01")
    notes       = pending.get("notes", "")
    user_id     = message.from_user.id

    if not balance or balance <= 0:
        await message.reply_text("⚠️ Balance missing. Use /trade to start again.")
        return ConversationHandler.END

    thinking = await message.reply_text("⏳ Fetching live market data…")
    await message.chat.send_action(ChatAction.TYPING)

    try:
        plan = await get_trade_plan(
            balance=balance,
            market=market,
            pair=pair,
            risk=risk,
            trade_style=trade_style,
            notes=notes,
            lot_size=lot_size,
        )
    except AnalystError as exc:
        await thinking.delete()
        await message.reply_text(f"❌ Analysis failed:\n{exc}\n\nTry again with /trade.")
        return ConversationHandler.END
    except Exception:
        logger.exception("Unexpected error generating plan")
        await thinking.delete()
        await message.reply_text("❌ Unexpected error. Please try /trade again.")
        return ConversationHandler.END

    try:
        await log_trade(user_id, plan)
    except Exception:
        logger.exception("Failed to log trade to DB")

    await thinking.delete()
    await message.reply_text(format_trade_plan(plan, balance))
    context.user_data.pop(TRADE_KEY, None)
    return ConversationHandler.END


# ── Cancel ────────────────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop(TRADE_KEY, None)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ Trade analysis cancelled.")
    else:
        await update.message.reply_text("❌ Cancelled. Use /trade to start again.")
    return ConversationHandler.END


# ── Register all handlers ─────────────────────────────────────────────────────

def register_handlers(app) -> None:
    # Simple commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("setbalance", set_balance))
    app.add_handler(CommandHandler("history", history))

    # Trade conversation
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
                CallbackQueryHandler(received_pair, pattern="^pair_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_pair_text),
            ],
            ASK_STYLE: [
                CallbackQueryHandler(received_style, pattern="^style_")
            ],
            ASK_RISK: [
                CallbackQueryHandler(received_risk, pattern="^risk_")
            ],
            ASK_LOT_SIZE: [
                CallbackQueryHandler(received_lot_size_callback, pattern="^lot_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_lot_size_text),
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
    app.add_handler(trade_conv)

    # Natural language fallback — catches any free-text message outside conversation
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_natural_language)
    )
