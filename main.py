import time
import os
import requests
from datetime import datetime

# ===== ENV =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_FOOTBALL_KEY")

HEADERS = {
    "x-apisports-key": API_KEY,
    "Accept": "application/json"
}

LIVE_STATUSES = {"LIVE", "1H", "2H", "HT", "ET"}

# ===== TELEGRAM =====
def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.get(url, params={
        "chat_id": CHAT_ID,
        "text": text
    })

# ===== API =====
def get_today_fixtures():
    url = "https://v3.football.api-sports.io/fixtures"
    params = {
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
        "timezone": "Europe/Amsterdam"
    }
    r = requests.get(url, headers=HEADERS, params=params, timeout=10)
    data = r.json()
    return data.get("response", [])

# ===== START =====
send_message("🟢 Bot gestart – LIVE detectie test")

while True:
    try:
        fixtures = get_today_fixtures()

        live_matches = [
            f for f in fixtures
            if f["fixture"]["status"]["short"] in LIVE_STATUSES
        ]

        send_message(f"📡 LIVE MATCHES GEVONDEN: {len(live_matches)}")

        for match in live_matches[:3]:
            home = match["teams"]["home"]["name"]
            away = match["teams"]["away"]["name"]
            minute = match["fixture"]["status"]["elapsed"]
            status = match["fixture"]["status"]["short"]
            gh = match["goals"]["home"]
            ga = match["goals"]["away"]

            send_message(
                f"⚽ LIVE WEDSTRIJD\n"
                f"{home} vs {away}\n"
                f"Status: {status}\n"
                f"Minuut: {minute}'\n"
                f"Stand: {gh}-{ga}"
            )

        time.sleep(60)

    except Exception as e:
        send_message(f"❌ ERROR: {e}")
        time.sleep(60)
