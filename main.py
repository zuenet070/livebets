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
ALERTED_FIXTURES = set()

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

def get_stat(stats, name):
    for s in stats:
        if s["type"] == name:
            val = s["value"]
            if val is None:
                return 0
            if isinstance(val, str):
                return int(val.replace("%", ""))
            return int(val)
    return 0

def pressure_score(minute, is_draw, shots_on, corners, possession):
    if minute < 20 or minute > 80:
        return 0

    score = 0

    if is_draw:
        score += 2
    if shots_on >= 3:
        score += 3
    elif shots_on == 2:
        score += 2
    if corners >= 3:
        score += 2
    if possession >= 65:
        score += 1

    return score

send_message("🟢 Next-Goal bot actief")

while True:
    try:
        matches = get_live_matches()

        for match in matches:
            fixture_id = match["fixture"]["id"]
            if fixture_id in ALERTED_FIXTURES:
                continue

            status = match["fixture"]["status"]["short"]
            if status not in LIVE_STATUSES:
                continue

            minute = match["fixture"]["status"]["elapsed"]
            if minute is None:
                continue

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

            if score >= 6:
                home = match["teams"]["home"]["name"]
                away = match["teams"]["away"]["name"]

                send_message(
                    f"🚨 NEXT GOAL ALERT\n\n"
                    f"{home} vs {away}\n"
                    f"Minuut: {minute}'\n"
                    f"Stand: {goals_home}-{goals_away}\n\n"
                    f"Drukscore: {score}\n"
                    f"Schoten op doel: {shots_on}\n"
                    f"Corners: {corners}\n"
                    f"Balbezit: {possession}%"
                )

                ALERTED_FIXTURES.add(fixture_id)

        time.sleep(30)

    except Exception as e:
        send_message(f"❌ ERROR: {e}")
        time.sleep(30)
