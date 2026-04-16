"""
bot/formatter.py — Convert a Claude trade plan dict into a clean Telegram message.

Changes from previous version:
  - MT5 setup section removed
  - Scalp vs swing layout awareness (scalp = 2 TPs, swing = 3 TPs)
  - Fixed crypto detection (covers XRP, DOGE, ADA, USDT pairs)
  - Single datetime import at top
  - Removed redundant _wrap utility (unused)
"""

from datetime import datetime, timezone


# ── Helpers ───────────────────────────────────────────────────────────────────

def _direction_emoji(direction: str) -> str:
    return "🟢" if direction.upper() == "BUY" else "🔴"


CRYPTO_KEYWORDS = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "USDT"}

def _is_crypto(pair: str) -> bool:
    return any(kw in pair.upper() for kw in CRYPTO_KEYWORDS)


# ── Main formatter ────────────────────────────────────────────────────────────

def format_trade_plan(plan: dict, balance: float) -> str:
    direction   = plan.get("direction", "")
    pair        = plan.get("pair", "N/A")
    trade_style = plan.get("trade_style", "swing").upper()
    dir_emoji   = _direction_emoji(direction)
    style_emoji = "⚡" if trade_style == "SCALP" else "📈"

    lines = []

    # ── Header ────────────────────────────────────────────────
    lines.append(f"📊 TRADE PLAN — {pair}  {style_emoji} {trade_style}")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"Direction   : {dir_emoji} {direction}")
    lines.append(f"Execution   : {plan.get('execution', 'N/A')}")
    lines.append(f"Session     : {plan.get('session', 'N/A')}")
    lines.append(f"Confluence  : ⭐ {plan.get('confluence_score', 'N/A')}/10")

    # ── Account ───────────────────────────────────────────────
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("💰 ACCOUNT")
    lines.append(f"Balance     : ${balance:,.2f}")
    lines.append(f"At Risk     : {plan.get('risk_amount', 'N/A')} ({plan.get('risk_percent', 'N/A')}%)")

    # ── Position Size ─────────────────────────────────────────
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("📐 POSITION SIZE")
    lines.append(f"Lot Size    : {plan.get('lot_size', 'N/A')}")
    lines.append(f"Pip Value   : {plan.get('pip_value', 'N/A')}")

    # ── Levels ────────────────────────────────────────────────
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("📍 LEVELS")
    lines.append(f"Market Price: {plan.get('current_market_price', 'N/A')}")
    lines.append(f"Entry       : {plan.get('entry', 'N/A')}")
    lines.append(
        f"Stop Loss   : {plan.get('stop_loss', 'N/A')} "
        f"({plan.get('stop_loss_pips', 'N/A')} pips)"
    )

    # ── Take Profits ──────────────────────────────────────────
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("🎯 TAKE PROFITS")

    tp_profit_keys = [
        "estimated_profit_at_tp1",
        "estimated_profit_at_tp2",
        "estimated_profit_at_tp3",
    ]
    for i, tp in enumerate(plan.get("take_profits", [])):
        profit = plan.get(tp_profit_keys[i], "N/A") if i < len(tp_profit_keys) else "N/A"
        lines.append(
            f"{tp['label']} → {tp['price']} | {tp.get('rr', 'N/A'):>6} "
            f"| Close {tp.get('partial_close', 'N/A')} → Est: {profit}"
        )
    lines.append(f"Total if all TPs hit → {plan.get('total_potential_profit', 'N/A')}")

    # ── Trailing Stop ─────────────────────────────────────────
    ts = plan.get("trailing_stop", {})
    if ts and ts.get("recommended"):
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("🔁 TRAILING STOP")
        lines.append(
            f"Activate at {ts.get('activate_at')} → Trail {ts.get('trail_distance')}"
        )
        lines.append(ts.get("rationale", ""))

    # ── Analysis ──────────────────────────────────────────────
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("📖 ANALYSIS")
    lines.append(plan.get("rationale", "N/A"))

    # ── Caution ───────────────────────────────────────────────
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("⚠️  CAUTION")
    lines.append(plan.get("caution", "N/A"))

    # ── Timestamp ─────────────────────────────────────────────
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(
        f"⏱ Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"
    )

    return "\n".join(lines)


# ── History formatter ─────────────────────────────────────────────────────────

def format_history_entry(trade: dict, index: int) -> str:
    """Format a single trade history row into a compact Telegram summary."""
    pair      = (trade.get("pair") or "N/A").upper()
    direction = (trade.get("direction") or "N/A").upper()
    entry     = trade.get("entry") or "N/A"
    sl        = trade.get("stop_loss") or "N/A"
    risk      = trade.get("risk_amount") or "N/A"
    score     = trade.get("confluence_score") or "?"
    created   = trade.get("created_at") or "N/A"
    style     = (trade.get("trade_style") or "swing").upper()

    emoji = _direction_emoji(direction)
    style_icon = "⚡" if style == "SCALP" else "📈"
    return (
        f"{index}. {emoji} {direction} {pair} {style_icon}{style}\n"
        f"   Entry: {entry}  SL: {sl}  Risk: {risk}  ⭐{score}/10\n"
        f"   📅 {created}"
    )
