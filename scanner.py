import os
import sys
import math
import requests
import yfinance as yf
from datetime import datetime, timezone


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

# Alert threshold
MIN_SCORE = 70

# Options filters
MAX_OPTION_SPREAD = 0.15
MIN_OPTION_OPEN_INTEREST = 100

# Scanner limits
MAX_CANDIDATES_TO_SCAN = 75
MAX_ALERTS_PER_RUN = 3


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
    Send a message to the Discord webhook.
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
# DYNAMIC STOCK DISCOVERY
# ============================================================

def get_dynamic_candidates():
    """
    Dynamically discover stocks from Yahoo Finance screeners.

    No fixed watchlist is required.
    """

    symbols = set()

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

                exchange = quote.get(
                    "exchange",
                    ""
                )

                if not symbol:
                    continue

                # Only scan normal equities
                if quote_type and quote_type != "EQUITY":
                    continue

                # Avoid symbols such as warrants/options/etc.
                if (
                    "^" in symbol
                    or "=" in symbol
                ):
                    continue

                symbols.add(symbol)

        except Exception as error:

            print(
                f"Dynamic screener error: {error}"
            )

    candidates = sorted(symbols)

    print(
        f"Dynamic discovery found "
        f"{len(candidates)} unique symbols."
    )

    return candidates[:MAX_CANDIDATES_TO_SCAN]


# ============================================================
# VOLUME METRICS
# ============================================================

def calculate_relative_volume(history):
    """
    Compare current daily volume against the prior
    20-session average.
    """

    if len(history) < 21:
        return 0

    current_volume = safe_float(
        history["Volume"].iloc[-1]
    )

    previous_volume = (
        history["Volume"]
        .iloc[-21:-1]
    )

    average_volume = safe_float(
        previous_volume.mean()
    )

    if average_volume <= 0:
        return 0

    return current_volume / average_volume


def calculate_average_dollar_volume(history):
    """
    Approximate 20-day average dollar volume.
    """

    if len(history) < 20:
        return 0

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
    Calculate squeeze score from 0-100.
    """

    score = 0
    reasons = []

    # --------------------------------------------------------
    # SHORT FLOAT
    # Maximum: 30 points
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
    # Maximum: 20 points
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
    # Maximum: 25 points
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
    # Maximum: 15 points
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
    # Maximum: 10 points
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
    Find two possible call contracts:

    1. Lower-risk:
       ATM or slightly ITM.

    2. Higher-risk:
       Moderately OTM.

    Contracts with poor liquidity are rejected.
    """

    try:

        expirations = stock.options

        if not expirations:
            return []

        candidates = []

        # Check several near-term expirations
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

                    spread_pct = (
                        ask - bid
                    ) / midpoint

                    # Reject bad bid/ask spreads
                    if (
                        spread_pct
                        > MAX_OPTION_SPREAD
                    ):
                        continue

                    # Reject illiquid contracts
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
                                spread_pct * 100,

                            "breakeven":
                                breakeven,
                        }
                    )

            except Exception as error:

                print(
                    f"Option expiration error "
                    f"{expiration}: {error}"
                )

        if not candidates:
            return []

        # Prefer:
        # - closer strikes
        # - higher OI
        # - tighter spreads

        candidates.sort(
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

        conservative = None
        aggressive = None

        # ----------------------------------------------------
        # LOWER-RISK OPTION
        # Slightly ITM through slightly OTM
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
        # HIGHER-RISK OPTION
        # 5-15% OTM
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
    """
    Analyze one dynamically discovered stock.
    """

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

        if (
            price <= 0
            or previous_close <= 0
        ):
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

        # Ignore stocks without enough momentum
        if daily_gain < MIN_DAILY_GAIN:
            return None

        # Avoid chasing stocks already too extended
        if daily_gain > MAX_DAILY_GAIN:
            return None

        # ----------------------------------------------------
        # VOLUME
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
        # SHORT INTEREST
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

        # Yahoo may provide shortPercentOfFloat directly.
        yahoo_short_pct = safe_float(
            info.get("shortPercentOfFloat")
        )

        if yahoo_short_pct > 0:

            # Yahoo normally represents this as a decimal.
            if yahoo_short_pct <= 1:

                short_percent = (
                    yahoo_short_pct * 100
                )

            else:

                short_percent = (
                    yahoo_short_pct
                )

        elif (
            shares_short > 0
            and float_shares > 0
        ):

            short_percent = (
                shares_short
                / float_shares
            ) * 100

        if (
            short_percent
            < MIN_SHORT_FLOAT
        ):
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
            "symbol":
                symbol,

            "price":
                price,

            "daily_gain":
                daily_gain,

            "relative_volume":
                relative_volume,

            "short_percent":
                short_percent,

            "days_to_cover":
                days_to_cover,

            "avg_dollar_volume":
                avg_dollar_volume,

            "score":
                score,

            "reasons":
                reasons,

            "stock":
                stock,
        }

    except Exception as error:

        print(
            f"Error scanning "
            f"{symbol}: {error}"
        )

        return None


# ============================================================
# DISCORD ALERT
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

    option_candidates = (
        find_call_candidates(
            result["stock"],
            price,
        )
    )

    # --------------------------------------------------------
    # RISK LABEL
    # --------------------------------------------------------

    if score >= 90:
        signal = "🔥 VERY STRONG"

    elif score >= 80:
        signal = "🟢 STRONG"

    elif score >= 70:
        signal = "🟡 WATCH"

    else:
        signal = "⚪ WEAK"

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
    # REASONS
    # --------------------------------------------------------

    if result["reasons"]:

        message += (
            "**Why the scanner triggered:**\n"
        )

        for reason in result["reasons"]:

            message += (
                f"• {reason}\n"
            )

    # --------------------------------------------------------
    # OPTIONS
    # --------------------------------------------------------

    message += "\n"

    if option_candidates:

        message += (
            "🎯 **CALL OPTION CANDIDATES**\n"
        )

        for (
            risk,
            option,
        ) in option_candidates:

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
        "option pricing and risk before entering a trade.**"
    )

    return message


# ============================================================
# SCAN COMPLETE MESSAGE
# ============================================================

def send_scan_complete_message(
    discovered,
    analyzed,
    qualifying,
    alerts_sent,
):
    """
    Send a completion report after every scanner run.
    """

    scan_time = datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )

    message = (
        "✅ **SQUEEZE SCAN COMPLETE**\n\n"

        f"🔎 Candidates discovered: "
        f"{discovered}\n"

        f"📊 Candidates fully analyzed: "
        f"{analyzed}\n"

        f"🔥 Qualifying squeeze setups: "
        f"{qualifying}\n"

        f"🚨 Alerts sent: "
        f"{alerts_sent}\n\n"

        f"🕐 Scan completed: "
        f"{scan_time}"
    )

    send_discord_message(message)


# ============================================================
# MAIN SCANNER
# ============================================================

def run_scanner():

    print(
        "Starting dynamic "
        "Squeeze Alert Scanner..."
    )

    symbols = (
        get_dynamic_candidates()
    )

    discovered_count = len(symbols)

    analyzed_count = 0

    alerts = []

    # --------------------------------------------------------
    # ANALYZE DYNAMIC CANDIDATES
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
            f"score={result['score']} "
            f"RVOL="
            f"{result['relative_volume']:.2f}x "
            f"short="
            f"{result['short_percent']:.1f}% "
            f"gain="
            f"{result['daily_gain']:+.1f}%"
        )

        if (
            result["score"]
            >= MIN_SCORE
        ):

            alerts.append(result)

    # --------------------------------------------------------
    # RANK BEST SETUPS
    # --------------------------------------------------------

    alerts.sort(
        key=lambda result:
            result["score"],
        reverse=True,
    )

    qualifying_count = len(alerts)

    # Only send the strongest few
    alerts_to_send = (
        alerts[
            :MAX_ALERTS_PER_RUN
        ]
    )

    alerts_sent = 0

    # --------------------------------------------------------
    # SEND ALERTS
    # --------------------------------------------------------

    for result in alerts_to_send:

        alert_message = (
            create_alert(result)
        )

        success = (
            send_discord_message(
                alert_message
            )
        )

        if success:
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
    # ALWAYS SEND COMPLETION MESSAGE
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
