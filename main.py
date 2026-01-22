import time
import os
import requests
import csv
from datetime import date, datetime

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
# PREMIUM SETTINGS
# =========================
TODAY = date.today()
DAILY_ALERTS = 0
DAILY_MAX_ALERTS = 9999

MIN_MINUTE = 18
MAX_MINUTE = 88

# ✅ Alerts (premium strenger)
MIN_SCORE = 13
EXTREME_SCORE = 20

# ✅ Nieuw: Dominantie GAP filter
# (hoe hoger, hoe minder alerts maar betere kwaliteit)
MIN_GAP = 18.0

# ✅ Nieuw: Tegenstander mag niet te veel threat hebben
MAX_OPP_SOT = 1          # tegenstander max 1 SOT in de helft
MAX_OPP_SHOTS = 6        # of max 6 shots in de helft

# ✅ Weights (SOT super belangrijk)
W_SOT = 6
W_SHOTS = 1
W_CORNERS = 1
W_POSSESSION = 0.07
W_BIGCHANCES = 3
RED_CARD_BONUS = 6

# Kwaliteit filter dominant team
MIN_DOMINANT_SOT = 2
MIN_DOMINANT_SHOTS = 6

# Score regels
MAX_BEHIND_GOALS = 2
GOAL_COOLDOWN_SECONDS = 90

# Odds settings
REQUIRE_ODDS = False  # altijd sturen, odds is nice-to-have

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
# State
# =========================
ALERT_STATE = {}         # fid -> {"normal": bool, "extreme": bool}
HALF_TIME_SNAPSHOT = {}  # fid -> snapshot dict
SCORE_STATE = {}         # fid -> {"score": (gh,ga), "changed_at": epoch}

LOG_FILE = "alerts_log.csv"


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
# ODDS
# =========================
def get_live_odds(fixture_id):
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
# CONFIDENCE SCORE
# =========================
def confidence_score(dominant_score, gap, sot_diff, red_adv, big_diff):
    """
    Output: 0-100
    """
    score = 0
    score += max(0, (dominant_score - MIN_SCORE) * 6)   # basis vanuit dominantie
    score += min(30, gap * 1.0)                         # gap = mega belangrijk
    score += min(25, sot_diff * 10)                     # SOT verschil
    score += min(10, red_adv * 10)                      # rood voordeel
    score += min(10, big_diff * 6)                      # big chances diff

    if score > 100:
        score = 100
    if score < 0:
        score = 0
    return int(score)


# =========================
# LOGGING
# =========================
def ensure_log_header():
    try:
        with open(LOG_FILE, "r", newline="", encoding="utf-8") as f:
            return
    except FileNotFoundError:
        with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "timestamp", "fixture_id", "league", "home", "away",
                "minute", "score", "pick", "dominant_score", "gap", "confidence", "odds"
            ])

def log_alert(fid, league, home, away, minute, gh, ga, pick, dom_score, gap, conf, odds):
    ensure_log_header()
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            datetime.now().isoformat(timespec="seconds"),
            fid,
            league,
            home,
            away,
            minute,
            f"{gh}-{ga}",
            pick,
            round(dom_score, 2),
            round(gap, 2),
            conf,
            odds if odds is not None else ""
        ])


# =========================
# START
# =========================
send_message("🟢 Premium Bot gestart – GAP + CONFIDENCE + LOGGING ✅")


# =========================
# MAIN LOOP
# =========================
while True:
    try:
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
            if status_short in ("FT", "AET", "PEN", "CANC", "PST", "ABD", "AWD", "WO"):
                cleanup_finished(fid)
                continue

            home = match.get("teams", {}).get("home", {}).get("name", "HOME")
            away = match.get("teams", {}).get("away", {}).get("name", "AWAY")

            league = match.get("league", {})
            league_name = league.get("name", "Unknown League")
            league_country = league.get("country", "")

            if is_excluded_match(league_name, home, away):
                continue

            minute = fixture.get("status", {}).get("elapsed")
            if not minute or minute < MIN_MINUTE or minute > MAX_MINUTE:
                continue

            goals = match.get("goals", {})
            gh = goals.get("home", 0)
            ga = goals.get("away", 0)

            if abs(gh - ga) > MAX_BEHIND_GOALS:
                continue

            # cooldown na goal
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

            if fid not in ALERT_STATE:
                ALERT_STATE[fid] = {"normal": False, "extreme": False}

            stats_response = get_match_statistics(fid)
            if not stats_response or len(stats_response) != 2:
                continue

            home_stats = stats_response[0].get("statistics", [])
            away_stats = stats_response[1].get("statistics", [])

            # totals
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

            # halftime snapshot
            if (status_short == "HT" or minute >= 45) and fid not in HALF_TIME_SNAPSHOT:
                HALF_TIME_SNAPSHOT[fid] = {
                    "home": {"sot": hsot_total, "shots": hshots_total, "corn": hcorn_total, "big": hbig_total},
                    "away": {"sot": asot_total, "shots": ashots_total, "corn": acorn_total, "big": abig_total},
                }

            # per half
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

            # red card bonus
            red_adv_home = ared_total - hred_total  # >0 = home man meer
            red_adv_away = hred_total - ared_total  # >0 = away man meer
            red_bonus_home = max(0, red_adv_home) * RED_CARD_BONUS
            red_bonus_away = max(0, red_adv_away) * RED_CARD_BONUS

            # dominance score
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

            gap = abs(score_home - score_away)

            # ✅ PREMIUM: skip als gap niet groot genoeg is
            if gap < MIN_GAP:
                continue

            # dominant side
            if score_home > score_away:
                dominant_side = "HOME"
                dominant_score = score_home
                dominant_sot = hsot
                dominant_shots = hshots
                opp_sot = asot
                opp_shots = ashots
                sot_diff = hsot - asot
                red_adv = max(0, red_adv_home)
                big_diff = hbig - abig
            else:
                dominant_side = "AWAY"
                dominant_score = score_away
                dominant_sot = asot
                dominant_shots = ashots
                opp_sot = hsot
                opp_shots = hshots
                sot_diff = asot - hsot
                red_adv = max(0, red_adv_away)
                big_diff = abig - hbig

            # ✅ jouw regels: NOOIT alert als dominant team voor staat
            if dominant_side == "HOME" and gh > ga:
                continue
            if dominant_side == "AWAY" and ga > gh:
                continue

            # comeback max 2 goals
            if dominant_side == "HOME" and (ga - gh) > MAX_BEHIND_GOALS:
                continue
            if dominant_side == "AWAY" and (gh - ga) > MAX_BEHIND_GOALS:
                continue

            # dominant team moet dreiging hebben
            if dominant_sot < MIN_DOMINANT_SOT and dominant_shots < MIN_DOMINANT_SHOTS:
                continue

            # ✅ opponent threat filter (tegenstander mag niet ook gevaarlijk zijn)
            if opp_sot > MAX_OPP_SOT and opp_shots > MAX_OPP_SHOTS:
                continue

            # threshold
            if dominant_score < MIN_SCORE:
                continue

            # odds
            picked_odds = None
            odds_response = get_live_odds(fid)
            picked_odds = find_next_goal_odds(odds_response, dominant_side, home, away)

            if picked_odds is None and REQUIRE_ODDS:
                continue

            odds_line = (
                f"\n💰 Next Goal Odds: {picked_odds} ✅"
                if picked_odds is not None
                else "\n💰 Next Goal Odds: — (SLOT: check BetCity) 🟡"
            )

            predicted = home if dominant_side == "HOME" else away

            # confidence
            conf = confidence_score(
                dominant_score=dominant_score,
                gap=gap,
                sot_diff=sot_diff,
                red_adv=red_adv,
                big_diff=big_diff
            )

            red_txt = ""
            if hred_total or ared_total:
                red_txt = f"\n🟥 Red Cards: {hred_total} - {ared_total}"

            # EXTREME alert
            if dominant_score >= EXTREME_SCORE and not ALERT_STATE[fid]["extreme"]:
                send_message(
                    f"🔥🔥 EXTREME NEXT GOAL ALERT ({half_text})\n\n"
                    f"🏆 {league_name} ({league_country})\n"
                    f"{home} vs {away}\n"
                    f"Minuut: {minute}' | Stand: {gh}-{ga}\n\n"
                    f"✅ Confidence: {conf}/100\n"
                    f"📏 GAP: {round(gap,1)}\n\n"
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

                log_alert(
                    fid=fid,
                    league=f"{league_name} ({league_country})",
                    home=home, away=away,
                    minute=minute,
                    gh=gh, ga=ga,
                    pick=predicted,
                    dom_score=dominant_score,
                    gap=gap,
                    conf=conf,
                    odds=picked_odds
                )

                ALERT_STATE[fid]["extreme"] = True
                DAILY_ALERTS += 1
                break

            # NORMAL alert
            if dominant_score >= MIN_SCORE and not ALERT_STATE[fid]["normal"]:
                send_message(
                    f"⚠️ NEXT GOAL ALERT ({half_text})\n\n"
                    f"🏆 {league_name} ({league_country})\n"
                    f"{home} vs {away}\n"
                    f"Minuut: {minute}' | Stand: {gh}-{ga}\n\n"
                    f"✅ Confidence: {conf}/100\n"
                    f"📏 GAP: {round(gap,1)}\n\n"
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

                log_alert(
                    fid=fid,
                    league=f"{league_name} ({league_country})",
                    home=home, away=away,
                    minute=minute,
                    gh=gh, ga=ga,
                    pick=predicted,
                    dom_score=dominant_score,
                    gap=gap,
                    conf=conf,
                    odds=picked_odds
                )

                ALERT_STATE[fid]["normal"] = True
                DAILY_ALERTS += 1
                break

        time.sleep(90)

    except Exception as e:
        send_message(f"❌ ERROR: {e}")
        time.sleep(60)

