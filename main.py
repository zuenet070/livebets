import time
import os
import requests
from datetime import date

# =========================
# ENV VARS
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEY = os.getenv("API_FOOTBALL_KEY")

if not BOT_TOKEN or not CHAT_ID or not API_KEY:
    print("❌ ERROR: Missing env vars. Check BOT_TOKEN, CHAT_ID, API_FOOTBALL_KEY")
    exit()

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

# =========================
# SETTINGS
# =========================
TODAY = date.today()
DAILY_ALERTS = 0
DAILY_MAX_ALERTS = 9999  # praktisch onbeperkt

MIN_MINUTE = 15
MAX_MINUTE = 88

# Alert thresholds
MIN_SCORE = 10          # normale alert
EXTREME_SCORE = 18      # extreme alert

# Weights (Shots on Target telt zwaar)
W_SOT = 3
W_SHOTS = 1
W_CORNERS = 1
W_POSSESSION = 0.07
W_BIGCHANCES = 3
RED_CARD_BONUS = 4

# Kwaliteit filters
MIN_DOMINANT_SOT = 1
MIN_DOMINANT_SHOTS = 5

# Score regels
MAX_BEHIND_GOALS = 2         # dominant team mag max 2 goals achter staan
GOAL_COOLDOWN_SECONDS = 90   # na goal even geen alerts

# ODDS settings
# ✅ Altijd sturen, ook zonder odds
REQUIRE_ODDS = False

# =========================
# Blacklist rommel
# =========================
EXCLUDE_KEYWORDS = [
    "U21", "U20", "U19", "U18", "U17", "U16",
    "Youth", "Junior",
    "Reserves", "Reserve", "B Team", "B-team", "II",
    "Women", "Womens", "Fem", "Dames",
    "Futsal",
    "Esports", "E-sports", "Virtual",
]

# =========================
# State / memory
# =========================
ALERT_STATE = {}         # fid -> {"normal": bool, "extreme": bool}
HALF_TIME_SNAPSHOT = {}  # fid -> snapshot dict
SCORE_STATE = {}         # fid -> {"score": (gh,ga), "changed_at": epoch}

# =========================
# HELPERS
# =========================
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

def get_big_chances(team_stats_list):
    for key in ["Big Chances", "Big chances", "Big Chances Created", "Big chances created"]:
        v = stat(team_stats_list, key)
        if v != 0:
            return v
    return 0

def clamp_nonnegative(x):
    return x if x > 0 else 0

def is_excluded_match(league_name, home_name, away_name):
    text = f"{league_name} {home_name} {away_name}".lower()
    for kw in EXCLUDE_KEYWORDS:
        if kw.lower() in text:
            return True
    return False

def cleanup_finished(fid):
    ALERT_STATE.pop(fid, None)
    HALF_TIME_SNAPSHOT.pop(fid, None)
    SCORE_STATE.pop(fid, None)

# =========================
# ODDS (API-Football)
# =========================
def get_live_odds(fixture_id):
    """
    Probeert odds op te halen.
    Welke endpoint werkt hangt af van jouw plan.
    We proberen meerdere varianten zonder crashen.
    """
    endpoints = [
        ("/odds/live", {"fixture": fixture_id}),
        ("/odds", {"fixture": fixture_id, "live": "all"}),
        ("/odds", {"fixture": fixture_id}),
    ]

    for path, params in endpoints:
        try:
            data = api_get(path, params=params)
            resp = data.get("response", [])
            if resp:
                return resp
        except:
            continue

    return None

def find_next_goal_odds(odds_response, predicted_side, home_name, away_name):
    """
    Zoekt Next Goal market en pakt odds voor HOME/AWAY.
    predicted_side = "HOME" of "AWAY"
    """
    if not odds_response:
        return None

    market_keywords = [
        "next goal",
        "team to score next",
        "next team to score",
        "next goal scorer (team)",
    ]

    if predicted_side == "HOME":
        want_values = ["home", home_name.lower(), "1"]
    else:
        want_values = ["away", away_name.lower(), "2"]

    for item in odds_response:
        bookmakers = item.get("bookmakers", [])
        for book in bookmakers:
            bets = book.get("bets", [])
            for bet in bets:
                bet_name = (bet.get("name") or "").lower()
                if not any(k in bet_name for k in market_keywords):
                    continue

                values = bet.get("values", [])
                for v in values:
                    v_name = (v.get("value") or "").lower()
                    if any(w in v_name for w in want_values):
                        try:
                            return float(v.get("odd"))
                        except:
                            return None

    return None

# =========================
# START
# =========================
send_message("🟢 Bot gestart – ALL LEAGUES + BLACKLIST + ODDS SLOT + EXTREME ✅")

# =========================
# MAIN LOOP
# =========================
while True:
    try:
        # daily reset
        if date.today() != TODAY:
            TODAY = date.today()
            DAILY_ALERTS = 0
            ALERT_STATE.clear()
            HALF_TIME_SNAPSHOT.clear()
            SCORE_STATE.clear()
            send_message("🔄 Nieuwe dag — daily alerts gereset")

        if DAILY_ALERTS >= DAILY_MAX_ALERTS:
            time.sleep(300)
            continue

        matches = get_live_matches()

        for match in matches:
            fixture = match.get("fixture", {})
            fid = fixture.get("id")
            if not fid:
                continue

            status_short = fixture.get("status", {}).get("short", "")

            # finished / cancelled -> cleanup
            if status_short in ("FT", "AET", "PEN", "CANC", "PST", "ABD", "AWD", "WO"):
                cleanup_finished(fid)
                continue

            home = match.get("teams", {}).get("home", {}).get("name", "HOME")
            away = match.get("teams", {}).get("away", {}).get("name", "AWAY")

            league = match.get("league", {})
            league_name = league.get("name", "Unknown League")
            league_country = league.get("country", "")

            # blacklist filter
            if is_excluded_match(league_name, home, away):
                continue

            minute = fixture.get("status", {}).get("elapsed")
            if not minute or minute < MIN_MINUTE or minute > MAX_MINUTE:
                continue

            goals = match.get("goals", {})
            gh = goals.get("home", 0)
            ga = goals.get("away", 0)

            # te extreme scoreline skip
            if abs(gh - ga) > MAX_BEHIND_GOALS:
                continue

            # cooldown na score change
            now = time.time()
            current_score = (gh, ga)

            if fid not in SCORE_STATE:
                SCORE_STATE[fid] = {"score": current_score, "changed_at": now}
            else:
                old_score = SCORE_STATE[fid]["score"]
                if current_score != old_score:
                    SCORE_STATE[fid] = {"score": current_score, "changed_at": now}

            if now - SCORE_STATE[fid]["changed_at"] < GOAL_COOLDOWN_SECONDS:
                continue

            # init alert state per match
            if fid not in ALERT_STATE:
                ALERT_STATE[fid] = {"normal": False, "extreme": False}

            # stats ophalen
            stats_response = get_match_statistics(fid)
            if not stats_response or len(stats_response) != 2:
                continue

            home_stats = stats_response[0].get("statistics", [])
            away_stats = stats_response[1].get("statistics", [])

            # cumulatieve stats
            hsot_total = stat(home_stats, "Shots on Goal")
            asot_total = stat(away_stats, "Shots on Goal")

            hshots_total = stat(home_stats, "Total Shots")
            ashots_total = stat(away_stats, "Total Shots")

            hcorn_total = stat(home_stats, "Corner Kicks")
            acorn_total = stat(away_stats, "Corner Kicks")

            hpos_total = stat(home_stats, "Ball Possession")
            apos_total = stat(away_stats, "Ball Possession")

            hbig_total = get_big_chances(home_stats)
            abig_total = get_big_chances(away_stats)

            hred_total = stat(home_stats, "Red Cards")
            ared_total = stat(away_stats, "Red Cards")

            # halftime snapshot opslaan
            if (status_short == "HT" or minute >= 45) and fid not in HALF_TIME_SNAPSHOT:
                HALF_TIME_SNAPSHOT[fid] = {
                    "home": {"sot": hsot_total, "shots": hshots_total, "corn": hcorn_total, "big": hbig_total},
                    "away": {"sot": asot_total, "shots": ashots_total, "corn": acorn_total, "big": abig_total},
                }

            # per helft stats
            in_second_half = minute > 45
            use_half_stats = in_second_half and fid in HALF_TIME_SNAPSHOT

            if use_half_stats:
                snap = HALF_TIME_SNAPSHOT[fid]
                hsot = clamp_nonnegative(hsot_total - snap["home"]["sot"])
                asot = clamp_nonnegative(asot_total - snap["away"]["sot"])

                hshots = clamp_nonnegative(hshots_total - snap["home"]["shots"])
                ashots = clamp_nonnegative(ashots_total - snap["away"]["shots"])

                hcorn = clamp_nonnegative(hcorn_total - snap["home"]["corn"])
                acorn = clamp_nonnegative(acorn_total - snap["away"]["corn"])

                hbig = clamp_nonnegative(hbig_total - snap["home"]["big"])
                abig = clamp_nonnegative(abig_total - snap["away"]["big"])
                half_text = "2e helft"
            else:
                hsot, asot = hsot_total, asot_total
                hshots, ashots = hshots_total, ashots_total
                hcorn, acorn = hcorn_total, acorn_total
                hbig, abig = hbig_total, abig_total
                half_text = "1e helft" if minute <= 45 else "totaal"

            # rode kaart bonus
            red_adv_home = ared_total - hred_total
            red_adv_away = hred_total - ared_total
            red_bonus_home = max(0, red_adv_home) * RED_CARD_BONUS
            red_bonus_away = max(0, red_adv_away) * RED_CARD_BONUS

            # dominantie score
            score_home = (
                (hsot - asot) * W_SOT +
                (hshots - ashots) * W_SHOTS +
                (hcorn - acorn) * W_CORNERS +
                (hbig - abig) * W_BIGCHANCES +
                ((hpos_total - 50) * W_POSSESSION) +
                red_bonus_home
            )

            score_away = (
                (asot - hsot) * W_SOT +
                (ashots - hshots) * W_SHOTS +
                (acorn - hcorn) * W_CORNERS +
                (abig - hbig) * W_BIGCHANCES +
                ((apos_total - 50) * W_POSSESSION) +
                red_bonus_away
            )

            # dominant side
            if score_home > score_away:
                dominant_side = "HOME"
                dominant_score = score_home
                dominant_sot = hsot
                dominant_shots = hshots
            else:
                dominant_side = "AWAY"
                dominant_score = score_away
                dominant_sot = asot
                dominant_shots = ashots

            # ✅ jouw regels:
            # nooit alert als dominant team VOOR staat
            if dominant_side == "HOME" and gh > ga:
                continue
            if dominant_side == "AWAY" and ga > gh:
                continue

            # comeback max 2 goals
            if dominant_side == "HOME" and (ga - gh) > MAX_BEHIND_GOALS:
                continue
            if dominant_side == "AWAY" and (gh - ga) > MAX_BEHIND_GOALS:
                continue

            # kwaliteit check
            if dominant_sot < MIN_DOMINANT_SOT and dominant_shots < MIN_DOMINANT_SHOTS:
                continue

            # =========================
            # ODDS CHECK (nice-to-have)
            # =========================
            picked_odds = None
            odds_response = get_live_odds(fid)
            picked_odds = find_next_goal_odds(odds_response, dominant_side, home, away)

            # ✅ NOOIT skippen door odds
            if picked_odds is None and REQUIRE_ODDS:
                continue

            # SLOT als odds ontbreken
            odds_line = (
                f"\n💰 Next Goal Odds: {picked_odds} ✅"
                if picked_odds is not None
                else "\n💰 Next Goal Odds: — (SLOT: check BetCity) 🟡"
            )

            predicted = home if dominant_side == "HOME" else away

            # rode kaart tekst
            red_txt = ""
            if hred_total or ared_total:
                red_txt = f"\n🟥 Red Cards: {hred_total} - {ared_total}"

            # =========================
            # EXTREME ALERT
            # =========================
            if dominant_score >= EXTREME_SCORE and not ALERT_STATE[fid]["extreme"]:
                send_message(
                    f"🔥🔥 EXTREME NEXT GOAL ALERT ({half_text})\n\n"
                    f"🏆 {league_name} ({league_country})\n"
                    f"{home} vs {away}\n"
                    f"Minuut: {minute}' | Stand: {gh}-{ga}\n\n"
                    f"📊 Stats ({half_text}):\n"
                    f"SOT: {hsot} - {asot}\n"
                    f"Shots: {hshots} - {ashots}\n"
                    f"Corners: {hcorn} - {acorn}\n"
                    f"Big Chances: {hbig} - {abig}\n"
                    f"Possession (totaal): {hpos_total}% - {apos_total}%"
                    f"{red_txt}"
                    f"{odds_line}\n\n"
                    f"🚀 Dominantie score: {round(score_home,1)} - {round(score_away,1)}\n"
                    f"➡️ EXTREME pick: {predicted}"
                )
                ALERT_STATE[fid]["extreme"] = True
                DAILY_ALERTS += 1
                break

            # =========================
            # NORMAL ALERT
            # =========================
            if dominant_score >= MIN_SCORE and not ALERT_STATE[fid]["normal"]:
                send_message(
                    f"⚠️ NEXT GOAL ALERT ({half_text})\n\n"
                    f"🏆 {league_name} ({league_country})\n"
                    f"{home} vs {away}\n"
                    f"Minuut: {minute}' | Stand: {gh}-{ga}\n\n"
                    f"📊 Stats ({half_text}):\n"
                    f"SOT: {hsot} - {asot}\n"
                    f"Shots: {hshots} - {ashots}\n"
                    f"Corners: {hcorn} - {acorn}\n"
                    f"Big Chances: {hbig} - {abig}\n"
                    f"Possession (totaal): {hpos_total}% - {apos_total}%"
                    f"{red_txt}"
                    f"{odds_line}\n\n"
                    f"🔥 Dominantie score: {round(score_home,1)} - {round(score_away,1)}\n"
                    f"➡️ Verwachte volgende goal: {predicted}"
                )
                ALERT_STATE[fid]["normal"] = True
                DAILY_ALERTS += 1
                break

        time.sleep(90)

    except Exception as e:
        send_message(f"❌ ERROR: {e}")
        time.sleep(60)
