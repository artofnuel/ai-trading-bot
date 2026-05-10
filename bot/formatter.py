"""
bot/formatter.py — Convert a Claude trade plan dict into a clean Telegram message.

v2 additions:
  - PDH/PDL display
  - Kill zone status
  - HTF bias line
  - Confluence breakdown (weighted factors)
  - Math check field shown for transparency
  - Crypto removed
"""

from datetime import datetime, timezone


# ── Helpers ───────────────────────────────────────────────────────────────────

def _direction_emoji(direction: str) -> str:
    return "🟢" if direction.upper() == "BUY" else "🔴"


# ── Main formatter ────────────────────────────────────────────────────────────

def format_trade_plan(plan: dict, balance: float) -> str:
    direction   = plan.get("direction", "")
    pair        = plan.get("pair", "N/A")
    trade_style = plan.get("trade_style", "swing").upper()
    dir_emoji   = _direction_emoji(direction)
    style_emoji = "⚡" if trade_style == "SCALP" else "📈"
    kz_active   = plan.get("kill_zone_active", False)
    kz_icon     = "✅" if kz_active else "⏳"

    lines = []

    # ── Header ────────────────────────────────────────────────
    lines.append(f"📊 TRADE PLAN — {pair}  {style_emoji} {trade_style}")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"Direction   : {dir_emoji} {direction}")
    lines.append(f"Execution   : {plan.get('execution', 'N/A')}")
    lines.append(f"Session     : {plan.get('session', 'N/A')}  {kz_icon} {'Kill Zone' if kz_active else 'Outside KZ'}")
    lines.append(f"Confluence  : ⭐ {plan.get('confluence_score', 'N/A')}/10")

    # ── HTF Bias ──────────────────────────────────────────────
    htf_bias = plan.get("htf_bias")
    if htf_bias:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("🧭 HTF BIAS")
        lines.append(htf_bias)

    # ── Key Levels (PDH/PDL) ──────────────────────────────────
    key_levels = plan.get("key_levels", {})
    if key_levels:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("🗝 KEY LEVELS")
        if key_levels.get("pdh"):
            lines.append(f"PDH (Liquidity) : {key_levels['pdh']}")
        if key_levels.get("pdl"):
            lines.append(f"PDL (Liquidity) : {key_levels['pdl']}")
        if key_levels.get("entry_rationale"):
            lines.append(f"Entry Reason    : {key_levels['entry_rationale']}")

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
    for tp in plan.get("take_profits", []):
        profit = tp.get("profit", "N/A")
        lines.append(
            f"{tp['label']} → {tp['price']} | {tp.get('rr', 'N/A'):>6} "
            f"| Close {tp.get('partial_close', 'N/A')} → {profit}"
        )
    lines.append(f"Total if all TPs hit → {plan.get('total_potential_profit', 'N/A')}")

    # ── Trailing Stop ─────────────────────────────────────────
    ts = plan.get("trailing_stop", {})
    if ts and ts.get("recommended"):
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("🔁 TRAILING STOP")
        trail_pips = ts.get("trail_distance_pips", ts.get("trail_distance", "N/A"))
        lines.append(
            f"Activate at {ts.get('activate_at')} → Trail {trail_pips} pips"
        )
        lines.append(ts.get("rationale", ""))

    # ── Confluence Breakdown ───────────────────────────────────
    breakdown = plan.get("confluence_breakdown", {})
    if breakdown:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("⭐ CONFLUENCE BREAKDOWN")
        label_map = {
            "htf_structure":   "HTF Structure",
            "kill_zone":       "Kill Zone",
            "ob_fvg":          "OB / FVG at entry",
            "liquidity_swept": "Liquidity swept",
            "premium_discount":"Premium/Discount zone",
            "bos_choch":       "BOS / CHoCH",
            "atr_sl_room":     "ATR SL validity",
            "rr_quality":      "R:R quality",
        }
        for key, label in label_map.items():
            val = breakdown.get(key, 0)
            bar = "█" * int(val * 2)
            lines.append(f"{label:<22}: {val:.1f}  {bar}")

    # ── Analysis ──────────────────────────────────────────────
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("📖 ANALYSIS")
    lines.append(plan.get("rationale", "N/A"))

    # ── Caution ───────────────────────────────────────────────
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("⚠️  CAUTION")
    lines.append(plan.get("caution", "N/A"))

    # ── Math Check ────────────────────────────────────────────
    math_check = plan.get("math_check")
    if math_check:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("🔢 MATH VERIFIED")
        lines.append(math_check)

    # ── Timestamp ─────────────────────────────────────────────
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(
        f"⏱ Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"
    )

    return "\n".join(lines)


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

    emoji = _direction_emoji(direction)
    style_icon = "⚡" if style == "SCALP" else "📈"
    return (
        f"{index}. {emoji} {direction} {pair} {style_icon}{style}\n"
        f"   Entry: {entry}  SL: {sl}  Risk: {risk}  ⭐{score}/10\n"
        f"   📅 {created}"
    )
