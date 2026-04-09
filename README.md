# AI Forex & Crypto Trade Planner — Telegram Bot

An AI-powered trade planning bot built with **Python**, **python-telegram-bot v20+**, and **Claude (Anthropic)**. The bot analyses the Forex and Crypto markets and returns a fully structured trade recommendation including entry, stop loss, take profit levels, trailing stop guidance, and risk-adjusted sizing.

> **The bot advises. You decide and execute manually.**

---

## Features

- 📊 **Structured trade plans** — entry, SL, TP1/2/3, trailing stop, RR ratios
- ⚖️ **Risk-adjusted sizing** — conservative (1%), moderate (2%), aggressive (3%)
- 🤖 **AI pair selection** — let Claude pick the best opportunity if you don't specify a pair
- 💬 **Natural language input** — skip the menu and just type freely
- 🗄️ **Trade history** — all plans logged to SQLite, retrievable via `/history`
- 🔁 **Persistent user defaults** — save your balance with `/setbalance`

---

## Project Structure

```
trade-bot/
├── main.py               # Entry point
├── config.py             # Env var loader
├── requirements.txt      # Pinned dependencies
├── .env.example          # Template env file
├── tradingbot.service    # systemd service unit
├── bot/
│   ├── handlers.py       # All Telegram command & message handlers
│   ├── keyboards.py      # Inline keyboard definitions
│   └── formatter.py      # Trade plan → Telegram message renderer
├── ai/
│   └── analyst.py        # Claude API integration & prompt logic
└── db/
    └── database.py       # Async SQLite layer (aiosqlite)
```

---

## Local Setup

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/trade-bot.git
cd trade-bot
```

### 2. Create your virtual environment

```bash
python3 -m venv venv
source venv/bin/activate        # Linux / macOS
# venv\Scripts\activate         # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in your real keys:

```
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
ANTHROPIC_API_KEY=your_anthropic_api_key
```

> Get your Telegram token from [@BotFather](https://t.me/BotFather).  
> Get your Anthropic key from [console.anthropic.com](https://console.anthropic.com).

### 5. Run locally

```bash
python main.py
```

---

## VPS Deployment (Google Cloud e2-micro — Ubuntu)

### 1. SSH into your VPS

```bash
ssh your_username@YOUR_VPS_IP
```

### 2. Install dependencies

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install python3.11 python3.11-venv python3-pip git -y
```

### 3. Clone the repo and set up

```bash
git clone https://github.com/YOUR_USERNAME/trade-bot.git
cd trade-bot
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env   # add your real keys
```

### 4. Configure the systemd service

```bash
# Edit the service file to match your username and paths
sudo nano /etc/systemd/system/tradingbot.service
```

Paste the contents of [`tradingbot.service`](./tradingbot.service), replacing `your_vps_username` with your actual username.

```bash
sudo systemctl daemon-reload
sudo systemctl enable tradingbot
sudo systemctl start tradingbot
```

### 5. Check service status

```bash
sudo systemctl status tradingbot
# View live logs:
sudo journalctl -u tradingbot -f
```

---

## Telegram Commands

| Command | Description |
|---|---|
| `/start` | Welcome message and quick overview |
| `/trade` | Start a guided trade analysis |
| `/setbalance <amount>` | Save your default account balance |
| `/history` | View your last 5 trade plans |
| `/help` | Show all commands and usage tips |

### Natural Language Examples

```
I have $500, analyse EUR/USD for me, moderate risk
$1000 account, pick the best crypto pair, aggressive
Check GBP/USD, conservative, I think NFP might move it
```

---

## Environment Variables

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token from @BotFather |
| `ANTHROPIC_API_KEY` | Key from console.anthropic.com |

---

## Tech Stack

| Layer | Tool |
|---|---|
| Language | Python 3.11+ |
| Telegram | python-telegram-bot v21 (async) |
| AI Engine | Anthropic Claude (`claude-sonnet-4-5-20251001`) |
| Storage | SQLite via aiosqlite |
| Config | python-dotenv |
| Hosting | Google Cloud VPS (e2-micro, Ubuntu) |
| Process Manager | systemd |

---

## Important Notes

- **Never commit your `.env` file** — it contains secret API keys
- The bot uses **long polling** — no webhook setup required
- All trade plans are saved locally in `trade_bot.db` (SQLite)
- Claude has a **30-second timeout**; if it times out, the user receives a graceful error
- The bot does **not** execute trades — it is an advisory tool only

---

## License

MIT
