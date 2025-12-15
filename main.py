import os
import time
import requests

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_FOOTBALL_KEY")

HEADERS = {
    "x-apisports-key": API_KEY,
    "Accept": "application/json"
}

LIVE_STATUSES = {"LIVE", "1H", "2H", "HT", "ET"}

def send_message(text):
    requests.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        params={"chat_id": CHAT_ID, "text": text}
    )

def get_live_matches():
    url = "https://v3.football.api-sports.io/fixtures"
    params = {"live": "all"}
    r = requests.get(url, headers=HEADERS, params=params, timeout=10)
    return r.json().get("response", [])

send_message("🟢 Bot gestart – LIVE wedstrijden monitor actief")

while True:
    try:
        matches = get_live_matches()

        live_matches = [
            m for m in matches
            if m["fixture"]["status"]["short"] in LIVE_STATUSES
        ]

        if live_matches:
            send_message(f"⚽ LIVE WEDSTRIJDEN: {len(live_matches)}")

            for m in live_matches[:3]:
                home = m["teams"]["home"]["name"]
                away = m["teams"]["away"]["name"]
                minute = m["fixture"]["status"]["elapsed"]
                gh = m["goals"]["home"]
                ga = m["goals"]["away"]

                send_message(
                    f"🔴 LIVE\n{home} vs {away}\n"
                    f"Minuut: {minute}'\nStand: {gh}-{ga}"
                )

        time.sleep(60)

    except Exception as e:
        send_message(f"❌ ERROR: {e}")
        time.sleep(60)
