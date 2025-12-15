import requests
import os
from datetime import datetime

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

send("🧪 TEST START – vandaag wedstrijden ophalen")

today = datetime.utcnow().strftime("%Y-%m-%d")

r = requests.get(
    "https://v3.football.api-sports.io/fixtures",
    headers=HEADERS,
    params={"date": today}
)

data = r.json()

send(f"📊 RESULTAAT API: {data['results']} wedstrijden gevonden")

if data["results"] > 0:
    match = data["response"][0]
    send(
        f"⚽ VOORBEELD:\n"
        f"{match['teams']['home']['name']} vs {match['teams']['away']['name']}"
    )

