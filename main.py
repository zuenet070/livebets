import time
import os
import requests
from datetime import datetime

# ===== ENV VARS =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_FOOTBALL_KEY")

HEADERS = {
    "x-apisports-key": API_KEY,
    "Accept": "application/json"
}

# ===== TELEGRAM =====
def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.get(url, params={
        "chat_id": CHAT_ID,
        "text": text
    })

# ===== API FOOTBALL =====
def get_live_matches_manual():
    url = "https://v3.football.api-sports.io/fixtures"
    params = {
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
        "timezone": "Europe/Amsterdam"
    }

    r = requests.get(url, headers=HEADERS, params=params, timeout=10)
    data = r.json()

    live_matches = []

    for match in data.get("response", []):
        status = match["fixture"]["status"]["short"]
        if status in ["1H", "2H"]:
            live_matches.append(match)

    return live_matches

# ===== START =====
send_message("🟢 Bot gestart – live check actief")

while True:
    try:
        matches = get_live_matches_manual()
        send_message(f"🧪 LIVE WEDSTRIJDEN GEVONDEN: {len(matches)}")

        for match in matches:
            home = match["teams"]["home"]["name"]
            away = match["teams"]["away"]["name"]
            minute = match["fixture"]["status"]["elapsed"]
            score_home = match["goals"]["home"]
            score_away = match["goals"]["away"]

            send_message(
                f"⚽ LIVE WEDSTRIJD\n"
                f"{home} vs {away}\n"
                f"Minuut: {minute}'\n"
                f"Stand: {score_home}-{score_away}"
            )

        time.sleep(60)

    except Exception as e:
        send_message(f"❌ ERROR: {e}")
        time.sleep(60)
