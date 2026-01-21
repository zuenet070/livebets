import time
import os
import requests
from datetime import date

# =========================
# JOUW ENV VARS (zoals in je screenshot)
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_FOOTBALL_KEY")

# Als er iets mist → duidelijke melding en stoppen
if not BOT_TOKEN or not CHAT_ID or not API_KEY:
    print("❌ ERROR: Missing env vars. Check BOT_TOKEN, CHAT_ID, API_FOOTBALL_KEY")
    exit()

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

ALERTED_MATCHES = set()
DAILY_ALERTS = 0
TODAY = date.today()
DAILY_MAX_ALERTS = 5

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    params = {"chat_id": CHAT_ID, "text": text}
    try:
        requests.get(url, params=params, timeout=10)
    except:
        pass

def api_get(path, params=None):
    r = requests.get(f"{BASE_URL}{path}", headers=HEADERS, params=params, timeout=25)
    r.raise_for_status()
    return r.json()

def get_live_matches():
    data = api_get("/fixtures", params={"live": "all"})
    return data.get("response", [])

def get_match_statistics(fixture_id):
    data = api_get("/fixtures/statistics", params={"fixture": fixture_id})
    return data.get("response", [])

def safe_int(v):
    if v is None:
        return 0
    if isinstance(v, str):
        v = v.replace("%", "").strip()
    try:
        return int(float(v))
    except:
        return 0

def stat(team_stats_list, name):
    for s in team_stats_list:
        if s.get("type") == name:
            return safe_int(s.get("value"))
    return 0

send_message("🟢 Bot gestart – NEXT GOAL alerts actief")
send_message("🟢 VALUE-MODUS actief — max 5 bets per dag")

while True:
    try:
        # reset elke dag
        global TODAY, DAILY_ALERTS
        if date.today() != TODAY:
            TODAY = date.today()
            DAILY_ALERTS = 0
            ALERTED_MATCHES.clear()
            send_message("🔄 Nieuwe dag — daily alerts gereset")

        # max 5 alerts per dag
        if DAILY_ALERTS >= DAILY_MAX_ALERTS:
            time.sleep(300)
            continue

        matches = get_live_matches()

        for match in matches:
            fixture = match.get("fixture", {})
            fid = fixture.get("id")
            if not fid:
                continue

            # 1 alert per match
            if fid in ALERTED_MATCHES:
                continue

            minute = fixture.get("status", {}).get("elapsed")
            if not minute or minute < 25 or minute > 80:
                continue

            goals = match.get("goals", {})
            gh = goals.get("home", 0)
            ga = goals.get("away", 0)

            # alleen spannende potjes
            if abs(gh - ga) > 1:
                continue

            # stats ophalen (dit zit NIET in live fixtures zelf)
            stats_response = get_match_statistics(fid)
            if not stats_response or len(stats_response) != 2:
                continue

            home_stats = stats_response[0].get("statistics", [])
            away_stats = stats_response[1].get("statistics", [])

            hsot = stat(home_stats, "Shots on Goal")
            asot = stat(away_stats, "Shots on Goal")

            hcorn = stat(home_stats, "Corner Kicks")
            acorn = stat(away_stats, "Corner Kicks")

            hpos = stat(home_stats, "Ball Possession")
            apos = stat(away_stats, "Ball Possession")

            # pressure score
            pressure_home = sum([hsot >= 3, hcorn >= 3, hsot >= 2, hcorn >= 2, hpos >= 60])
            pressure_away = sum([asot >= 3, acorn >= 3, asot >= 2, acorn >= 2, apos >= 60])

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
            break

        time.sleep(90)

    except Exception as e:
        send_message(f"❌ ERROR: {e}")
        time.sleep(60)
