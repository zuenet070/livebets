import time
import os
import requests
from datetime import datetime

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_FOOTBALL_KEY")

HEADERS = {
    "x-apisports-key": API_KEY,
    "Accept": "application/json"
}

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.get(url, params={"chat_id": CHAT_ID, "text": text})

def get_today_fixtures():
    url = "https://v3.football.api-sports.io/fixtures"
    params = {
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
        "timezone": "Europe/Amsterdam"
    }
    r = requests.get(url, headers=HEADERS, params=params, timeout=10)
    return r.json().get("response", [])

send_message("🟢 Bot gestart – LIVE scan actief")

while True:
    try:
        fixtures = get_today_fixtures()

        live_matches = [
            m for m in fixtures
            if m["fixture"]["status"]["short"] == "LIVE"
        ]

        send_message(f"📡 LIVE WEDSTRIJDEN: {len(live_matches)}")

        for match in live_matches[:3]:
            home = match["teams"]["home"]["name"]
            away = match["teams"]["away"]["name"]
            minute = match["fixture"]["status"]["elapsed"]
            gh = match["goals"]["home"]
            ga = match["goals"]["away"]

            send_message(
                f"⚽ LIVE\n{home} vs {away}\nMinuut: {minute}'\nStand: {gh}-{ga}"
            )

        time.sleep(60)

    except Exception as e:
        send_message(f"❌ ERROR: {e}")
        time.sleep(60)
