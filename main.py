import time
import os
import requests

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

def get_live_matches():
    url = "https://v3.football.api-sports.io/fixtures"
    params = {"live": "all"}
    r = requests.get(url, headers=HEADERS, params=params, timeout=10)
    data = r.json()

    # debug info
    send_message(f"🧪 API results: {data.get('results', 'no results key')}")
    return data.get("response", [])

send_message("🟢 Bot gestart – live check actief")

while True:
    try:
        matches = get_live_matches()
        send_message(f"⚽ LIVE WEDSTRIJDEN GEVONDEN: {len(matches)}")

        for m in matches[:3]:
            home = m["teams"]["home"]["name"]
            away = m["teams"]["away"]["name"]
            minute = m["fixture"]["status"]["elapsed"]
            gh = m["goals"]["home"]
            ga = m["goals"]["away"]

            send_message(
                f"🔴 LIVE\n{home} vs {away}\nMinuut: {minute}'\nStand: {gh}-{ga}"
            )

        time.sleep(60)

    except Exception as e:
        send_message(f"❌ ERROR: {e}")
        time.sleep(60)
