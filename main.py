import requests
import os
import time

send_message(f"DEBUG API KEY IS: {API_KEY}")


API_KEY = os.getenv("API_FOOTBALL_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

HEADERS = {
    "x-apisports-key": API_KEY
}

def send(msg):
    requests.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        params={"chat_id": CHAT_ID, "text": msg}
    )

send("🟢 TEST START – LIVE endpoint")

while True:
    r = requests.get(
        "https://v3.football.api-sports.io/fixtures?live=all",
        headers=HEADERS
    )

    data = r.json()

    send(f"📊 API results: {data.get('results')}")

    if data.get("results", 0) > 0:
        match = data["response"][0]
        send(
            f"⚽ LIVE GEVONDEN:\n"
            f"{match['teams']['home']['name']} vs {match['teams']['away']['name']}\n"
            f"Minuut: {match['fixture']['status']['elapsed']}"
        )

    time.sleep(120)
