import time
import os
import requests

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_FOOTBALL_KEY")

HEADERS = {
    "x-apisports-key": API_KEY
}

ALERTED_FIXTURES = set()


def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.get(url, params={"chat_id": CHAT_ID, "text": text})


matches = get_live_matches()

        send_message(f"🧪 LIVE MATCHES GEVONDEN: {len(matches)}")

        for match in matches:
            home = match["teams"]["home"]["name"]
            away = match["teams"]["away"]["name"]
            minute = match["fixture"]["status"]["elapsed"]

            send_message(f"⚽ LIVE: {home} vs {away} ({minute}')")
            break  # slechts 1 match sturen

        time.sleep(60)

    except Exception as e:
        send_message(f"❌ ERROR: {e}")
        time.sleep(60)


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


def pressure_score(minute, is_draw, favorite_behind,
                   shots_on, corners, possession,
                   red_card_recent):

    if minute < 20 or minute > 80:
        return 0
    if red_card_recent:
        return 0

    score = 0

    # Stand
    if is_draw:
        score += 3
    elif favorite_behind:
        score += 2

    # Schoten op doel (laatste 10 min benadering)
    if shots_on >= 3:
        score += 3
    elif shots_on == 2:
        score += 2
    elif shots_on == 1:
        score += 1

    # Corners
    if corners >= 3:
        score += 2
    elif corners == 2:
        score += 1

    # Balbezit
    if possession >= 70:
        score += 2
    elif possession >= 65:
        score += 1

    return score


while True:
    try:
        matches = get_live_matches()

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

            # Favoriet logica (simpel: laagste pre-match odd)
            odds = match.get("odds", {})
            favorite_behind = False  # fallback (veilig)

            # Rode kaart check
            red_card_recent = False
            for event in match.get("events", []):
                if event["type"] == "Card" and event["detail"] == "Red Card":
                    if minute - event["time"]["elapsed"] <= 5:
                        red_card_recent = True

            stats = match.get("statistics")
            if not stats or len(stats) < 2:
                continue

            home_stats = stats[0]["statistics"]

            possession = get_stat(home_stats, "Ball Possession")
            shots_on = get_stat(home_stats, "Shots on Goal")
            corners = get_stat(home_stats, "Corner Kicks")

            score = pressure_score(
                minute=minute,
                is_draw=is_draw,
                favorite_behind=favorite_behind,
                shots_on=shots_on,
                corners=corners,
                possession=possession,
                red_card_recent=red_card_recent
            )

            if score >= 1:
                send_message(
                    f"⚠️ NEXT GOAL ALERT\n\n"
                    f"{home} vs {away}\n"
                    f"Minuut: {minute}\n\n"
                    f"Pressure score: {score}\n"
                    f"• Schoten op doel: {shots_on}\n"
                    f"• Corners: {corners}\n"
                    f"• Balbezit: {possession}%"
                )

                ALERTED_FIXTURES.add(fixture_id)

        time.sleep(30)

    except Exception as e:
        print("Error:", e)
        time.sleep(30)
