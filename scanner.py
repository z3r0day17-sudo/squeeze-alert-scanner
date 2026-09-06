import os
import sys
import math
import requests
import yfinance as yf

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# ============================================================
# SETTINGS
# ============================================================

MIN_PRICE = 3.00
MAX_PRICE = 60.00

MIN_RELATIVE_VOLUME = 1.50
MIN_DAILY_GAIN = 2.0
MAX_DAILY_GAIN = 25.0

MIN_SHORT_FLOAT = 10.0
MIN_AVG_DOLLAR_VOLUME = 10_000_000

MIN_SCORE = 70
MAX_OPTION_SPREAD = 0.15

MAX_CANDIDATES_TO_SCAN = 50
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
        print("Discord alert sent.")

    except requests.RequestException as error:
        print(f"Discord error: {error}")


# ============================================================
# DYNAMIC CANDIDATE DISCOVERY
# ============================================================

def get_dynamic_candidates():
    """
    Pull candidates dynamically from Yahoo Finance screeners.
    No fixed watchlist.
    """

    symbols = set()

    screener_urls = [
        "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?scrIds=most_actives&count=100",
        "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?scrIds=day_gainers&count=100",
        "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?scrIds=small_cap_gainers&count=100",
    ]

    headers = {
        "User-Agent": "Mozilla/5.0"
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

            quotes = results[0].get("quotes", [])

            for quote in quotes:
                symbol = quote.get("symbol")

                if symbol:
                    symbols.add(symbol)

        except Exception as error:
            print(f"Screener error: {error}")

    candidates = list(symbols)

    print(
        f"Dynamic discovery found "
        f"{len(candidates)} unique symbols."
    )

    return candidates[:MAX_CANDIDATES_TO_SCAN]


# ============================================================
# VOLUME CALCULATIONS
# ============================================================

def calculate_relative_volume(history):
    if len(history) < 21:
        return 0

    current_volume = safe_float(
        history["Volume"].iloc[-1]
    )

    previous_volume = history["Volume"].iloc[-21:-1]

    average_volume = safe_float(
        previous_volume.mean()
    )

    if average_volume <= 0:
        return 0

    return current_volume / average_volume


def calculate_average_dollar_volume(history):
    if len(history) < 20:
        return 0

    recent = history.tail(20)

    dollar_volume = (
        recent["Close"] * recent["Volume"]
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
    score = 0
    reasons = []

    # Short float
    if short_percent >= 25:
        score += 30
        reasons.append(
            f"Very high short float: {short_percent:.1f}%"
        )

    elif short_percent >= 20:
        score += 25
        reasons.append(
            f"High short float: {short_percent:.1f}%"
        )

    elif short_percent >= 15:
        score += 20
        reasons.append(
            f"Elevated short float: {short_percent:.1f}%"
        )

    elif short_percent >= 10:
        score += 10
        reasons.append(
            f"Short float: {short_percent:.1f}%"
        )

    # Days to cover
    if days_to_cover >= 5:
        score += 20
        reasons.append(
            f"Days to cover: {days_to_cover:.1f}"
        )

    elif days_to_cover >= 3:
        score += 15
        reasons.append(
            f"Days to cover: {days_to_cover:.1f}"
        )

    elif days_to_cover >= 2:
        score += 8

    # Relative volume
    if relative_volume >= 3:
        score += 25
        reasons.append(
            f"Relative volume: {relative_volume:.1f}x"
        )

    elif relative_volume >= 2:
        score += 20
        reasons.append(
            f"Relative volume: {relative_volume:.1f}x"
        )

    elif relative_volume >= 1.5:
        score += 15
        reasons.append(
            f"Relative volume: {relative_volume:.1f}x"
        )

    # Price momentum
    if 5 <= daily_gain <= 15:
        score += 15
        reasons.append(
            f"Price momentum: +{daily_gain:.1f}%"
        )

    elif 2 <= daily_gain < 5:
        score += 10
        reasons.append(
            f"Price gaining: +{daily_gain:.1f}%"
        )

    elif 15 < daily_gain <= 25:
        score += 8
        reasons.append(
            f"Strong but extended: +{daily_gain:.1f}%"
        )

    # Liquidity
    if avg_dollar_volume >= 50_000_000:
        score += 10

    elif avg_dollar_volume >= 20_000_000:
        score += 7

    elif avg_dollar_volume >= MIN_AVG_DOLLAR_VOLUME:
        score += 5

    return min(score, 100), reasons


# ============================================================
# OPTIONS
# ============================================================

def find_call_candidates(stock, price):
    try:
        expirations = stock.options

        if not expirations:
            return []

        candidates = []

        for expiration in expirations[:4]:

            chain = stock.option_chain(expiration)

            calls = chain.calls.copy()

            if calls.empty:
                continue

            calls = calls[
                (calls["strike"] >= price * 0.95)
                & (calls["strike"] <= price * 1.20)
            ]

            for _, option in calls.iterrows():

                bid = safe_float(option.get("bid"))
                ask = safe_float(option.get("ask"))
                volume = safe_float(option.get("volume"))

                open_interest = safe_float(
                    option.get("openInterest")
                )

                iv = safe_float(
                    option.get("impliedVolatility")
                )

                strike = safe_float(
                    option.get("strike")
                )

                if bid <= 0 or ask <= 0:
                    continue

                midpoint = (bid + ask) / 2

                if midpoint <= 0:
                    continue

                spread = (
                    (ask - bid) / midpoint
                )

                if spread > MAX_OPTION_SPREAD:
                    continue

                if open_interest < 100:
                    continue

                candidates.append(
                    {
                        "expiration": expiration,
                        "strike": strike,
                        "bid": bid,
                        "ask": ask,
                        "volume": volume,
                        "open_interest": open_interest,
                        "iv": iv * 100,
                        "spread": spread,
                    }
                )

        if not candidates:
            return []

        candidates.sort(
            key=lambda x: (
                abs(x["strike"] - price),
                -x["open_interest"],
                x["spread"],
            )
        )

        conservative = None
        aggressive = None

        for option in candidates:
            if (
                option["strike"] >= price * 0.97
                and option["strike"] <= price * 1.03
            ):
                conservative = option
                break

        for option in candidates:
            if (
                option["strike"] >= price * 1.05
                and option["strike"] <= price * 1.15
            ):
                aggressive = option
                break

        results = []

        if conservative:
            results.append(
                ("Lower-risk", conservative)
            )

        if aggressive:
            results.append(
                ("Higher-risk", aggressive)
            )

        return results

    except Exception as error:
        print(f"Option lookup error: {error}")
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
        )

        if len(history) < 21:
            return None

        price = safe_float(
            history["Close"].iloc[-1]
        )

        previous_close = safe_float(
            history["Close"].iloc[-2]
        )

        if price <= 0 or previous_close <= 0:
            return None

        if price < MIN_PRICE or price > MAX_PRICE:
            return None

        daily_gain = (
            (price - previous_close)
            / previous_close
        ) * 100

        if daily_gain < MIN_DAILY_GAIN:
            return None

        if daily_gain > MAX_DAILY_GAIN:
            return None

        relative_volume = calculate_relative_volume(
            history
        )

        if relative_volume < MIN_RELATIVE_VOLUME:
            return None

        avg_dollar_volume = (
            calculate_average_dollar_volume(
                history
            )
        )

        if avg_dollar_volume < MIN_AVG_DOLLAR_VOLUME:
            return None

        info = stock.info

        shares_short = safe_float(
            info.get("sharesShort")
        )

        float_shares = safe_float(
            info.get("floatShares")
        )

        average_volume = safe_float(
            info.get("averageVolume")
        )

        short_percent = 0

        if shares_short > 0 and float_shares > 0:
            short_percent = (
                shares_short
                / float_shares
            ) * 100

        if short_percent < MIN_SHORT_FLOAT:
            return None

        days_to_cover = 0

        if shares_short > 0 and average_volume > 0:
            days_to_cover = (
                shares_short / average_volume
            )

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
            f"Error scanning {symbol}: {error}"
        )

        return None


# ============================================================
# ALERT MESSAGE
# ============================================================

def create_alert(result):

    symbol = result["symbol"]
    score = result["score"]
    price = result["price"]

    short_percent = result["short_percent"]
    days_to_cover = result["days_to_cover"]
    relative_volume = result["relative_volume"]
    daily_gain = result["daily_gain"]

    option_candidates = find_call_candidates(
        result["stock"],
        price,
    )

    message = (
        f"🚨 **SQUEEZE WATCH — {symbol}**\n\n"
        f"🔥 **Squeeze Score:** {score}/100\n"
        f"💵 **Price:** ${price:.2f}\n"
        f"📈 **Today:** {daily_gain:+.1f}%\n"
        f"🩳 **Short Float:** {short_percent:.1f}%\n"
        f"⏳ **Days to Cover:** {days_to_cover:.1f}\n"
        f"📊 **Relative Volume:** {relative_volume:.1f}x\n\n"
    )

    if result["reasons"]:

        message += "**Why it triggered:**\n"

        for reason in result["reasons"]:
            message += f"• {reason}\n"

    message += "\n"

    if option_candidates:

        message += "**Potential call contracts:**\n"

        for risk, option in option_candidates:

            message += (
                f"\n**{risk}**\n"
                f"${option['strike']:.2f} Call "
                f"— {option['expiration']}\n"
                f"Bid/Ask: "
                f"${option['bid']:.2f} / "
                f"${option['ask']:.2f}\n"
                f"IV: {option['iv']:.0f}%\n"
                f"Open Interest: "
                f"{option['open_interest']:.0f}\n"
            )

    else:

        message += (
            "⚠️ **Options:** PASS — "
            "no sufficiently liquid call contract found.\n"
        )

    message += (
        "\n⚠️ **Squeeze-watch signal only. "
        "Verify current news, options pricing, "
        "and risk before trading.**"
    )

    return message


# ============================================================
# MAIN
# ============================================================

def run_scanner():

    print("Starting dynamic Squeeze Alert Scanner...")

    symbols = get_dynamic_candidates()

    alerts = []

    for symbol in symbols:

        result = analyze_stock(symbol)

        if not result:
            continue

        print(
            f"{symbol}: "
            f"score={result['score']} "
            f"RVOL={result['relative_volume']:.2f}x "
            f"short={result['short_percent']:.1f}% "
            f"gain={result['daily_gain']:.1f}%"
        )

        if result["score"] >= MIN_SCORE:
            alerts.append(result)

    alerts.sort(
        key=lambda x: x["score"],
        reverse=True,
    )

    alerts = alerts[:MAX_ALERTS_PER_RUN]

    for result in alerts:

        send_discord_message(
            create_alert(result)
        )

    if not alerts:
        print(
            "No stocks currently meet "
            "the squeeze-alert threshold."
        )

    print("Scan complete.")


if __name__ == "__main__":
    run_scanner()
