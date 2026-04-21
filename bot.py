"""
BingX Pump & Dump Alert Bot
Phiên bản GitHub Actions - chạy 1 lần rồi thoát
"""

import asyncio, logging, time, io, os, json
from datetime import datetime, timezone

import requests
import mplfinance as mpf
import pandas as pd
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

# ─── Đọc từ GitHub Secrets ───────────────────
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]
# ─────────────────────────────────────────────

THRESHOLDS    = [20.0, 40.0, 60.0]
QUOTE_ASSET   = "USDT"
CANDLE_LIMIT  = 90
COOLDOWN_FILE = "cooldown.json"
BINGX_BASE    = "https://open-api.bingx.com"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ── Cooldown ──────────────────────────────────
def load_cooldown() -> dict:
    if os.path.exists(COOLDOWN_FILE):
        try:
            with open(COOLDOWN_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_cooldown(data: dict):
    with open(COOLDOWN_FILE, "w") as f:
        json.dump(data, f)

def is_on_cooldown(cd: dict, symbol: str, hours: float = 4) -> bool:
    return (time.time() - cd.get(symbol, 0)) < hours * 3600

def set_cooldown(cd: dict, symbol: str):
    cd[symbol] = time.time()


# ── BingX API ─────────────────────────────────
def get_all_tickers() -> list:
    try:
        r = requests.get(f"{BINGX_BASE}/openApi/spot/v1/ticker/24hr", timeout=15)
        d = r.json()
        if d.get("code") == 0:
            return d.get("data", [])
    except Exception as e:
        logger.error(f"Lỗi ticker: {e}")
    return []

def get_klines_day(symbol: str) -> list:
    try:
        r = requests.get(
            f"{BINGX_BASE}/openApi/spot/v1/market/kline",
            params={"symbol": symbol, "interval": "1d", "limit": CANDLE_LIMIT},
            timeout=15,
        )
        d = r.json()
        if d.get("code") == 0:
            return d.get("data", [])
    except Exception as e:
        logger.error(f"Lỗi kline [{symbol}]: {e}")
    return []


# ── Biểu đồ Day ───────────────────────────────
def build_chart(symbol: str, klines: list, change_pct: float):
    if len(klines) < 5:
        return None
    try:
        rows = [{"Date": pd.Timestamp(int(k[0]), unit="ms", tz="UTC"),
                 "Open": float(k[1]), "High": float(k[2]),
                 "Low":  float(k[3]), "Close": float(k[4]),
                 "Volume": float(k[5])} for k in klines]
        df = pd.DataFrame(rows).set_index("Date").sort_index()

        is_pump = change_pct > 0
        up  = "#00e676" if is_pump else "#ef5350"
        dn  = "#ef5350" if is_pump else "#b71c1c"

        mc    = mpf.make_marketcolors(up=up, down=dn,
                                      edge={"up": up, "down": dn},
                                      wick={"up": up, "down": dn},
                                      volume={"up": up, "down": dn})
        style = mpf.make_mpf_style(base_mpf_style="nightclouds", marketcolors=mc,
                                    gridstyle="--", gridcolor="#1e1e2e",
                                    facecolor="#0d0d1a", edgecolor="#0d0d1a",
                                    figcolor="#0d0d1a", y_on_right=True,
                                    rc={"font.size": 9,
                                        "xtick.color": "#666688",
                                        "ytick.color": "#666688"})

        df["EMA20"] = df["Close"].ewm(span=20).mean()
        df["EMA50"] = df["Close"].ewm(span=50).mean()
        adds = [mpf.make_addplot(df["EMA20"], color="#f9ca24", width=1.2),
                mpf.make_addplot(df["EMA50"], color="#a29bfe", width=1.2)]

        title = f"{symbol}  |  {'🚀 PUMP' if is_pump else '📉 DUMP'}  {change_pct:+.2f}%  |  Khung: Day"
        buf = io.BytesIO()
        mpf.plot(df, type="candle", style=style, title=title,
                 volume=True, addplot=adds, figsize=(12, 7), tight_layout=True,
                 savefig=dict(fname=buf, dpi=130, bbox_inches="tight"))
        buf.seek(0)
        return buf
    except Exception as e:
        logger.error(f"Lỗi chart [{symbol}]: {e}")
        return None


# ── Tin nhắn ──────────────────────────────────
def fmt_price(p):
    if p >= 1:     return f"{p:.4f}"
    if p >= 0.001: return f"{p:.6f}"
    return f"{p:.8f}"

def fmt_vol(v):
    if v >= 1_000_000: return f"{v/1_000_000:.2f}M"
    if v >= 1_000:     return f"{v/1_000:.1f}K"
    return f"{v:.2f}"

def build_message(ticker: dict) -> str:
    symbol = ticker["symbol"]
    price  = float(ticker.get("lastPrice", 0))
    change = float(ticker.get("priceChangePercent", 0))
    volume = float(ticker.get("quoteVolume", 0))
    high   = float(ticker.get("highPrice", 0))
    low    = float(ticker.get("lowPrice", 0))

    is_pump = change > 0
    icon  = "💹" if is_pump else "📉"
    arrow = "🚀" if is_pump else "🔥"
    bar   = "🟢" if is_pump else "🔴"
    trend = "PUMP" if is_pump else "DUMP"
    abs_c = abs(change)
    tag   = "#P60 ✨🌀" if abs_c >= 60 else "#P40 🔆☀" if abs_c >= 40 else f"#Percent_20 {arrow}"
    now   = datetime.now(timezone.utc).strftime("%H:%M UTC")
    coin  = symbol.replace(QUOTE_ASSET, "")

    return (
        f"{icon} <b>#{coin}</b>  <code>${fmt_price(price)}</code>  <b>{change:+.2f}%</b>  {arrow}\n"
        f"{bar} {tag}\n\n"
        f"📊 <b>24h Vol:</b> <code>{fmt_vol(volume)}</code>\n"
        f"📈 <b>High:</b> <code>${fmt_price(high)}</code>\n"
        f"📉 <b>Low:</b>  <code>${fmt_price(low)}</code>\n\n"
        f"🕐 {now}  |  Sàn: <b>BingX Spot</b>\n"
        f"#BingX #{trend} #Crypto"
    )


# ── Main ──────────────────────────────────────
async def main():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    cd  = load_cooldown()

    me = await bot.get_me()
    logger.info(f"✅ Bot: @{me.username}")

    tickers = get_all_tickers()
    usdt    = [t for t in tickers if t.get("symbol","").endswith(QUOTE_ASSET)]
    logger.info(f"📊 Cặp USDT: {len(usdt)}")

    alerts = 0
    for ticker in usdt:
        symbol = ticker.get("symbol", "")
        try:
            change = float(ticker.get("priceChangePercent", 0))
        except Exception:
            continue

        abs_c     = abs(change)
        triggered = next((t for t in sorted(THRESHOLDS, reverse=True) if abs_c >= t), None)
        if not triggered or is_on_cooldown(cd, symbol):
            continue

        logger.info(f"🎯 {symbol} {change:+.2f}%")
        chart   = build_chart(symbol, get_klines_day(symbol), change)
        caption = build_message(ticker)

        try:
            if chart:
                await bot.send_photo(chat_id=TELEGRAM_CHANNEL_ID, photo=chart,
                                     caption=caption, parse_mode=ParseMode.HTML)
            else:
                await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID,
                                       text=caption, parse_mode=ParseMode.HTML)
            set_cooldown(cd, symbol)
            alerts += 1
            await asyncio.sleep(2)
        except TelegramError as e:
            logger.error(f"Telegram lỗi [{symbol}]: {e}")

    save_cooldown(cd)
    logger.info(f"✅ Xong! Gửi {alerts} alert.")

if __name__ == "__main__":
    asyncio.run(main())
