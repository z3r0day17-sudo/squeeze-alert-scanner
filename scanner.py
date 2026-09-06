import os
import sys
import math
import requests
import yfinance as yf

from datetime import datetime, time
from zoneinfo import ZoneInfo


# ============================================================
# CONFIGURATION
# ============================================================

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# Stock filters
MIN_PRICE = 3.00
MAX_PRICE = 60.00

# Momentum / volume filters
MIN_RELATIVE_VOLUME = 1.50
MIN_DAILY_GAIN = 2.0
MAX_DAILY_GAIN = 25.0

# Short squeeze filters
MIN_SHORT_FLOAT = 10.0
MIN_AVG_DOLLAR_VOLUME = 10_000_000

# Minimum squeeze score required for alert
MIN_SCORE = 70

# Options liquidity filters
MAX_OPTION_SPREAD = 0.15
MIN_OPTION_OPEN_INTEREST = 100

# Scanner limits
MAX_CANDIDATES_TO_SCAN = 75
MAX_ALERTS_PER_RUN = 3

# U.S. stock market timezone
MARKET_TIMEZONE = ZoneInfo("America/New_York")

MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)


# ============================================================
# HELPERS
# ============================================================

def safe_float(value, default=0.0):
    try:
        value = float(value)

        if math.isnan(value):
            return default

        return value

    except (TypeError, ValueError):
        return default


def send_discord_message(message):
    """
    Send message to Discord.
    """

    if not DISCORD_WEBHOOK_URL:
        print("ERROR: DISCORD_WEBHOOK_URL secret is missing.")
        sys.exit(1)

    try:
        response = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": message},
            timeout=20,
        )

        response.raise_for_status()

        print("Discord message sent.")

        return True

    except requests.RequestException as error:
        print(f"Discord error: {error}")

        return False


# ============================================================
# MARKET HOURS
# ============================================================

def market_is_open():
    """
    Normal U.S. equity market hours:

    Monday-Friday
    9:30 AM - 4:00 PM Eastern

    America/New_York automatically handles daylight saving.
    """

    now = datetime.now(MARKET_TIMEZONE)

    # Saturday / Sunday
    if now.weekday() >= 5:
        return False

    current_time = now.time()

    return MARKET_OPEN <= current_time <= MARKET_CLOSE


# ============================================================
# DYNAMIC STOCK DISCOVERY
# ============================================================

def get_dynamic_candidates():
    """
    Discover squeeze candidates dynamically.

    Uses:
    - Most active stocks
    - Day gainers
    - Small-cap gainers

    No fixed watchlist.
    """

    symbols = []
    seen = set()

    screener_urls = [
        (
            "https://query1.finance.yahoo.com/v1/finance/"
            "screener/predefined/saved?"
            "scrIds=most_actives&count=100"
        ),
        (
            "https://query1.finance.yahoo.com/v1/finance/"
            "screener/predefined/saved?"
            "scrIds=day_gainers&count=100"
        ),
        (
            "https://query1.finance.yahoo.com/v1/finance/"
            "screener/predefined/saved?"
            "scrIds=small_cap_gainers&count=100"
        ),
    ]

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/120 Safari/537.36"
        )
    }

    for url in screener_urls:

        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=20,
            )

            response.raise_for_status()

            data = response.json()

            results = (
                data
                .get("finance", {})
                .get("result", [])
            )

            if not results:
                continue

            quotes = results[0].get(
                "quotes",
                [],
            )

            for quote in quotes:

                symbol = quote.get("symbol")

                quote_type = quote.get(
                    "quoteType",
                    ""
                )

                if not symbol:
                    continue

                # Equities only
                if quote_type and quote_type != "EQUITY":
                    continue

                # Skip indexes/currencies/etc.
                if "^" in symbol or "=" in symbol:
                    continue

                if symbol not in seen:

                    seen.add(symbol)

                    symbols.append(symbol)

        except Exception as error:

            print(
                f"Dynamic screener error: {error}"
            )

    print(
        f"Dynamic discovery found "
        f"{len(symbols)} unique stocks."
    )

    return symbols[:MAX_CANDIDATES_TO_SCAN]


# ============================================================
# VOLUME CALCULATIONS
# ============================================================

def calculate_relative_volume(history):
    """
    Current volume compared with prior 20-day average.
    """

    if len(history) < 21:
        return 0.0

    current_volume = safe_float(
        history["Volume"].iloc[-1]
    )

    prior_volume = (
        history["Volume"]
        .iloc[-21:-1]
    )

    average_volume = safe_float(
        prior_volume.mean()
    )

    if average_volume <= 0:
        return 0.0

    return current_volume / average_volume


def calculate_average_dollar_volume(history):
    """
    Calculate approximate 20-day average dollar volume.
    """

    if len(history) < 20:
        return 0.0

    recent = history.tail(20)

    dollar_volume = (
        recent["Close"]
        * recent["Volume"]
    )

    return safe_float(
        dollar_volume.mean()
    )


# ============================================================
# SQUEEZE SCORE
# ============================================================

def calculate_score(
    daily_gain,
    relative_volume,
    short_percent,
    days_to_cover,
    avg_dollar_volume,
):
    """
    Score squeeze potential from 0-100.
    """

    score = 0
    reasons = []

    # --------------------------------------------------------
    # SHORT FLOAT
    # Maximum 30 points
    # --------------------------------------------------------

    if short_percent >= 30:

        score += 30

        reasons.append(
            f"Extremely high short float: "
            f"{short_percent:.1f}%"
        )

    elif short_percent >= 25:

        score += 27

        reasons.append(
            f"Very high short float: "
            f"{short_percent:.1f}%"
        )

    elif short_percent >= 20:

        score += 24

        reasons.append(
            f"High short float: "
            f"{short_percent:.1f}%"
        )

    elif short_percent >= 15:

        score += 20

        reasons.append(
            f"Elevated short float: "
            f"{short_percent:.1f}%"
        )

    elif short_percent >= 10:

        score += 12

        reasons.append(
            f"Short float: "
            f"{short_percent:.1f}%"
        )

    # --------------------------------------------------------
    # DAYS TO COVER
    # Maximum 20 points
    # --------------------------------------------------------

    if days_to_cover >= 7:

        score += 20

        reasons.append(
            f"Very high days to cover: "
            f"{days_to_cover:.1f}"
        )

    elif days_to_cover >= 5:

        score += 18

        reasons.append(
            f"High days to cover: "
            f"{days_to_cover:.1f}"
        )

    elif days_to_cover >= 3:

        score += 14

        reasons.append(
            f"Days to cover: "
            f"{days_to_cover:.1f}"
        )

    elif days_to_cover >= 2:

        score += 8

    # --------------------------------------------------------
    # RELATIVE VOLUME
    # Maximum 25 points
    # --------------------------------------------------------

    if relative_volume >= 4:

        score += 25

        reasons.append(
            f"Extreme relative volume: "
            f"{relative_volume:.1f}x"
        )

    elif relative_volume >= 3:

        score += 22

        reasons.append(
            f"Very strong relative volume: "
            f"{relative_volume:.1f}x"
        )

    elif relative_volume >= 2:

        score += 18

        reasons.append(
            f"Strong relative volume: "
            f"{relative_volume:.1f}x"
        )

    elif relative_volume >= 1.5:

        score += 13

        reasons.append(
            f"Elevated relative volume: "
            f"{relative_volume:.1f}x"
        )

    # --------------------------------------------------------
    # PRICE MOMENTUM
    # Maximum 15 points
    # --------------------------------------------------------

    if 5 <= daily_gain <= 12:

        score += 15

        reasons.append(
            f"Strong early momentum: "
            f"+{daily_gain:.1f}%"
        )

    elif 2 <= daily_gain < 5:

        score += 11

        reasons.append(
            f"Early price momentum: "
            f"+{daily_gain:.1f}%"
        )

    elif 12 < daily_gain <= 18:

        score += 10

        reasons.append(
            f"Strong momentum: "
            f"+{daily_gain:.1f}%"
        )

    elif 18 < daily_gain <= 25:

        score += 5

        reasons.append(
            f"Momentum elevated but extended: "
            f"+{daily_gain:.1f}%"
        )

    # --------------------------------------------------------
    # LIQUIDITY
    # Maximum 10 points
    # --------------------------------------------------------

    if avg_dollar_volume >= 100_000_000:

        score += 10

        reasons.append(
            "Excellent trading liquidity"
        )

    elif avg_dollar_volume >= 50_000_000:

        score += 8

    elif avg_dollar_volume >= 20_000_000:

        score += 6

    elif avg_dollar_volume >= MIN_AVG_DOLLAR_VOLUME:

        score += 4

    return min(score, 100), reasons


# ============================================================
# OPTIONS ANALYSIS
# ============================================================

def find_call_candidates(stock, price):
    """
    Select up to two call candidates:

    Lower-risk:
    ATM / slightly ITM

    Higher-risk:
    5%-15% OTM
    """

    try:

        expirations = stock.options

        if not expirations:
            return []

        candidates = []

        # Look through nearest five expirations
        for expiration in expirations[:5]:

            try:

                chain = stock.option_chain(
                    expiration
                )

                calls = chain.calls.copy()

                if calls.empty:
                    continue

                calls = calls[
                    (
                        calls["strike"]
                        >= price * 0.90
                    )
                    &
                    (
                        calls["strike"]
                        <= price * 1.20
                    )
                ]

                for _, option in calls.iterrows():

                    strike = safe_float(
                        option.get("strike")
                    )

                    bid = safe_float(
                        option.get("bid")
                    )

                    ask = safe_float(
                        option.get("ask")
                    )

                    volume = safe_float(
                        option.get("volume")
                    )

                    open_interest = safe_float(
                        option.get(
                            "openInterest"
                        )
                    )

                    iv = safe_float(
                        option.get(
                            "impliedVolatility"
                        )
                    )

                    if bid <= 0 or ask <= 0:
                        continue

                    midpoint = (
                        bid + ask
                    ) / 2

                    if midpoint <= 0:
                        continue

                    spread = (
                        ask - bid
                    ) / midpoint

                    if spread > MAX_OPTION_SPREAD:
                        continue

                    if (
                        open_interest
                        < MIN_OPTION_OPEN_INTEREST
                    ):
                        continue

                    breakeven = (
                        strike + midpoint
                    )

                    candidates.append(
                        {
                            "expiration":
                                expiration,

                            "strike":
                                strike,

                            "bid":
                                bid,

                            "ask":
                                ask,

                            "mid":
                                midpoint,

                            "volume":
                                volume,

                            "open_interest":
                                open_interest,

                            "iv":
                                iv * 100,

                            "spread_pct":
                                spread * 100,

                            "breakeven":
                                breakeven,
                        }
                    )

            except Exception as error:

                print(
                    f"Option chain error "
                    f"{expiration}: {error}"
                )

        if not candidates:
            return []

        # ----------------------------------------------------
        # LOWER-RISK
        # ----------------------------------------------------

        conservative_choices = [
            option
            for option in candidates
            if (
                price * 0.95
                <= option["strike"]
                <= price * 1.03
            )
        ]

        conservative = None

        if conservative_choices:

            conservative_choices.sort(
                key=lambda option: (
                    abs(
                        option["strike"]
                        - price
                    ),
                    -option[
                        "open_interest"
                    ],
                    option[
                        "spread_pct"
                    ],
                )
            )

            conservative = (
                conservative_choices[0]
            )

        # ----------------------------------------------------
        # HIGHER-RISK
        # ----------------------------------------------------

        aggressive_choices = [
            option
            for option in candidates
            if (
                price * 1.05
                <= option["strike"]
                <= price * 1.15
            )
        ]

        aggressive = None

        if aggressive_choices:

            aggressive_choices.sort(
                key=lambda option: (
                    -option[
                        "open_interest"
                    ],
                    option[
                        "spread_pct"
                    ],
                    abs(
                        option["strike"]
                        - price * 1.08
                    ),
                )
            )

            aggressive = (
                aggressive_choices[0]
            )

        results = []

        if conservative:

            results.append(
                (
                    "Lower-risk",
                    conservative,
                )
            )

        if aggressive:

            results.append(
                (
                    "Higher-risk",
                    aggressive,
                )
            )

        return results

    except Exception as error:

        print(
            f"Option lookup error: {error}"
        )

        return []


# ============================================================
# STOCK ANALYSIS
# ============================================================

def analyze_stock(symbol):

    print(f"Scanning {symbol}...")

    try:

        stock = yf.Ticker(symbol)

        history = stock.history(
            period="3mo",
            interval="1d",
            auto_adjust=False,
        )

        if history is None or len(history) < 21:
            return None

        price = safe_float(
            history["Close"].iloc[-1]
        )

        previous_close = safe_float(
            history["Close"].iloc[-2]
        )

        if price <= 0 or previous_close <= 0:
            return None

        # ----------------------------------------------------
        # PRICE FILTER
        # ----------------------------------------------------

        if (
            price < MIN_PRICE
            or price > MAX_PRICE
        ):
            return None

        daily_gain = (
            (
                price - previous_close
            )
            / previous_close
        ) * 100

        if daily_gain < MIN_DAILY_GAIN:
            return None

        # Avoid names that may already be too extended
        if daily_gain > MAX_DAILY_GAIN:
            return None

        # ----------------------------------------------------
        # RELATIVE VOLUME
        # ----------------------------------------------------

        relative_volume = (
            calculate_relative_volume(
                history
            )
        )

        if (
            relative_volume
            < MIN_RELATIVE_VOLUME
        ):
            return None

        # ----------------------------------------------------
        # LIQUIDITY
        # ----------------------------------------------------

        avg_dollar_volume = (
            calculate_average_dollar_volume(
                history
            )
        )

        if (
            avg_dollar_volume
            < MIN_AVG_DOLLAR_VOLUME
        ):
            return None

        # ----------------------------------------------------
        # YAHOO FUNDAMENTAL DATA
        # ----------------------------------------------------

        info = stock.info or {}

        shares_short = safe_float(
            info.get("sharesShort")
        )

        float_shares = safe_float(
            info.get("floatShares")
        )

        average_volume = safe_float(
            info.get("averageVolume")
        )

        short_percent = 0.0

        reported_short_percent = safe_float(
            info.get("shortPercentOfFloat")
        )

        # Yahoo often reports this as decimal
        if reported_short_percent > 0:

            if reported_short_percent <= 1:

                short_percent = (
                    reported_short_percent
                    * 100
                )

            else:

                short_percent = (
                    reported_short_percent
                )

        elif (
            shares_short > 0
            and float_shares > 0
        ):

            short_percent = (
                shares_short
                / float_shares
            ) * 100

        # ----------------------------------------------------
        # SHORT FLOAT FILTER
        # ----------------------------------------------------

        if short_percent < MIN_SHORT_FLOAT:
            return None

        # ----------------------------------------------------
        # DAYS TO COVER
        # ----------------------------------------------------

        days_to_cover = 0.0

        if (
            shares_short > 0
            and average_volume > 0
        ):

            days_to_cover = (
                shares_short
                / average_volume
            )

        # ----------------------------------------------------
        # SCORE
        # ----------------------------------------------------

        score, reasons = calculate_score(
            daily_gain,
            relative_volume,
            short_percent,
            days_to_cover,
            avg_dollar_volume,
        )

        return {
            "symbol": symbol,
            "price": price,
            "daily_gain": daily_gain,
            "relative_volume": relative_volume,
            "short_percent": short_percent,
            "days_to_cover": days_to_cover,
            "avg_dollar_volume": avg_dollar_volume,
            "score": score,
            "reasons": reasons,
            "stock": stock,
        }

    except Exception as error:

        print(
            f"Error scanning "
            f"{symbol}: {error}"
        )

        return None


# ============================================================
# CREATE DISCORD SQUEEZE ALERT
# ============================================================

def create_alert(result):

    symbol = result["symbol"]

    score = result["score"]

    price = result["price"]

    short_percent = (
        result["short_percent"]
    )

    days_to_cover = (
        result["days_to_cover"]
    )

    relative_volume = (
        result["relative_volume"]
    )

    daily_gain = (
        result["daily_gain"]
    )

    avg_dollar_volume = (
        result["avg_dollar_volume"]
    )

    # --------------------------------------------------------
    # SIGNAL
    # --------------------------------------------------------

    if score >= 90:
        signal = "🔥 VERY STRONG"

    elif score >= 80:
        signal = "🟢 STRONG"

    elif score >= 70:
        signal = "🟡 WATCH"

    else:
        signal = "⚪ WEAK"

    # --------------------------------------------------------
    # OPTION CONTRACTS
    # --------------------------------------------------------

    option_candidates = (
        find_call_candidates(
            result["stock"],
            price,
        )
    )

    message = (
        f"🚨 **SQUEEZE ALERT — {symbol}**\n\n"

        f"🔥 **Squeeze Score:** "
        f"{score}/100\n"

        f"🎯 **Signal:** "
        f"{signal}\n\n"

        f"💵 **Stock Price:** "
        f"${price:.2f}\n"

        f"📈 **Daily Move:** "
        f"{daily_gain:+.1f}%\n"

        f"📊 **Relative Volume:** "
        f"{relative_volume:.2f}x\n"

        f"🩳 **Short Float:** "
        f"{short_percent:.1f}%\n"

        f"⏳ **Days to Cover:** "
        f"{days_to_cover:.2f}\n"

        f"💰 **20D Avg Dollar Volume:** "
        f"${avg_dollar_volume / 1_000_000:.1f}M\n\n"
    )

    # --------------------------------------------------------
    # TRIGGER REASONS
    # --------------------------------------------------------

    if result["reasons"]:

        message += (
            "**Why the scanner triggered:**\n"
        )

        for reason in result["reasons"]:

            message += (
                f"• {reason}\n"
            )

    message += "\n"

    # --------------------------------------------------------
    # OPTION CANDIDATES
    # --------------------------------------------------------

    if option_candidates:

        message += (
            "🎯 **CALL OPTION CANDIDATES**\n"
        )

        for risk, option in option_candidates:

            message += (
                f"\n**{risk} candidate**\n"

                f"Strike: "
                f"${option['strike']:.2f} Call\n"

                f"Expiration: "
                f"{option['expiration']}\n"

                f"Bid / Ask: "
                f"${option['bid']:.2f} / "
                f"${option['ask']:.2f}\n"

                f"Midpoint: "
                f"${option['mid']:.2f}\n"

                f"Breakeven: "
                f"${option['breakeven']:.2f}\n"

                f"IV: "
                f"{option['iv']:.1f}%\n"

                f"Volume: "
                f"{option['volume']:.0f}\n"

                f"Open Interest: "
                f"{option['open_interest']:.0f}\n"

                f"Bid/Ask Spread: "
                f"{option['spread_pct']:.1f}%\n"
            )

    else:

        message += (
            "⛔ **OPTIONS: PASS**\n"
            "No call contract met the "
            "liquidity and spread requirements.\n"
        )

    message += (
        "\n⚠️ **Scanner signal only — "
        "verify the catalyst, current quote, "
        "option pricing and risk before trading.**"
    )

    return message


# ============================================================
# SQUEEZE SCAN COMPLETE
# ============================================================

def send_scan_complete_message(
    discovered,
    analyzed,
    qualifying,
    alerts_sent,
):

    now = datetime.now(
        MARKET_TIMEZONE
    )

    scan_time = now.strftime(
        "%Y-%m-%d %I:%M:%S %p ET"
    )

    message = (
        "✅ **SQUEEZE SCAN COMPLETE**\n\n"

        f"🔎 Candidates discovered: "
        f"{discovered}\n"

        f"📊 Passed preliminary filters: "
        f"{analyzed}\n"

        f"🔥 Qualifying squeeze setups: "
        f"{qualifying}\n"

        f"🚨 Alerts sent: "
        f"{alerts_sent}\n\n"

        f"🕐 Completed: "
        f"{scan_time}"
    )

    send_discord_message(message)


# ============================================================
# MAIN SCANNER
# ============================================================

def run_scanner():

    print(
        "Starting Dynamic "
        "Squeeze Alert Scanner..."
    )

    # --------------------------------------------------------
    # MARKET HOURS GUARD
    # --------------------------------------------------------

    if not market_is_open():

        now = datetime.now(
            MARKET_TIMEZONE
        )

        print(
            "Market is closed. "
            "Skipping squeeze scan."
        )

        print(
            "Current Eastern time:",
            now.strftime(
                "%Y-%m-%d %I:%M:%S %p ET"
            )
        )

        return

    # --------------------------------------------------------
    # DISCOVERY
    # --------------------------------------------------------

    symbols = get_dynamic_candidates()

    discovered_count = len(symbols)

    analyzed_count = 0

    qualifying_results = []

    # --------------------------------------------------------
    # STOCK ANALYSIS
    # --------------------------------------------------------

    for symbol in symbols:

        result = analyze_stock(
            symbol
        )

        if not result:
            continue

        analyzed_count += 1

        print(
            f"{symbol}: "
            f"Score={result['score']} | "
            f"RVOL="
            f"{result['relative_volume']:.2f}x | "
            f"Short="
            f"{result['short_percent']:.1f}% | "
            f"Move="
            f"{result['daily_gain']:+.1f}%"
        )

        if (
            result["score"]
            >= MIN_SCORE
        ):

            qualifying_results.append(
                result
            )

    # --------------------------------------------------------
    # RANK BEST SQUEEZE SETUPS
    # --------------------------------------------------------

    qualifying_results.sort(
        key=lambda result:
            result["score"],
        reverse=True,
    )

    qualifying_count = len(
        qualifying_results
    )

    alerts_to_send = (
        qualifying_results[
            :MAX_ALERTS_PER_RUN
        ]
    )

    alerts_sent = 0

    # --------------------------------------------------------
    # SEND DISCORD ALERTS
    # --------------------------------------------------------

    for result in alerts_to_send:

        alert = create_alert(
            result
        )

        if send_discord_message(
            alert
        ):
            alerts_sent += 1

    # --------------------------------------------------------
    # CONSOLE RESULT
    # --------------------------------------------------------

    if qualifying_count == 0:

        print(
            "No stocks currently meet "
            "the squeeze-alert threshold."
        )

    else:

        print(
            f"{qualifying_count} stocks "
            f"met the squeeze threshold."
        )

    # --------------------------------------------------------
    # ALWAYS SEND SCAN COMPLETION MESSAGE
    # --------------------------------------------------------

    send_scan_complete_message(
        discovered=discovered_count,
        analyzed=analyzed_count,
        qualifying=qualifying_count,
        alerts_sent=alerts_sent,
    )

    print(
        "✅ SQUEEZE SCAN COMPLETE"
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    run_scanner()
