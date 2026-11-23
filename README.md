# Tomorn 🐼 — Crypto Trading Bot with Telegram Alerts & Live Dashboard

A rule-based BTC/USDT trading bot for **Binance TH (Spot)**, written in plain Python with no third-party packages. It watches the market around the clock, finds signals from five technical strategies, sends trade alerts to **Telegram**, paper-trades with simulated money, and shows everything on a **web dashboard** with a panda mascot that reacts to the bot's status.

> Built with AI-assisted development (Claude Code). Strategy design, testing and operation by Pumara Thaworn.

---

## What it does

| Mode | What happens | Real money |
|---|---|---|
| `alert` | Sends each signal to Telegram with entry, stop-loss, take-profit and position size | No |
| `paper` | Trades with simulated money, reports every trade and a daily summary at 08:00 | No |
| `live` | Places real orders on Binance TH, only after passing a safety gate | Yes, gated |

**Safety built in**
- Risk capped at 1% of capital per trade, **including fees on both legs**
- Trades rejected if net reward:risk after fees is below 1.5
- Kill switch stops new trades after a 10% drawdown from peak
- Live trading stays locked until 30 paper trades with profit factor ≥ 1.2
- API keys live in `.env` (never committed) and need Spot permission only, no withdrawals

## How it works

```
Binance TH API ──► runner.py ──► state/*.json ──► dashboard.py ──► dashboard.html
  (1h candles)       │  find signals on 4h            (reads files)     (charts + mascot)
                     └──► Telegram Bot API (alerts, daily summary)
```

| File | Role |
|---|---|
| `bot/config.py` | All settings in one place |
| `bot/fees.py` | Position sizing and fee-aware reward:risk |
| `bot/backtest.py` | Indicators (EMA, RSI, ADX, fractals), strategies S1–S5, backtest engine and metrics |
| `bot/runner.py` | The bot: exchange client (HMAC-signed orders), candle store, paper/live brokers, Telegram, main loop |
| `bot/dashboard.py` | Small web server (standard library only) serving `/api/data` |
| `bot/dashboard.html` | Single-file dashboard: hand-drawn SVG candlestick chart, EMA lines, entries/exits, SL/TP, equity curve |

## Strategies tested (4h candles, daily trend filter)

1. EMA trend pullback · 2. Support/resistance flip · 3. Order block · 4. RSI divergence · 5. Range box

## Backtest results — honest version

5 years, 5 coins (BTC, ETH, BNB, SOL, XRP), realistic costs of 0.12% per order. Full table in [`bot/results/summary.md`](bot/results/summary.md).

- No strategy was consistently profitable after real trading costs.
- Max drawdown stayed **under 25%**, against **71–97%** for buy-and-hold over the same period.
- Conclusion: the bot runs in paper mode to collect live statistics before any real money is used.

Being clear about what did *not* work is part of the result: the backtest kept the project from risking money on an unproven edge.

## Run it

Requires Python 3 and a Telegram bot token from @BotFather.

```bash
cd bot
cp .env.example .env          # add TELEGRAM_TOKEN
python3 runner.py --setup-telegram
python3 runner.py --once      # check the latest candle once
python3 runner.py             # run the bot
python3 dashboard.py          # open http://localhost:8080
python3 backtest.py --symbols BTCUSDT --years 5
```

More detail (Thai): [`bot/README_bot.md`](bot/README_bot.md) · [`bot/README_backtest.md`](bot/README_backtest.md)

---

*Educational project. Not financial advice.*
