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
DEBUGGED_FIXTURES = set()
PREVIOUS_STATS = {}

def send_message(text):
    requests.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        params={"chat_id": CHAT_ID, "text": text}
    )

def get_live_matches():
    r = requests.get(
        "https://v3.football.api-sports.io/fixtures",
        headers=HEADERS,
        params={"live": "all"},
        timeout=10
    )
    return r.json().get("response", [])

def get_stat(stats, name):
    for s in stats:
        if s["type"] == name:
            v = s["value"]
            if v is None:
                return 0
            if isinstance(v, str):
                return int(v.replace("%", ""))
            return int(v)
    return 0

def pressure_score(shots, corners, possession):
    score = 0
    if shots >= 3:
        score += 3
    elif shots == 2:
        score += 2
    if corners >= 3:
        score += 2
    if possession >= 65:
        score += 1
    return score

send_message("🟡 DEBUGMODUS AAN – bot analyseert live wedstrijden")

while True:
    try:
        matches = get_live_matches()

        for match in matches:
            fixture_id = match["fixture"]["id"]
            if fixture_id in DEBUGGED_FIXTURES:
                continue

            status = match["fixture"]["status"]["short"]
            minute = match["fixture"]["status"]["elapsed"]
            if status not in LIVE_STATUSES or minute is None:
                continue
            if minute < 25 or minute > 80:
                continue

            gh = match["goals"]["home"]
            ga = match["goals"]["away"]
            if abs(gh - ga) >= 2:
                DEBUGGED_FIXTURES.add(fixture_id)
                send_message(f"🧪 DEBUG {match['teams']['home']['name']} vs {match['teams']['away']['name']}\n→ overgeslagen: grote voorsprong")
                continue

            stats = match.get("statistics")
            if not stats or len(stats) < 2:
                continue

            home_stats = stats[0]["statistics"]
            away_stats = stats[1]["statistics"]

            h_shots = get_stat(home_stats, "Shots on Goal")
            a_shots = get_stat(away_stats, "Shots on Goal")
            h_corners = get_stat(home_stats, "Corner Kicks")
            a_corners = get_stat(away_stats, "Corner Kicks")
            h_pos = get_stat(home_stats, "Ball Possession")
            a_pos = get_stat(away_stats, "Ball Possession")

            h_score = pressure_score(h_shots, h_corners, h_pos)
            a_score = pressure_score(a_shots, a_corners, a_pos)
            diff = abs(h_score - a_score)

            prev = PREVIOUS_STATS.get(fixture_id)
            PREVIOUS_STATS[fixture_id] = (h_shots, a_shots, h_corners, a_corners)

            momentum_ok = False
            if prev:
                delta_shots = abs(h_shots - prev[0]) + abs(a_shots - prev[1])
                delta_corners = abs(h_corners - prev[2]) + abs(a_corners - prev[3])
                momentum_ok = delta_shots >= 1 or delta_corners >= 1

            reason = []
            if diff < 3:
                reason.append("dominantie te klein")
            if not momentum_ok:
                reason.append("geen recente momentum")

            DEBUGGED_FIXTURES.add(fixture_id)
            send_message(
                f"🧪 DEBUG {match['teams']['home']['name']} vs {match['teams']['away']['name']}\n"
                f"Minuut {minute}' | Stand {gh}-{ga}\n"
                f"H/A score: {h_score}-{a_score}\n"
                f"→ {' & '.join(reason)}"
            )

        time.sleep(60)

    except Exception as e:
        send_message(f"❌ ERROR: {e}")
        time.sleep(60)
