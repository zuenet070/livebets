import time
import os
import requests
from datetime import date

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_FOOTBALL_KEY")

HEADERS = {
    "x-apisports-key": API_KEY
}

ALERTED_MATCHES = set()
DAILY_ALERTS = 0
TODAY = date.today()

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
        timeout=25
    )
    return r.json().get("response", [])

def stat(stats, name):
    for s in stats:
        if s["type"] == name:
            v = s["value"]
            if v is None:
                return 0
            if isinstance(v, str):
                return int(v.replace("%", ""))
            return float(v)
    return 0

send_message("🟢 VALUE-MODUS actief — max 5 bets per dag")

while True:
    try:
        if date.today() != TODAY:
            TODAY = date.today()
            DAILY_ALERTS = 0
            ALERTED_MATCHES.clear()

        if DAILY_ALERTS >= 5:
            time.sleep(300)
            continue

        matches = get_live_matches()

        for match in matches:
            fid = match["fixture"]["id"]
            if fid in ALERTED_MATCHES:
                continue

            minute = match["fixture"]["status"]["elapsed"]
            if not minute or minute < 25 or minute > 80:
                continue

            gh = match["goals"]["home"]
            ga = match["goals"]["away"]
            if abs(gh - ga) > 1:
                continue

            stats = match.get("statistics")
            if not stats or len(stats) < 2:
                continue

            home_stats = stats[0]["statistics"]
            away_stats = stats[1]["statistics"]

            hs = stat(home_stats, "Shots on Goal")
            as_ = stat(away_stats, "Shots on Goal")
            hc = stat(home_stats, "Corner Kicks")
            ac = stat(away_stats, "Corner Kicks")
            hp = stat(home_stats, "Ball Possession")
            ap = stat(away_stats, "Ball Possession")

            pressure_home = sum([
                hs >= 2,
                hc >= 2,
                hp >= 60
            ])
            pressure_away = sum([
                as_ >= 2,
                ac >= 2,
                ap >= 60
            ])

            if max(pressure_home, pressure_away) < 2:
                continue

            home = match["teams"]["home"]["name"]
            away = match["teams"]["away"]["name"]
            predicted = home if pressure_home > pressure_away else away

            send_message(
                f"🎯 VALUE BET – NEXT GOAL\n\n"
                f"{home} vs {away}\n"
                f"Minuut: {minute}'\n"
                f"Stand: {gh}-{ga}\n\n"
                f"Schoten op doel: {hs} vs {as_}\n"
                f"Corners: {hc} vs {ac}\n"
                f"Balbezit: {hp}% vs {ap}%\n\n"
                f"➡️ Verwachte volgende goal: {predicted}"
            )

            ALERTED_MATCHES.add(fid)
            DAILY_ALERTS += 1
            break

        time.sleep(90)

    except Exception:
        time.sleep(60)

