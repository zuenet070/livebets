import time
import os
import requests

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_FOOTBALL_KEY")

HEADERS = {
    "x-apisports-key": API_KEY
}

ALERTED = set()

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

def stat(team_stats, name):
    for s in team_stats:
        if s["type"] == name:
            v = s["value"]
            if v is None:
                return 0
            if isinstance(v, str):
                return int(v.replace("%", ""))
            return int(v)
    return 0

send_message("🟢 Bot gestart – next goal alerts actief")

while True:
    try:
        matches = get_live_matches()

        for match in matches:
            fid = match["fixture"]["id"]
            if fid in ALERTED:
                continue

            minute = match["fixture"]["status"]["elapsed"]
            if not minute or minute < 30 or minute > 75:
                continue

            gh = match["goals"]["home"]
            ga = match["goals"]["away"]
            diff = abs(gh - ga)

            if diff > 1:
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
                hs >= 3,
                hc >= 3,
                hp >= 60
            ])
            pressure_away = sum([
                as_ >= 3,
                ac >= 3,
                ap >= 60
            ])

            if max(pressure_home, pressure_away) < 2:
                continue

            home = match["teams"]["home"]["name"]
            away = match["teams"]["away"]["name"]

            predicted = home if pressure_home > pressure_away else away

            send_message(
                f"⚠️ NEXT GOAL ALERT\n\n"
                f"{home} vs {away}\n"
                f"Minuut: {minute}'\n"
                f"Stand: {gh}-{ga}\n\n"
                f"Schoten op doel: {hs} vs {as_}\n"
                f"Corners: {hc} vs {ac}\n"
                f"Balbezit: {hp}% vs {ap}%\n\n"
                f"➡️ Verwachte volgende goal: {predicted}"
            )

            ALERTED.add(fid)
            break

        time.sleep(60)

    except Exception as e:
        send_message(f"❌ ERROR: {e}")
        time.sleep(60)


    except Exception as e:
        send_message(f"❌ ERROR: {e}")
        time.sleep(60)
