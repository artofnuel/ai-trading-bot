"""
bot/formatter.py — Convert a Claude trade plan dict into Telegram messages.

Splits output into two messages to stay under Telegram's 4096 char limit:
  Message 1 — Trade Card  : levels, position size, TPs, account risk
  Message 2 — Analysis    : HTF bias, confluence breakdown, rationale,
                             caution, math check
"""

from datetime import datetime, timezone


def _direction_emoji(direction: str) -> str:
    return "🟢" if direction.upper() == "BUY" else "🔴"


# ── Message 1: Trade Card ─────────────────────────────────────────────────────

def format_trade_card(plan: dict, balance: float) -> str:
    """
    Core numbers only. Always short enough to fit in one Telegram message.
    """
    direction   = plan.get("direction", "")
    pair        = plan.get("pair", "N/A")
    trade_style = plan.get("trade_style", "swing").upper()
    dir_emoji   = _direction_emoji(direction)
    style_emoji = "⚡" if trade_style == "SCALP" else "📈"
    kz_active   = plan.get("kill_zone_active", False)
    kz_icon     = "✅" if kz_active else "⏳"

    lines = []

    # Header
    lines.append(f"📊 *TRADE PLAN — {pair}*  {style_emoji} {trade_style}")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"Direction  : {dir_emoji} *{direction}*")
    lines.append(f"Execution  : {plan.get('execution', 'N/A')}")
    lines.append(f"Session    : {plan.get('session', 'N/A')}  {kz_icon}")
    lines.append(f"Confluence : ⭐ {plan.get('confluence_score', 'N/A')}/10")

    # Key levels (PDH/PDL)
    key_levels = plan.get("key_levels", {})
    if key_levels.get("pdh") or key_levels.get("pdl"):
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("🗝 *KEY LEVELS*")
        if key_levels.get("pdh"):
            lines.append(f"PDH : {key_levels['pdh']}  ← liquidity above")
        if key_levels.get("pdl"):
            lines.append(f"PDL : {key_levels['pdl']}  ← liquidity below")

    # Account
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("💰 *ACCOUNT*")
    lines.append(f"Balance    : ${balance:,.2f}")
    lines.append(f"At Risk    : {plan.get('risk_amount', 'N/A')} ({plan.get('risk_percent', 'N/A')}%)")
    lines.append(f"Lot Size   : {plan.get('lot_size', 'N/A')}")
    lines.append(f"Pip Value  : {plan.get('pip_value', 'N/A')}")

    # Levels
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("📍 *LEVELS*")
    lines.append(f"Market Price : {plan.get('current_market_price', 'N/A')}")
    lines.append(f"Entry        : *{plan.get('entry', 'N/A')}*")
    lines.append(
        f"Stop Loss    : *{plan.get('stop_loss', 'N/A')}*"
        f"  ({plan.get('stop_loss_pips', 'N/A')} pips)"
    )

    # Take Profits
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("🎯 *TAKE PROFITS*")
    for tp in plan.get("take_profits", []):
        lines.append(
            f"{tp.get('label','TP')} → *{tp.get('price','N/A')}*"
            f"  {tp.get('rr','N/A'):>7}"
            f"  Close {tp.get('partial_close','N/A')}"
            f"  → {tp.get('profit','N/A')}"
        )
    lines.append(f"Total potential → *{plan.get('total_potential_profit', 'N/A')}*")

    # Trailing stop
    ts = plan.get("trailing_stop", {})
    if ts and ts.get("recommended"):
        trail_pips = ts.get("trail_distance_pips", ts.get("trail_distance", "N/A"))
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("🔁 *TRAILING STOP*")
        lines.append(f"Activate at {ts.get('activate_at')} → trail {trail_pips} pips")

    # Timestamp
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(
        f"⏱ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"
        f"  |  ➡️ See next message for analysis"
    )

    return "\n".join(lines)


# ── Message 2: Analysis Card ──────────────────────────────────────────────────

def format_analysis_card(plan: dict) -> str:
    """
    HTF bias, confluence breakdown, rationale, caution, math check.
    Everything the trader needs to understand WHY the trade exists.
    """
    lines = []

    pair        = plan.get("pair", "N/A")
    direction   = plan.get("direction", "")
    dir_emoji   = _direction_emoji(direction)

    lines.append(f"📖 *ANALYSIS — {pair}  {dir_emoji} {direction}*")

    # HTF Bias
    htf_bias = plan.get("htf_bias")
    if htf_bias:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("🧭 *HTF BIAS*")
        lines.append(htf_bias)

    # Entry rationale from key_levels
    entry_rationale = (plan.get("key_levels") or {}).get("entry_rationale")
    if entry_rationale:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("📌 *ENTRY RATIONALE*")
        lines.append(entry_rationale)

    # Confluence breakdown
    breakdown = plan.get("confluence_breakdown", {})
    if breakdown:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"⭐ *CONFLUENCE BREAKDOWN  ({plan.get('confluence_score', '?')}/10)*")
        label_map = {
            "htf_structure":    ("HTF Structure",       2.0),
            "kill_zone":        ("Kill Zone",            1.5),
            "ob_fvg":           ("OB / FVG at entry",   2.0),
            "liquidity_swept":  ("Liquidity swept",      1.5),
            "premium_discount": ("Premium/Discount zone",1.0),
            "bos_choch":        ("BOS / CHoCH",          1.0),
            "atr_sl_room":      ("ATR SL validity",      0.5),
            "rr_quality":       ("R:R quality",           0.5),
        }
        for key, (label, max_val) in label_map.items():
            val = breakdown.get(key, 0)
            filled = int((val / max_val) * 4) if max_val else 0
            bar = "█" * filled + "░" * (4 - filled)
            lines.append(f"`{label:<22}` {bar}  {val}/{max_val}")

    # Rationale
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("📝 *RATIONALE*")
    lines.append(plan.get("rationale", "N/A"))

    # Caution
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("⚠️ *CAUTION*")
    lines.append(plan.get("caution", "N/A"))

    # Math check
    math_check = plan.get("math_check")
    if math_check:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("🔢 *MATH VERIFIED*")
        lines.append(f"`{math_check}`")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("⚠️ _Advisory only. Always verify before placing any trade._")

    return "\n".join(lines)


# ── Combined convenience (returns list of two strings) ────────────────────────

def format_trade_plan(plan: dict, balance: float) -> list[str]:
    """
    Returns [card_message, analysis_message].
    handlers.py sends them sequentially.
    """
    return [
        format_trade_card(plan, balance),
        format_analysis_card(plan),
    ]


# ── History formatter ─────────────────────────────────────────────────────────

def format_history_entry(trade: dict, index: int) -> str:
    pair      = (trade.get("pair") or "N/A").upper()
    direction = (trade.get("direction") or "N/A").upper()
    entry     = trade.get("entry") or "N/A"
    sl        = trade.get("stop_loss") or "N/A"
    risk      = trade.get("risk_amount") or "N/A"
    score     = trade.get("confluence_score") or "?"
    created   = trade.get("created_at") or "N/A"
    style     = (trade.get("trade_style") or "swing").upper()

    emoji      = _direction_emoji(direction)
    style_icon = "⚡" if style == "SCALP" else "📈"
    return (
        f"{index}. {emoji} {direction} {pair}  {style_icon} {style}\n"
        f"   Entry: {entry}  SL: {sl}  Risk: {risk}  ⭐{score}/10\n"
        f"   📅 {created}"
    )
