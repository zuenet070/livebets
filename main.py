import time
import os
import requests

# ===== ENV VARS =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_FOOTBALL_KEY")

HEADERS = {
    "x-apisports-key": API_KEY,
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0"
}


ALERTED_FIXTURES = set()

# ===== TELEGRAM =====
def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.get(url, params={
        "chat_id": CHAT_ID,
        "text": text
    })

# ===== API FOOTBALL =====
def get_live_matches():
    url = "https://v3.football.api-sports.io/fixtures"
    params = {
        "next": 5
    }

    r = requests.get(url, headers=HEADERS, params=params, timeout=10)
    data = r.json()

    send_message(f"🧪 UPCOMING MATCHES: {data.get('results')}")
    return data.get("response", [])




def get_stat(stats, name):
    for s in stats:
        if s["type"] == name:
            value = s["value"]
            if value is None:
                return 0
            if isinstance(value, str):
                return int(value.replace("%", ""))
            return int(value)
    return 0

def pressure_score(minute, is_draw, shots_on, corners, possession):
    if minute < 20 or minute > 80:
        return 0

    score = 0

    if is_draw:
        score += 2

    if shots_on >= 2:
        score += 2

    if corners >= 2:
        score += 1

    if possession >= 65:
        score += 1

    return score

# ===== START =====
send_message("🟢 Bot gestart en draait")

while True:
    try:
        matches = get_live_matches()

        send_message(f"🧪 LIVE MATCHES: {len(matches)}")

        for match in matches:
            fixture_id = match["fixture"]["id"]
            if fixture_id in ALERTED_FIXTURES:
                continue

            minute = match["fixture"]["status"]["elapsed"]
            if minute is None:
                continue

            home = match["teams"]["home"]["name"]
            away = match["teams"]["away"]["name"]

            goals_home = match["goals"]["home"]
            goals_away = match["goals"]["away"]
            is_draw = goals_home == goals_away

            stats = match.get("statistics")
            if not stats or len(stats) < 2:
                continue

            home_stats = stats[0]["statistics"]

            shots_on = get_stat(home_stats, "Shots on Goal")
            corners = get_stat(home_stats, "Corner Kicks")
            possession = get_stat(home_stats, "Ball Possession")

            score = pressure_score(
                minute,
                is_draw,
                shots_on,
                corners,
                possession
            )

            if score >= 1:
                send_message(
                    f"⚠️ NEXT GOAL ALERT\n\n"
                    f"{home} vs {away}\n"
                    f"Minuut: {minute}\n"
                    f"Score: {goals_home}-{goals_away}\n\n"
                    f"Shots on target: {shots_on}\n"
                    f"Corners: {corners}\n"
                    f"Possession: {possession}%\n"
                    f"Pressure score: {score}"
                )

                ALERTED_FIXTURES.add(fixture_id)
                break  # max 1 alert per loop

        time.sleep(60)

    except Exception as e:
        send_message(f"❌ ERROR: {e}")
        time.sleep(60)

