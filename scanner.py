import os
import requests
from datetime import datetime

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")


def send_discord_alert(message):
    if not DISCORD_WEBHOOK_URL:
        print("DISCORD_WEBHOOK_URL is not configured.")
        return

    response = requests.post(
        DISCORD_WEBHOOK_URL,
        json={"content": message},
        timeout=10
    )

    response.raise_for_status()
    print("Discord alert sent successfully.")


def test_alert():
    message = (
        "🚨 **Squeeze Alert Scanner Test**\n"
        f"Scanner is connected and running.\n"
        f"Test time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    send_discord_alert(message)


if __name__ == "__main__":
    test_alert()
