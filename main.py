import time
import os
import requests
from datetime import date

# ENV VARS
BOT_TOKEN = os.getenv("BOT_TOKEN")       # telegram bot token
CHAT_ID = os.getenv("CHAT_ID")           # chat id (user of groep)
API_KEY = os.getenv("API_KEY")           # api-football key

if not BOT_TOKEN or not CHAT_ID or not API_KEY:
    raise ValueError("❌ Missing env vars: BOT_TOKEN, CHAT_ID, API_KEY")

HEADERS = {
    "x-apisports-key": API_KEY
}

BASE_URL = "https://v3.football.api-sports.io"

# Anti-spam / daily limits
ALERTED_MATCHES = set()
DAILY_ALERTS = 0
TODAY = date.today()

def send_message(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    params = {"chat_id": CHAT_ID, "text": text}
    try:
        requests.get(url, params=params, timeout=10)
    except Exception:
        # als telegram faalt, wil je niet dat je hele bot stopt
        pass

def api_get(path: str, params=None):
    r = requests.get(f"{BASE_URL}{path}", headers=HEADERS, params=params, timeout=25)
    r.raise_for_status()
    return r.json()

def get_live_matches():
    data = api_get("/fixtures", params={"live": "all"})
    return data.get("response", [])

def get_match_statistics(fixture_id: int):
    data = api_get("/fixtures/statistics", params={"fixture": fixture_id})
    return data.get("response", [])

def safe_int(v):
    if v is None:
        return 0
    if isinstance(v, str):
        v = v.replace("%", "").strip()
    try:
        return int(float(v))
    except Exception:
        return 0

def stat(team_stats_list, name: str) -> int:
    """
    team_stats_list = list met dicts:
    [
        {"type":"Shots on Goal","value": 3},
        {"type":"Ball Possession","value":"55%"},
        ...
    ]
    """
    for s in team_stats_list:
        if s.get("type") == name:
            return safe_int(s.get("value"))
    return 0

send_message("🟢 Bot gestart – NEXT GOAL alerts actief")
send_message("🟢 VALUE-MODUS actief — max 5 bets per dag")

while True:
    try:
        # Reset daily limit
        if date.today() != TODAY:
            TODAY = date.today()
            DAILY_ALERTS = 0
            ALERTED_MATCHES.clear()
            send_message("🔄 Nieuwe dag — daily alerts gereset")

        # Daily cap
        if DAILY_ALERTS >= 5:
            time.sleep(300)  # 5 min wachten
            continue

        matches = get_live_matches()

        for match in matches:
            fixture = match.get("fixture", {})
            fid = fixture.get("id")
            if not fid:
                continue

            if fid in ALERTED_MATCHES:
                continue

            minute = fixture.get("status", {}).get("elapsed")
            if not minute or minute < 25 or minute > 80:
                continue

            # Score filter
            goals = match.get("goals", {})
            gh = goals.get("home", 0)
            ga = goals.get("away", 0)

            if abs(gh - ga) > 1:
                continue

            # Stats ophalen via losse endpoint
            stats_response = get_match_statistics(fid)

            # verwacht: 2 teams (home/away)
            if not stats_response or len(stats_response) != 2:
                continue

            home_block = stats_response[0]
            away_block = stats_response[1]

            home_stats = home_block.get("statistics", [])
            away_stats = away_block.get("statistics", [])

            # Metrics
            hsot = stat(home_stats, "Shots on Goal")
            asot = stat(away_stats, "Shots on Goal")

            hcorn = stat(home_stats, "Corner Kicks")
            acorn = stat(away_stats, "Corner Kicks")

            hpos = stat(home_stats, "Ball Possession")
            apos = stat(away_stats, "Ball Possession")

            # PRESSURE LOGICA (jouw value-mode idee)
            # je kunt deze thresholds later easy aanpassen
            pressure_home = sum([
                hsot >= 3,
                hcorn >= 3,
                hsot >= 2,
                hcorn >= 2,
                hpos >= 60
            ])

            pressure_away = sum([
                asot >= 3,
                acorn >= 3,
                asot >= 2,
                acorn >= 2,
                apos >= 60
            ])

            # Alleen alert als er echt verschil is
            if pressure_home == pressure_away:
                continue

            home = match.get("teams", {}).get("home", {}).get("name", "HOME")
            away = match.get("teams", {}).get("away", {}).get("name", "AWAY")

            predicted = home if pressure_home > pressure_away else away

            send_message(
                f"⚠️ NEXT GOAL ALERT\n\n"
                f"🎯 VALUE BET – NEXT GOAL\n\n"
                f"{home} vs {away}\n"
                f"Minuut: {minute}'\n"
                f"Stand: {gh}-{ga}\n\n"
                f"📊 Stats:\n"
                f"Possession: {hpos}% - {apos}%\n"
                f"Shots on Target: {hsot} - {asot}\n"
                f"Corners: {hcorn} - {acorn}\n\n"
                f"🔥 Pressure score: {pressure_home} - {pressure_away}\n"
                f"➡️ Verwachte volgende goal: {predicted}"
            )

            ALERTED_MATCHES.add(fid)
            DAILY_ALERTS += 1
            break  # 1 alert per cycle

        time.sleep(90)

    except Exception as e:
        # Niet spammen op Telegram bij errors, maar wel backoff
        send_message(f"❌ ERROR: {e}")
        time.sleep(60)
